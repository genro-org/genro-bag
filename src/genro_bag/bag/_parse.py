# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""BagParser mixin - classmethods for deserializing from various formats.

This module provides the BagParser mixin class containing from_xml, from_tytx,
and from_json classmethods. The Bag class inherits from this mixin to get
deserialization capabilities without circular imports.

All methods are classmethods. When called as Bag.from_json(data), cls is the Bag
class, so cls() creates a new Bag instance.
"""

from __future__ import annotations

import datetime
import io
import re
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal
from xml import sax
from xml.sax import saxutils
from xml.sax.handler import ContentHandler

from genro_toolbox import SignatureError
from genro_tytx import from_tytx as tytx_decode

from genro_bag._resolver_wire import (
    _decode_bag_class,
    _decode_sign_key,
    decode_attrs,
    decode_resolver,
    has_nested_resolver,
)
from genro_bag._subtype_wire import CLS_ATTRIBUTE, get_inherited_class, get_subtype_class
from genro_bag.bag._exceptions import BagSerializationError

if TYPE_CHECKING:
    from genro_bag.bag._core import Bag


# Regex for empty checks
_EMPTY_CONTENT_RE = re.compile(r"^\s*$")


class BagParser:
    """Mixin providing deserialization classmethods for Bag."""

    if TYPE_CHECKING:
        def set_item(self, path: str, value: Any, _attributes: dict[Any, Any] | None = ..., node_position: str | int | None = ..., _updattr: bool = ..., _remove_null_attributes: bool = ..., _reason: str | None = ..., _fired: bool = ..., do_trigger: bool = ..., resolver: Any = ..., node_tag: str | None = ..., **kwargs: Any) -> Any: ...

    # ==================== from_xml ====================

    @classmethod
    def from_xml(
        cls,
        source: str | bytes,
        empty: Callable[[], Any] | None = None,
        raise_on_error: bool = False,
        tag_attribute: str | None = None,
        sign_key: str | None = None,
        *,
        legacy_mode: bool = False,
        catalog: Any = None,
        bag_class: type | None = None,
        attr_in_value: str | bool | None = None,
        avoid_duplicate_label: bool | None = None,
    ) -> Bag:
        """Deserialize from XML format.

        Automatically detects and handles legacy GenRoBag format:
        - Decodes `_T` attribute for value types (L=int, R=float, D=date, etc.)
        - Decodes `::TYPE` suffix in attribute values (TYTX encoding)
        - Handles `<GenRoBag>` root wrapper element (unwraps automatically)

        For plain XML without type markers, values remain as strings.
        Supports environment variable substitution for {GNR_*} placeholders.

        A ``_resolver`` attribute rebuilds the node's own resolver, and any
        attribute holding a ``::RSLV:`` string becomes a resolver again.
        Both are inert: nothing is called until the caller reads the value.

        Args:
            source: XML string or bytes to parse.
            empty: Factory function for empty element values. Called when an
                element has no content and no type marker.
            raise_on_error: If True, raise exceptions for type conversion errors.
                If False (default), invalid values become '**INVALID::TYPE**' markers.
            tag_attribute: If specified, use this attribute's value as the node
                label instead of the XML tag name. Dotted values create nested
                structure (e.g., 'section.elem' becomes section/elem path).
            sign_key: Secret key. When given, every resolver payload must
                carry a valid signature — required for XML of untrusted
                origin, since a resolver's arguments say what it will act on.

        Returns:
            Bag: Reconstructed Bag hierarchy.

        Raises:
            SignatureError: Signature missing, forged or expired, when
                sign_key was given.

        Example:
            >>> # Plain XML - each element becomes a node
            >>> bag = Bag.from_xml('<root><name>test</name></root>')
            >>> bag['root.name']
            'test'

            >>> # Legacy GenRoBag format (auto-detected by root element name)
            >>> bag = Bag.from_xml('<GenRoBag><count _T="L">42</count></GenRoBag>')
            >>> bag['count']  # GenRoBag wrapper is unwrapped automatically
            42
            >>> type(bag['count'])  # _T="L" converts to int
            <class 'int'>

            >>> # Use attribute as path (creates nested structure)
            >>> xml = '<grammar><define name="section.elem"/></grammar>'
            >>> bag = Bag.from_xml(xml, tag_attribute='name')
            >>> 'section' in bag['grammar']  # dot creates hierarchy
            True
        """
        if legacy_mode:
            if sign_key is not None:
                raise ValueError(
                    "Legacy XML does not carry resolver signatures; "
                    "sign_key is supported only by the modern wire"
                )
            from genro_bag._legacy_codec import legacy_from_xml

            return legacy_from_xml(
                bag_class or cls,
                source,
                catalog=catalog,
                empty=empty,
                attr_in_value=attr_in_value,
                avoid_duplicate_label=avoid_duplicate_label,
            )

        handler = _BagXmlHandler(
            cls,
            empty=empty,
            raise_on_error=raise_on_error,
            tag_attribute=tag_attribute,
            sign_key=sign_key,
        )
        if isinstance(source, bytes):
            source = source.decode()

        # Create secure parser (disable DTD and external entities to prevent XXE)
        parser = sax.make_parser()
        parser.setContentHandler(handler)
        parser.setFeature(sax.handler.feature_external_ges, False)
        parser.setFeature(sax.handler.feature_external_pes, False)
        input_source = sax.xmlreader.InputSource()
        input_source.setCharacterStream(io.StringIO(source))
        parser.parse(input_source)

        result = handler.bags[0][0]
        if handler.legacy_mode:
            result = result["GenRoBag"]
        if result is None:
            result = cls()
        return result  # type: ignore[no-any-return]

    # ==================== from_tytx ====================

    @classmethod
    def from_tytx(
        cls, data: str | bytes, transport: Literal["json", "msgpack"] = "json",
        sign_key: str | None = None, *, recursive: bool = True,
    ) -> Bag:
        """Read ordinary JSON or TYTX, restoring a marked Bag or converting data.

        Only dict/list roots and explicitly typed Bags are accepted. A marked
        Bag retains its original structure regardless of recursive. Resolver
        signatures are verified while registered Bag payloads are decoded.
        """
        if not isinstance(data, (str, bytes)):
            raise TypeError("from_tytx requires serialized text or bytes")
        if not isinstance(recursive, bool):
            raise TypeError("recursive must be a bool")
        if transport not in ("json", "msgpack"):
            raise ValueError(f"Unsupported transport: {transport}")
        if transport == "json" and isinstance(data, bytes):
            data = data.decode("utf-8")
        token = _decode_sign_key.set(sign_key)
        class_token = _decode_bag_class.set(cls if cls.__tytx_suffix__ == "X" else None)
        try:
            # Calling a legacy subclass directly retains its class factory.
            suffix = f"::{cls.__tytx_suffix__}"
            if isinstance(data, str) and data.endswith(suffix):
                return cls._from_tytx_payload(data[:-len(suffix)])
            decoded = tytx_decode(data, transport=None if transport == "json" else transport)
        finally:
            _decode_sign_key.reset(token)
            _decode_bag_class.reset(class_token)
        if isinstance(decoded, BagParser):
            return decoded
        if not isinstance(decoded, (dict, list)):
            raise TypeError("from_tytx requires a dict, list or typed Bag root")
        if sign_key is not None and has_nested_resolver(decoded):
            raise SignatureError("a nested Bag carries a resolver outside the signature contract")
        return cls(decoded, recursive=recursive)


    @classmethod
    def _from_tytx_payload(cls, data: str) -> Bag:
        """Registry hook: decode only a payload explicitly identified as a Bag."""
        suffix = cls.__tytx_suffix__
        default_class = cls if cls.__module__.startswith("replacement.") else get_inherited_class(None, suffix)
        if data == "":
            return default_class()
        sign_key = _decode_sign_key.get()
        parsed = tytx_decode(data)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("rows"), list):
            raise BagSerializationError("Invalid TYTX Bag: expected rows")
        rows = parsed["rows"]
        paths_raw = parsed.get("paths")
        code_to_path: dict[int, str | list[str]] | None = (
            {int(k): v for k, v in paths_raw.items()} if paths_raw is not None else None
        )

        root_cls = parsed.get(CLS_ATTRIBUTE)
        root_class = get_subtype_class(suffix, root_cls) if root_cls is not None else default_class
        bag = root_class()
        path_to_bag: dict[tuple, Any] = {(): bag}

        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 5:
                raise BagSerializationError("Invalid TYTX Bag row")
            parent_ref, label, tag, value, attr = row

            # Resolve parent path
            if code_to_path is not None:
                if parent_ref is not None and parent_ref not in code_to_path:
                    raise BagSerializationError(f"Unknown TYTX parent reference: {parent_ref!r}")
                parent_path = code_to_path[parent_ref] if parent_ref is not None else ""
            else:
                parent_path = parent_ref if parent_ref is not None else ""

            if isinstance(parent_path, list):
                parent_path = tuple(parent_path)
            elif isinstance(parent_path, str):
                parent_path = tuple(parent_path.split(".")) if parent_path else ()
            else:
                raise BagSerializationError("Invalid TYTX parent path")
            if parent_path not in path_to_bag:
                raise BagSerializationError(
                    f"Missing or undecodable TYTX parent branch: {parent_path!r}"
                )
            parent_bag = path_to_bag[parent_path]
            full_path = (*parent_path, label)
            attr = decode_attrs(attr, sign_key)

            branch_cls = attr.pop(CLS_ATTRIBUTE, None)
            if hasattr(value, "_nodes") and isinstance(value, BagParser):
                # Rows carry empty branches; descendants are populated below.
                # Legacy X subclasses keep the historical root-class factory.
                child_class = type(value)
                if child_class.__tytx_suffix__ == "X" and cls.__tytx_suffix__ == "X":
                    child_class = cls
                branch_suffix = type(value).__tytx_suffix__
                child_class = (get_subtype_class(branch_suffix, branch_cls) if branch_cls is not None
                               else get_inherited_class(type(parent_bag), branch_suffix))
                child_bag = child_class()
                parent_bag._append_literal(label, child_bag, attr, node_tag=tag)
                path_to_bag[full_path] = child_bag
            elif branch_cls is not None:
                raise BagSerializationError(f"node {full_path!r}: reserved {CLS_ATTRIBUTE!r} on a scalar")
            else:
                resolver = decode_resolver(value, sign_key)
                if resolver is not None:
                    parent_bag._append_literal(label, None, attr, resolver, tag)
                else:
                    # A Bag hydrated inside a plain container came through the
                    # TYTX type registry, whose hooks take no sign_key: a
                    # resolver in there was never signature-checked, so refuse
                    # it — the mirror of the same refusal on the encode side.
                    if sign_key is not None and has_nested_resolver(value):
                        raise SignatureError(
                            f"node {full_path!r}: a Bag nested in a plain "
                            "container value carries a resolver whose "
                            "signature cannot be verified on this path"
                        )
                    parent_bag._append_literal(label, value, attr, node_tag=tag)

            # Set tag if present
            if tag:
                node = parent_bag._nodes[label]
                if node:
                    node.node_tag = tag

        return bag  # type: ignore[return-value]

    # ==================== from_json ====================

    @classmethod
    def from_json(cls, source: str | bytes, *, recursive: bool = True, sign_key=None,
                  legacy_mode: bool = False, catalog=None, list_joiner=None) -> Bag:
        """Deprecated modern reader alias; legacy_mode is an explicit compatibility codec."""
        if legacy_mode:
            if sign_key is not None:
                raise ValueError("Legacy JSON does not carry resolver signatures")
            from genro_bag._legacy_codec import legacy_from_json
            return legacy_from_json(cls, source, list_joiner=list_joiner, catalog=catalog)
        if list_joiner is not None or catalog is not None:
            raise TypeError("list_joiner and catalog are only supported in legacy_mode")
        warnings.warn("Bag.from_json is deprecated; use Bag.from_tytx", DeprecationWarning, stacklevel=2)
        return cls.from_tytx(source, recursive=recursive, sign_key=sign_key)



# =============================================================================
# Internal SAX Handler for XML parsing
# =============================================================================


class _BagXmlHandler(ContentHandler):
    """SAX handler for parsing XML into Bag.

    Uses a stack-based approach where each XML element creates a new Bag
    that gets populated with child elements. When an element closes, it's
    added to its parent Bag.

    Attributes:
        bag_class: The Bag class to instantiate for nested structures.
        empty: Optional factory for empty element values.
        raise_on_error: If True, raise on type conversion errors.
        tag_attribute: If set, use this attribute's value as node label.
        bags: Stack of (bag, attrs, type) tuples during parsing.
        value_list: Accumulator for character data between tags.
        legacy_mode: True if parsing GenRoBag format with _T type markers.
    """

    def __init__(
        self,
        bag_class: type,
        empty: Callable[[], Any] | None = None,
        raise_on_error: bool = False,
        tag_attribute: str | None = None,
        sign_key: str | None = None,
    ):
        super().__init__()
        self.bag_class = bag_class
        self.empty = empty
        self.raise_on_error = raise_on_error
        self.tag_attribute = tag_attribute
        self.sign_key = sign_key

    def startDocument(self) -> None:
        """Initialize parsing state with root Bag on stack."""
        self.bags: list[tuple[Any, dict | None, str | None]] = [(self.bag_class(), None, None)]
        self.value_list: list[str] = []
        self.legacy_mode: bool = False

    def _get_value(self, dtype: str | None = None) -> str:
        """Join accumulated character data, strip newlines, unescape XML entities."""
        if self.value_list:
            if self.value_list[0] == "\n":
                self.value_list[:] = self.value_list[1:]
            if self.value_list and self.value_list[-1] == "\n":
                self.value_list.pop()
        value = "".join(self.value_list)
        if dtype != "BAG":
            value = saxutils.unescape(value)
        return value

    def startElement(self, tag_label: str, attributes: Any) -> None:
        """Push new Bag onto stack, detect legacy format on first element."""
        attrs = {str(k): tytx_decode(saxutils.unescape(v)) for k, v in attributes.items()}
        curr_type: str | None = None

        if len(self.bags) == 1:
            # First element - detect legacy format
            self.legacy_mode = tag_label.lower() == "genrobag"
        else:
            if self.legacy_mode:
                curr_type = attrs.pop("_T", None)
            elif "".join(self.value_list).strip():
                # Plain XML - handle mixed content
                value = self._get_value()
                if value:
                    self.bags[-1][0].set_item("_", value)

        self.bags.append((self.bag_class(), attrs, curr_type))

        self.value_list = []

    def characters(self, s: str) -> None:
        """Accumulate text content between tags."""
        self.value_list.append(s)

    def endElement(self, tag_label: str) -> None:
        """Pop Bag from stack, convert value if typed, add to parent."""
        curr, attrs, curr_type = self.bags.pop()
        value = self._get_value(dtype=curr_type)
        self.value_list = []

        # A typed empty Bag is a container, even though it is falsy.
        # BAG is structural XML metadata, not a scalar TYTX suffix.
        if (
            self.legacy_mode
            and curr_type
            and curr_type.upper() == "BAG"
            and not value.strip()
        ):
            self._set_into_parent(tag_label, curr, attrs or {})
            return

        if self.legacy_mode and value and curr_type and curr_type != "T":
            try:
                value = tytx_decode(f"{value}::{curr_type}")
            except Exception:
                if self.raise_on_error:
                    raise
                value = f"**INVALID::{curr_type}**"

        if value or value == 0 or value == datetime.time(0, 0):
            if curr:
                if isinstance(value, str):
                    value = value.strip()
                if value:
                    curr.set_item("_", value)
            else:
                curr = value

        if not curr and curr != 0 and curr != datetime.time(0, 0):
            if self.empty:
                curr = self.empty()
            elif curr_type and curr_type != "T":
                try:
                    curr = tytx_decode(f"::{curr_type}")
                except Exception:
                    if self.raise_on_error:
                        raise
                    curr = f"**INVALID::{curr_type}**"
            else:
                curr = ""

        self._set_into_parent(tag_label, curr, attrs or {})

    def _set_into_parent(self, tag_label: str, curr: Any, attrs: dict) -> None:
        """Add node to parent Bag, handling label from attrs and duplicates."""
        dest = self.bags[-1][0]

        # Use _tag attribute as label if present, keep original as xml_tag
        original_xml_tag = tag_label
        tag_label = attrs.pop("_tag", tag_label)

        # _resolver carries the node's own resolver, the rest may hold theirs
        resolver = decode_resolver(attrs.pop("_resolver", None), self.sign_key)
        attrs = decode_attrs(attrs, self.sign_key)

        # Use tag_attribute value as label if specified (creates nested structure with dots)
        if self.tag_attribute and self.tag_attribute in attrs:
            tag_label = attrs.pop(self.tag_attribute)

        # XML siblings may repeat or already use a generated suffix.
        # Disambiguate the internal label without changing original_xml_tag.
        requested_label = tag_label
        suffix = 1
        while dest.get_node(tag_label) is not None:
            tag_label = f"{requested_label}_{suffix}"
            suffix += 1

        if resolver is not None:
            node = dest.set_item(tag_label, None, _attributes=attrs or None, resolver=resolver)
        elif attrs:
            node = dest.set_item(tag_label, curr, _attributes=attrs)
        else:
            node = dest.set_item(tag_label, curr)

        # Set xml_tag for XML serialization
        node.xml_tag = original_xml_tag
