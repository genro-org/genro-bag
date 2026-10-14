"""Deprecation wrapper for gnr.core.gnrbag API over genro_bag.

Uses __getattr__-based mixin for camelCase → snake_case fallback with
DeprecationWarning. Methods with identical names but different signatures
use explicit overrides instead.

Usage:
    from replacement.gnrbag_wrapper import Bag, BagNode, BagResolver
"""

import asyncio
import datetime
import json as json_module
import os
import pickle as pickle_module
import re
import urllib.parse
import urllib.request
import warnings
from decimal import Decimal
from typing import Any, cast
from xml.sax import saxutils

import yaml

import genro_bag
from genro_bag._resolver_wire import encode_attrs, encode_resolver
from replacement._legacy_json import LegacyJsonParser
from genro_bag.bagnode import BagNodeContainer, smartsplit
from genro_bag.resolver import BagResolver as _NewBagResolver
from replacement.gnrbagxml import BagFromXml, BagToXml

# ---------------------------------------------------------------------------
# Helper: camelCase → snake_case conversion
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case: getItem → get_item, toXml → to_xml."""
    return _CAMEL_RE.sub("_", name).lower()


_CAMEL_OVERRIDE_MAP: dict[str, str] = {
    "setBackRef": "set_backref",
    "clearBackRef": "clear_backref",
}


# ---------------------------------------------------------------------------
# Mixin: __getattr__ with 3-level fallback
# ---------------------------------------------------------------------------

class _CamelDeprecationMixin:
    """Mixin providing camelCase → snake_case fallback via __getattr__.

    Lookup order for an unknown attribute `name`:
    1. wrp_{name} on the instance → deprecation warning + return
    2. _camel_to_snake(name) on the instance → deprecation warning + return
    3. AttributeError
    """

    def __getattribute__(self, name: str) -> Any:
        # Core now supplies camelCase aliases too. Route them through this
        # adapter first so its legacy semantics and warnings remain effective.
        if not name.startswith("_") and any(char.isupper() for char in name):
            try:
                return type(self).__getattr__(self, name)
            except AttributeError:
                pass
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        # Skip dunder and private attributes
        if name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' has no attribute '{name}'"
            )

        # Level 1: explicit wrp_ adapter
        try:
            method = object.__getattribute__(self, f"wrp_{name}")
            warnings.warn(
                f"{name} is deprecated, use the snake_case equivalent",
                DeprecationWarning,
                stacklevel=2,
            )
            return method
        except AttributeError:
            pass

        # Level 2: automatic camelCase → snake_case conversion
        snake = _CAMEL_OVERRIDE_MAP.get(name) or _camel_to_snake(name)
        if snake != name:
            try:
                method = object.__getattribute__(self, snake)
                warnings.warn(
                    f"{name} is deprecated, use {snake}",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return method
            except AttributeError:
                pass

        # Level 3: give up
        raise AttributeError(
            f"'{type(self).__name__}' has no attribute '{name}'"
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CAMEL_TO_SNAKE_KEYS: dict[str, str] = {
    "cacheTime": "cache_time",
    "readOnly": "read_only",
    "retryPolicy": "retry_policy",
    "asBag": "as_bag",
}

_SNAKE_TO_CAMEL_KEYS: dict[str, str] = {v: k for k, v in _CAMEL_TO_SNAKE_KEYS.items()}

_TYPE_MAP: dict[type, str] = {
    str: "T",
    int: "L",
    float: "R",
    bool: "B",
    datetime.date: "D",
    datetime.datetime: "DH",
    datetime.time: "H",
    datetime.timedelta: "TD",
    Decimal: "N",
    type(None): "NN",
    list: "JS",
    dict: "JS",
    tuple: "JS",
}


def _type_code(value: Any) -> str:
    """Return the _T type code for a value, or empty string if text/unknown."""
    if isinstance(value, bool):
        return "B"
    t = type(value)
    code = _TYPE_MAP.get(t, "")
    if code == "T":
        return ""
    return code


# ---------------------------------------------------------------------------
# Support classes
# ---------------------------------------------------------------------------

class AllowMissingDict(dict):
    """Dict that returns '{key}' for missing keys (template substitution)."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def normalizeItemPath(item_path: Any) -> Any:
    """Normalize item path — port from gnr.core.gnrbag.normalizeItemPath."""
    if isinstance(item_path, (str, list)):
        return item_path
    return str(item_path).replace(".", "_")


class BagException(Exception):
    """Base exception for Bag operations."""


class BagNodeException(BagException):
    """Exception for BagNode operations."""


class BagValidationError(BagException):
    """Raised when BagNode validation fails."""


class BagDeprecatedCall(BagException):
    """Raised for deprecated Bag API calls."""

    def __init__(self, errcode: str, message: str):
        self.errcode = errcode
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# BagResolver
# ---------------------------------------------------------------------------

class BagResolver(_CamelDeprecationMixin, _NewBagResolver):
    """BagResolver wrapper with camelCase deprecation support.

    Override __init__ remaps camelCase kwargs with deprecation warning.
    __init_subclass__ translates classKwargs/classArgs.
    """

    classKwargs = {"cacheTime": 0, "readOnly": True}
    classArgs: list[str] = []

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Translate camelCase class attributes to snake_case for the new API."""
        super().__init_subclass__(**kwargs)
        if "classKwargs" in cls.__dict__:
            translated = {}
            for k, v in cls.__dict__["classKwargs"].items():
                translated[_CAMEL_TO_SNAKE_KEYS.get(k, k)] = v
            cls.class_kwargs = translated
        if "classArgs" in cls.__dict__:
            cls.class_args = list(cls.__dict__["classArgs"])

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        translated: dict[str, Any] = {}
        for k, v in kwargs.items():
            snake = _CAMEL_TO_SNAKE_KEYS.get(k)
            if snake:
                warnings.warn(
                    f"{k} is deprecated, use {snake}",
                    DeprecationWarning,
                    stacklevel=2,
                )
                translated[snake] = v
            else:
                translated[k] = v
        super().__init__(*args, **translated)

    # --- wrp_ property aliases (intercepted by __getattr__) ---

    @property
    def wrp_parentNode(self) -> Any:
        """Alias for parent_node."""
        return self.parent_node

    @wrp_parentNode.setter
    def wrp_parentNode(self, value: Any) -> None:
        self.parent_node = value

    @property
    def wrp_cacheTime(self) -> Any:
        """Alias for cache_time."""
        return self._kw.get("cache_time", 0)

    @wrp_cacheTime.setter
    def wrp_cacheTime(self, value: Any) -> None:
        self._kw["cache_time"] = value

    @property
    def wrp_readOnly(self) -> Any:
        """Alias for read_only."""
        return self.read_only

    @wrp_readOnly.setter
    def wrp_readOnly(self, value: Any) -> None:
        self._kw["read_only"] = value
        self._init_kwargs["read_only"] = value

    @property
    def wrp_instanceKwargs(self) -> dict[str, Any]:
        """Return dict of current parameter values with camelCase keys."""
        result: dict[str, Any] = {}
        for par in self.class_args:
            result[par] = self._kw.get(par)
        for par in self.class_kwargs:
            camel_key = _SNAKE_TO_CAMEL_KEYS.get(par, par)
            result[camel_key] = self._kw.get(par)
        return result

    def wrp_resolverSerialize(self, args: Any = None, kwargs: Any = None) -> dict[str, Any]:
        """Serialize to dict with original format keys."""
        data = self.serialize()
        result = {
            "resolverclass": data["resolver_class"],
            "resolvermodule": data["resolver_module"],
            "args": data.get("args", []),
            "kwargs": dict(data.get("kwargs", {})),
        }
        result["kwargs"]["cacheTime"] = self.wrp_cacheTime
        return result


# ---------------------------------------------------------------------------
# BagCbResolver
# ---------------------------------------------------------------------------

class BagCbResolver(BagResolver):
    """BagCbResolver wrapper accepting 'method' as first arg (original naming)."""

    classKwargs = {"cacheTime": 0, "readOnly": False}
    classArgs = ["method"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if "method" in self._kw and "callback" not in self._kw:
            self._kw["callback"] = self._kw.pop("method")

    @property
    def is_async(self) -> bool:
        """Detect if the callback is a coroutine function."""
        cb = self._kw.get("callback")
        if cb is None:
            return False
        return asyncio.iscoroutinefunction(cb)

    def load(self) -> Any:
        """Call sync callback with non-internal parameters."""
        cb = self._kw["callback"]
        params = {k: v for k, v in self._kw.items()
                  if k not in self.internal_params and k != "callback"}
        return cb(**params)

    async def async_load(self) -> Any:
        """Call async callback with non-internal parameters."""
        cb = self._kw["callback"]
        params = {k: v for k, v in self._kw.items()
                  if k not in self.internal_params and k != "callback"}
        return await cb(**params)


# ---------------------------------------------------------------------------
# BagNode
# ---------------------------------------------------------------------------

class BagNode(_CamelDeprecationMixin, genro_bag.BagNode):
    """BagNode wrapper with camelCase deprecation support.

    Supports duplicate labels via _display_label slot.
    Override subscribe/unsubscribe for subscriberId deprecation.
    """

    __slots__ = ("_display_label", "_tail_list")

    def __init__(self, *args: Any, _attributes: Any = None, validators: Any = None, **kwargs: Any) -> None:
        self._display_label: str | None = None
        self._tail_list: list[str] = []
        if _attributes and "attr" not in kwargs:
            kwargs["attr"] = _attributes
        if validators:
            warnings.warn(
                "BagNode validators are not supported in genro_bag. "
                "Use _invalid_reasons for validation.",
                DeprecationWarning,
                stacklevel=2,
            )
        super().__init__(*args, **kwargs)

    @property
    def _dict_key(self) -> str:
        """Return the dict key (suffixed for duplicates, or label)."""
        dk = self._display_label
        return dk if dk is not None else self.label

    @property
    def tag(self) -> str:
        """Return tag (from attr or label), matching original BagNode.tag."""
        return self.attr.get("tag") or self.label

    def _get_fullpath(self) -> str | None:
        """Alias for fullpath."""
        return self.fullpath

    # --- Override: subscribe/unsubscribe (subscriberId deprecation) ---

    def subscribe(self, subscriber_id: Any = None, subscriberId: Any = None,
                  callback: Any = None) -> None:
        """Subscribe to node changes. Accepts both subscriberId and subscriber_id."""
        if subscriberId is not None:
            warnings.warn(
                "subscriberId is deprecated, use subscriber_id",
                DeprecationWarning,
                stacklevel=2,
            )
            subscriber_id = subscriberId
        super().subscribe(subscriber_id=subscriber_id, callback=callback)

    def unsubscribe(self, subscriber_id: Any = None, subscriberId: Any = None) -> None:
        """Unsubscribe from node changes. Accepts both naming conventions."""
        if subscriberId is not None:
            warnings.warn(
                "subscriberId is deprecated, use subscriber_id",
                DeprecationWarning,
                stacklevel=2,
            )
            subscriber_id = subscriberId
        super().unsubscribe(subscriber_id=subscriber_id)

    # --- wrp_* methods (intercepted by __getattr__) ---

    def wrp_getValue(self, mode: str = "") -> Any:
        """Return node value. Maps mode='static' → static=True."""
        static = "static" in mode if mode else False
        return self.get_value(static=static)

    def wrp_setValue(self, value: Any, trigger: bool = True, _attributes: Any = None,
                     _updattr: Any = None, _removeNullAttributes: bool = True,
                     _reason: Any = None) -> None:
        """Set node value with original parameter names."""
        self.set_value(
            value, trigger=trigger, _attributes=_attributes,
            _updattr=_updattr, _remove_null_attributes=_removeNullAttributes,
            _reason=_reason,
        )

    def wrp_getStaticValue(self) -> Any:
        """Get node's value in static mode."""
        return self.static_value

    def wrp_setStaticValue(self, value: Any) -> None:
        """Set node's value directly, bypassing processing and triggers."""
        self.static_value = value

    def wrp_getLabel(self) -> str:
        """Return the node's label."""
        return self.label

    def wrp_setLabel(self, label: str) -> None:
        """Set node's label."""
        self.label = label

    def wrp_getAttr(self, label: Any = None, default: Any = None) -> Any:
        """Get attribute by name, or all attributes if label is None."""
        return self.get_attr(label=label, default=default)

    def wrp_setAttr(self, attr: Any = None, trigger: bool = True,
                    _updattr: bool = True, _removeNullAttributes: bool = True,
                    **kwargs: Any) -> None:
        """Set attributes via dict or kwargs."""
        self.set_attr(
            attr=attr, trigger=trigger, _updattr=_updattr,
            _remove_null_attributes=_removeNullAttributes, **kwargs,
        )

    def wrp_delAttr(self, *attrToDelete: str) -> None:
        """Remove attributes by name(s)."""
        self.del_attr(*attrToDelete)

    @property
    def wrp_parentbag(self) -> Any:
        """Alias for parent_bag."""
        return self.parent_bag

    @wrp_parentbag.setter
    def wrp_parentbag(self, value: Any) -> None:
        self.parent_bag = value

    @property
    def wrp_parentNode(self) -> Any:
        """Alias for parent_node."""
        return self.parent_node

    def wrp_getFormattedValue(self, joiner: Any = None, omitEmpty: bool = True,
                              mode: str = "", **kwargs: Any) -> str:
        """Return formatted display value with caption prefix."""
        static = "static" in mode if mode else False
        v = self.get_value(static=static)
        if isinstance(v, genro_bag.Bag):
            if hasattr(v, "wrp_getFormattedValue"):
                v = v.wrp_getFormattedValue(joiner=joiner, omitEmpty=omitEmpty, mode=mode, **kwargs)
            else:
                v = str(v)
        else:
            attr = self.attr or {}
            v = attr.get("_formattedValue") or attr.get("_displayedValue") or v
        if v or not omitEmpty:
            attr = self.attr or {}
            caption = attr.get("_valuelabel") or attr.get("name_long") or self.label.capitalize()
            return f"{caption}: {v}"
        return ""

    # --- Stub deprecati ---

    def wrp_addValidator(self, validator: Any, parameterString: Any) -> None:
        """Stub: validators are not supported in genro_bag."""
        warnings.warn(
            "addValidator() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    def wrp_removeValidator(self, validator: Any) -> None:
        """Stub: validators are not supported in genro_bag."""
        warnings.warn(
            "removeValidator() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    def wrp_getValidatorData(self, validator: Any, label: Any = None, dflt: Any = None) -> Any:
        """Stub: validators are not supported in genro_bag. Returns dflt."""
        warnings.warn(
            "getValidatorData() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )
        return dflt


# ---------------------------------------------------------------------------
# WrapperBagNodeContainer
# ---------------------------------------------------------------------------

class WrapperBagNodeContainer(BagNodeContainer):
    """BagNodeContainer with duplicate label support."""

    def _node_dict_key(self, node: Any) -> str:
        """Return the dict key for a node (suffixed key for duplicates, or label)."""
        dk = getattr(node, "_display_label", None)
        return dk if dk is not None else node.label

    def __delitem__(self, key: str | int) -> None:
        """Delete item using _dict_key for correct dict removal."""
        if isinstance(key, int):
            idx_to_delete = [key]
        else:
            idx_to_delete = [self.index(block) for block in smartsplit(key, ",")]

        for idx in sorted(idx_to_delete, reverse=True):
            if 0 <= idx < len(self._list):
                v = self._list.pop(idx)
                self._dict.pop(self._node_dict_key(v), None)

    def pop(self, key: str | int) -> Any:
        """Remove and return item using _dict_key for correct dict removal."""
        idx = self.index(key) if isinstance(key, str) else key
        if 0 <= idx < len(self._list):
            node = self._list.pop(idx)
            self._dict.pop(self._node_dict_key(node), None)
            return node
        return None

    def add_duplicate(self, label: str, node: Any, position: str = ">") -> Any:
        """Add a node with a duplicate label."""
        if label in self._dict:
            dup_n = 1
            while f"{label}__dup_{dup_n}" in self._dict:
                dup_n += 1
            dict_key = f"{label}__dup_{dup_n}"
            node.label = label
            node._display_label = dict_key
        else:
            dict_key = label

        idx = self._parse_position(position)
        self._dict[dict_key] = node
        self._list.insert(idx, node)
        return node


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _as_dict_deeply(bag: Any, ascii: bool = False, lower: bool = False) -> dict[str, Any]:
    """Recursively convert any genro_bag.Bag to nested dict."""
    d = bag.as_dict(ascii=ascii, lower=lower)
    for k, v in list(d.items()):
        if isinstance(v, genro_bag.Bag):
            d[k] = _as_dict_deeply(v, ascii=ascii, lower=lower)
    return d


# ---------------------------------------------------------------------------
# Bag
# ---------------------------------------------------------------------------

class Bag(_CamelDeprecationMixin, LegacyJsonParser, genro_bag.Bag):
    """Bag wrapper with camelCase deprecation support.

    Uses __getattr__ mixin for camelCase → snake_case fallback.
    Explicit overrides for methods with same name but different signature.
    wrp_* methods for camelCase methods needing parameter adaptation.
    """

    _node_class = BagNode
    _container_class = WrapperBagNodeContainer

    def __init__(self, source: Any = None, **kwargs: Any) -> None:
        """Create a new Bag from various source types."""
        self._template_kwargs = kwargs.pop("_template_kwargs", {})
        super().__init__()

        if source is None and kwargs:
            source = kwargs
        if source is None:
            return

        if isinstance(source, (bytes, str)):
            self._fill_from_string_source(source)
        elif isinstance(source, genro_bag.Bag):
            for node in source:
                t = node.as_tuple()
                self.set_item(t[0], t[1], _attributes=dict(t[2]) if t[2] else None)
                if t[3]:
                    target = self.get_node(t[0])
                    if target:
                        target.resolver = t[3]
        elif callable(getattr(source, "items", None)):
            for key, value in list(source.items()):
                if not isinstance(value, genro_bag.Bag) and hasattr(value, "items"):
                    value = self.__class__(value)
                self.set_item(str(key), value)
        elif isinstance(source, (list, tuple)):  # noqa: SIM102
            if len(source) > 0:
                if not isinstance(source[0], (list, tuple)):
                    source = [source]
                for x in source:
                    if len(x) == 3:
                        self.set_item(x[0], x[1], _attributes=x[2])
                    else:
                        self.set_item(x[0], x[1])

    def _fill_from_string_source(self, source: str | bytes) -> None:
        """Handle string/bytes sources: XML strings, file paths, URLs."""
        if isinstance(source, bytes):
            is_xml = source.startswith(b"<") or b"<?xml" in source
        else:
            is_xml = source.startswith("<") or "<?xml" in source

        if is_xml:
            xml_source = source
            if isinstance(xml_source, bytes):
                xml_source = xml_source.decode("utf-8")
            if self._template_kwargs:
                xml_source = xml_source.format_map(AllowMissingDict(self._template_kwargs))
            result = self.__class__.from_xml(xml_source)
            self._import_nodes_with_duplicates(result)
            return

        if isinstance(source, str) and len(source) <= 300:
            parsed = urllib.parse.urlparse(source)
            if parsed.scheme in ("http", "https"):
                urlobj = urllib.request.urlopen(source)
                content = urlobj.read()
                if isinstance(content, bytes):
                    content = content.decode("utf-8")
                result = self.__class__.from_xml(content)
                self._import_nodes_with_duplicates(result)
                return
            if os.path.exists(source):
                self._fill_from_file_with_duplicates(source)
                return

    def _fill_from_file_with_duplicates(self, filepath: str) -> None:
        """Load a file, detect format, apply duplicate-tag post-processing for XML."""
        ext = os.path.splitext(filepath)[1].lower()
        if ext in (".xml", ".html", ".xhtml", ".htm", ""):
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            if self._template_kwargs:
                content = content.format_map(AllowMissingDict(self._template_kwargs))
            result = self.__class__.from_xml(content)
            self._import_nodes_with_duplicates(result)
        else:
            self._fill_from_bag(genro_bag.Bag.from_file(filepath), self)

    def _copy_nodes_from(self, result: Any) -> None:
        """Copy all nodes from result Bag into self, handling duplicates."""
        self._import_nodes_with_duplicates(result)

    def _import_nodes_with_duplicates(self, source_bag: Any) -> None:
        """Import nodes from a parsed Bag, converting renamed duplicates."""
        node_cls = self._node_class
        for node in source_bag:
            xml_tag = node.xml_tag
            label = node.label
            is_renamed_dup = xml_tag and xml_tag != label
            value = node._value
            if isinstance(value, genro_bag.Bag) and len(value) > 0:
                new_value = self.__class__()
                new_value._import_nodes_with_duplicates(value)
                value = new_value
            if is_renamed_dup:
                self.wrp_addItem(xml_tag, value, _attributes=node._attr or None)
            else:
                new_node = node_cls(self, label=label, value=value, attr=node._attr or None)
                if xml_tag:
                    new_node.xml_tag = xml_tag
                self._nodes._dict[label] = new_node
                self._nodes._list.append(new_node)

    # --- Dunder overrides ---

    def __pow__(self, kwargs: dict[str, Any]) -> None:
        """Update parent node's attributes. Usage: bag ** {'color': 'red'}."""
        if self.parent_node:
            self.parent_node.attr.update(kwargs)

    def __call__(self, what: Any = None) -> Any:
        """Return keys list if no arg, or value at path."""
        if not what:
            return list(self.keys())
        return self[what]

    __setitem__ = genro_bag.Bag.__setitem__

    # -----------------------------------------------------------------------
    # Override espliciti: metodi con stesso nome ma firma diversa
    # -----------------------------------------------------------------------

    def pop(self, path: str, default: Any = None, dflt: Any = None,
            _reason: str | None = None) -> Any:
        """Remove and return value at path. Accepts dflt (deprecated) or default."""
        if dflt is not None:
            warnings.warn(
                "dflt is deprecated, use default",
                DeprecationWarning,
                stacklevel=2,
            )
            default = dflt
        return super().pop(path, default=default, _reason=_reason)

    del_item = pop

    def walk(self, callback: Any = None, st_mode: Any = None, static: bool = True,
             _mode: Any = None, **kwargs: Any) -> Any:
        """Walk the tree depth-first. Accepts st_mode/_mode (deprecated) or static."""
        if st_mode is not None:
            warnings.warn(
                "st_mode is deprecated, use static",
                DeprecationWarning,
                stacklevel=2,
            )
            static = "static" in st_mode if isinstance(st_mode, str) else bool(st_mode)
        elif _mode is not None:
            warnings.warn(
                "_mode is deprecated, use static",
                DeprecationWarning,
                stacklevel=2,
            )
            static = "static" in _mode if isinstance(_mode, str) else bool(_mode)
        if callback is None:
            return self._iter_nodes_with_paths(static=static)
        return genro_bag.Bag.for_each(self, callback, static=static, deep=True, **kwargs)

    def subscribe(self, subscriber_id: Any = None, subscriberId: Any = None,
                  **kwargs: Any) -> None:
        """Subscribe to bag events. Accepts subscriberId (deprecated) or subscriber_id."""
        if subscriberId is not None:
            warnings.warn(
                "subscriberId is deprecated, use subscriber_id",
                DeprecationWarning,
                stacklevel=2,
            )
            subscriber_id = subscriberId
        super().subscribe(subscriber_id=subscriber_id, **kwargs)

    def unsubscribe(self, subscriber_id: Any = None, subscriberId: Any = None,
                    **kwargs: Any) -> None:
        """Unsubscribe from bag events. Accepts subscriberId (deprecated) or subscriber_id."""
        if subscriberId is not None:
            warnings.warn(
                "subscriberId is deprecated, use subscriber_id",
                DeprecationWarning,
                stacklevel=2,
            )
            subscriber_id = subscriberId
        super().unsubscribe(subscriber_id=subscriber_id, **kwargs)

    def digest(self, what: Any = None, condition: Any = None, as_columns: bool = False,
               asColumns: Any = None) -> list:
        """Extract filtered data. Accepts asColumns (deprecated) or as_columns."""
        if asColumns is not None:
            warnings.warn(
                "asColumns is deprecated, use as_columns",
                DeprecationWarning,
                stacklevel=2,
            )
            as_columns = asColumns
        return super().digest(what=what, condition=condition, as_columns=as_columns)

    def update(self, source: Any, ignore_none: bool = False,
               ignoreNone: Any = None, resolved: bool = False,
               preservePattern: Any = None) -> None:
        """Update Bag with nodes from source.

        Accepts ignoreNone/resolved/preservePattern (deprecated) for backward
        compatibility. When only source and ignore_none are provided, delegates
        directly to super().
        """
        if ignoreNone is not None:
            warnings.warn(
                "ignoreNone is deprecated, use ignore_none",
                DeprecationWarning,
                stacklevel=2,
            )
            ignore_none = ignoreNone

        if not resolved and preservePattern is None:
            # Simple case: delegate to core
            super().update(source, ignore_none=ignore_none)
            return

        # Legacy path: resolved and/or preservePattern
        if resolved:
            warnings.warn(
                "resolved is deprecated in update()",
                DeprecationWarning,
                stacklevel=2,
            )
        if preservePattern is not None:
            warnings.warn(
                "preservePattern is deprecated in update()",
                DeprecationWarning,
                stacklevel=2,
            )

        def updatable(value: Any) -> bool:
            if preservePattern and isinstance(value, str):
                return preservePattern.search(value) is None
            return True

        if isinstance(source, str):
            b = self.__class__()
            b.wrp_fromXml(source)
            source = b

        if isinstance(source, dict):
            for k, v in source.items():
                if updatable(self.get_item(k, default=None, static=True)):
                    self.set_item(k, v)
            return

        for n in source:
            node_resolver = n.resolver if hasattr(n, "resolver") else None
            node_value = None
            if node_resolver is None or resolved:
                node_value = n.get_value(static=True) if hasattr(n, "get_value") else n.static_value
                node_resolver = None
            if n.label in self._nodes:
                curr_node = self.get_node(n.label)
                node_attr = curr_node.attr
                if not preservePattern:
                    node_attr.update(n.attr)
                else:
                    for k, v in n.attr.items():
                        if updatable(node_attr.get(k)):
                            node_attr[k] = v
                if node_resolver is not None:
                    curr_node.resolver = node_resolver
                curr_value = curr_node.get_value(static=True)
                if isinstance(node_value, genro_bag.Bag) and isinstance(curr_value, genro_bag.Bag):
                    if hasattr(curr_value, "update"):
                        curr_value.update(
                            node_value, resolved=resolved,
                            ignoreNone=ignore_none,
                            preservePattern=preservePattern,
                        )
                elif (not ignore_none or node_value is not None) and updatable(curr_value):
                    curr_node.value = node_value
            else:
                self.set_item(
                    n.label, node_value,
                    _attributes=dict(n.attr) if n.attr else None,
                )

    # -----------------------------------------------------------------------
    # wrp_* methods (intercepted by __getattr__)
    # -----------------------------------------------------------------------

    def wrp_getItem(self, path: str, default: Any = None, mode: Any = None) -> Any:
        """Get value at path with original mode parameter."""
        static = False
        if mode and "static" in mode:
            static = True
        return self.get_item(path, default=default, static=static)

    def wrp_setItem(self, item_path: str, item_value: Any, _attributes: Any = None,
                    _position: Any = None, _duplicate: bool = False,
                    _updattr: bool = False, _validators: Any = None,
                    _removeNullAttributes: bool = True, _reason: Any = None,
                    **kwargs: Any) -> Any:
        """Set item at path with original parameter names. Returns self for chaining."""
        if _duplicate:
            return self.wrp_addItem(
                item_path, item_value, _attributes=_attributes,
                _position=_position or ">", **kwargs,
            )
        if kwargs:
            _attributes = dict(_attributes or {})
            _attributes.update(kwargs)
        self.set_item(
            item_path, item_value, _attributes=_attributes,
            node_position=_position, _updattr=_updattr,
            _remove_null_attributes=_removeNullAttributes,
            _reason=_reason,
        )
        return self

    def wrp_addItem(self, item_path: str, item_value: Any, _attributes: Any = None,
                    _position: str = ">", _validators: Any = None,
                    **kwargs: Any) -> Any:
        """Add item allowing duplicate labels."""
        if kwargs:
            _attributes = dict(_attributes or {})
            _attributes.update(kwargs)

        result, label = self._htraverse(item_path, write_mode=True)
        obj = cast("Bag", result)

        node = obj._node_class(
            obj, label=label, value=item_value, attr=_attributes,
            _remove_null_attributes=True,
        )
        obj._nodes.add_duplicate(label, node, _position)
        return self

    def wrp_getNode(self, path: Any = None, asTuple: bool = False,
                    autocreate: bool = False, default: Any = None) -> Any:
        """Get node at path with original parameter names."""
        node = self.get_node(path=path, autocreate=autocreate, default=default)
        return (node.parent_bag, node) if asTuple and node is not None else node

    def wrp_popNode(self, path: str, _reason: Any = None) -> Any:
        """Remove and return BagNode at path."""
        return self.pop_node(path, _reason=_reason)

    def wrp_getNodes(self, condition: Any = None) -> list:
        """Get filtered list of BagNode objects."""
        return self.get_nodes(condition=condition)

    def wrp_setAttr(self, _path: Any = None, _attributes: Any = None,
                    _removeNullAttributes: bool = True, **kwargs: Any) -> None:
        """Set attributes on node at path with original parameter names."""
        self.set_attr(path=_path, _attributes=_attributes,
                      _remove_null_attributes=_removeNullAttributes, **kwargs)

    def wrp_getAttr(self, path: Any = None, attr: Any = None, default: Any = None) -> Any:
        """Get node attribute at path."""
        return self.get_attr(path=path, attr=attr, default=default)

    def wrp_delAttr(self, path: Any = None, attr: Any = None) -> None:
        """Delete attribute from node at path."""
        if attr is not None:
            self.del_attr(path, attr)
        else:
            self.del_attr(path)

    @property
    def wrp_parentNode(self) -> Any:
        """Alias for parent_node."""
        return self.parent_node

    def wrp_has_key(self, path: str) -> bool:
        """Test presence of key (boolean). Legacy dict-like API."""
        return path in self

    def wrp_asDict(self, ascii: bool = False, lower: bool = False) -> dict:
        """Convert Bag to flat dict. CamelCase alias."""
        return self.as_dict(ascii=ascii, lower=lower)

    def wrp_asString(self, encoding: str = "UTF-8", mode: str = "weak") -> bytes:
        """Return encoded string representation."""
        return str(self).encode(encoding, "ignore")

    def wrp_getNodeByAttr(self, attr: str, value: Any, path: Any = None) -> Any:
        """Find node with specific attribute value (recursive)."""
        return self.get_node_by_attr(attr, value)

    def wrp_getNodeByValue(self, label: str, value: Any) -> Any:
        """Find node by sub-value match."""
        return self.get_node_by_value(label, value)

    def wrp_setBackRef(self, node: Any = None, parent: Any = None) -> None:
        """Enable backref mode."""
        self.set_backref(node=node, parent=parent)

    def wrp_clearBackRef(self) -> None:
        """Clear backref mode."""
        self.clear_backref()

    def wrp_delParentRef(self) -> None:
        """Disconnect from parent."""
        self.del_parent_ref()

    def wrp_setCallBackItem(self, path: str, callback: Any, **kwargs: Any) -> None:
        """Set a BagCbResolver at path."""
        resolver = BagCbResolver(callback, **kwargs)
        self.set_item(path, resolver)

    def wrp_setResolver(self, path: str, resolver: Any) -> None:
        """Set a resolver at path."""
        self.set_item(path, resolver)

    def wrp_getResolver(self, path: str) -> Any:
        """Get the resolver of the node at path."""
        node = self.get_node(path)
        return node.resolver if node else None

    wrp_getFormula = wrp_getResolver

    def wrp_formula(self, formula: Any, **kwargs: Any) -> None:
        """Deprecated. Use BagCbResolver for dynamic computation."""
        warnings.warn(
            "formula() is deprecated. Use BagCbResolver instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return None

    def wrp_defineSymbol(self, **kwargs: Any) -> None:
        """Deprecated. BagFormula symbols are not supported."""
        warnings.warn(
            "defineSymbol() is deprecated.",
            DeprecationWarning,
            stacklevel=2,
        )

    def wrp_defineFormula(self, **kwargs: Any) -> None:
        """Deprecated. BagFormula is not supported."""
        warnings.warn(
            "defineFormula() is deprecated.",
            DeprecationWarning,
            stacklevel=2,
        )

    def wrp_subscribe_legacy(self, subscriberId: Any = None, subscriber_id: Any = None,
                             **kwargs: Any) -> None:
        """Legacy subscribe entry point via __getattr__."""
        if subscriberId is not None:
            warnings.warn(
                "subscriberId is deprecated, use subscriber_id",
                DeprecationWarning,
                stacklevel=2,
            )
            subscriber_id = subscriberId
        self.subscribe(subscriber_id=subscriber_id, **kwargs)

    # --- Copy / Merge ---

    def wrp_copy(self) -> Any:
        """Return a shallow copy of this Bag."""
        result = self.__class__()
        for node in self:
            result.set_item(
                node.label, node.get_value(static=True),
                _attributes=dict(node.attr) if node.attr else None,
            )
        return result

    def wrp_diff(self, other: Any) -> str | None:
        """Compare two Bags. Returns None if equal, or a description string."""
        if self == other:
            return None
        if not isinstance(other, genro_bag.Bag):
            return f"Other class is {other.__class__}, self class is {self.__class__}"
        if len(other) != len(self):
            return "Different length"
        result = []
        other_nodes = list(other)
        for k, node in enumerate(self):
            if node != other_nodes[k]:
                result.append(
                    f"Node {k} label {node.label} difference {node.diff(other_nodes[k])}"
                )
        return "\n".join(result)

    def wrp_merge(self, otherbag: Any, upd_values: bool = True, add_values: bool = True,
                  upd_attr: bool = True, add_attr: bool = True) -> Any:
        """Merge two Bags into a new Bag."""
        result = self.__class__()
        othernodes: dict[str, Any] = {}
        for n in otherbag:
            othernodes[n.label] = n
        for node in self:
            k = node.label
            v = node.get_value(static=True)
            attr = dict(node.attr) if node.attr else {}
            if k in othernodes:
                onode = othernodes.pop(k)
                oattr = dict(onode.attr) if onode.attr else {}
                if upd_attr and add_attr:
                    attr.update(oattr)
                elif upd_attr:
                    attr = {ak: oattr.get(ak, av) for ak, av in attr.items()}
                elif add_attr:
                    new_attrs = {ak: av for ak, av in oattr.items() if ak not in attr}
                    attr.update(new_attrs)
                ov = onode.get_value(static=True)
                if isinstance(v, genro_bag.Bag) and isinstance(ov, genro_bag.Bag):
                    if hasattr(v, "wrp_merge"):
                        v = v.wrp_merge(ov, upd_values=upd_values, add_values=add_values,
                                        upd_attr=upd_attr, add_attr=add_attr)
                elif upd_values:
                    v = ov
            result.set_item(k, v, _attributes=attr if attr else None)
        if add_values:
            for k, n in othernodes.items():
                nv = n.get_value(static=True)
                nattr = dict(n.attr) if n.attr else {}
                result.set_item(k, nv, _attributes=nattr if nattr else None)
        return result

    # --- Child / Rowchild ---

    def wrp_rowchild(self, childname: str = "R_#", _pkey: Any = None, **kwargs: Any) -> Any:
        """Create a row child with auto-numbered name."""
        if not childname:
            childname = "R_#"
        childname = childname.replace("#", str(len(self)).zfill(8))
        _pkey = _pkey or childname
        return self.wrp_setItem(childname, None, _pkey=_pkey, **kwargs)

    def wrp_child(self, tag: str, childname: str = "*_#", childcontent: Any = None,
                  _parentTag: Any = None, **kwargs: Any) -> Any:
        """Create or access a named child Bag in the structure."""
        where = self
        if not childname:
            childname = "*_#"

        if "." in childname:
            namelist = childname.split(".")
            childname = namelist.pop()
            for label in namelist:
                if label not in where:
                    item = self.__class__()
                    where[label] = item
                where = where[label]

        childname = childname.replace("*", tag).replace("#", str(len(where)))

        if childcontent is None:
            childcontent = self.__class__()
            result = childcontent
        else:
            result = None

        if _parentTag:
            if isinstance(_parentTag, str):
                _parentTag = [s.strip() for s in _parentTag.split(",")]
            actual_parent_tag = where.get_attr("", "tag")
            if actual_parent_tag not in _parentTag:
                raise genro_bag.BagException(
                    f'{tag} "{childname}" cannot be inserted in a {actual_parent_tag}'
                )

        if childname in where and where[childname] != "" and where[childname] is not None:
            existing_tag = where.get_attr(childname, "tag")
            if existing_tag != tag:
                raise genro_bag.BagException(
                    f"Cannot change {childname} from {existing_tag} to {tag}"
                )
            else:
                update_kwargs = {k: v for k, v in kwargs.items() if v is not None}
                result = where[childname]
                if update_kwargs:
                    where.set_attr(childname, **update_kwargs)
        else:
            where.wrp_setItem(childname, childcontent, tag=tag, _attributes=kwargs)

        return result

    # --- Walk / Traverse extras ---

    def wrp_traverse(self) -> Any:
        """Generator yielding all BagNodes depth-first."""
        for node in self:
            yield node
            value = node.get_value(static=True)
            if isinstance(value, genro_bag.Bag):
                yield from value.wrp_traverse() if hasattr(value, "wrp_traverse") else (
                    n for _p, n in value.walk()
                )

    def wrp_isEmpty(self, zeroIsNone: bool = False, blankIsNone: bool = False) -> bool:
        """Check if Bag is empty with original parameter names."""
        return self.is_empty(zero_is_none=zeroIsNone, blank_is_none=blankIsNone)

    def wrp_filter(self, cb: Any, _mode: str = "static", **kwargs: Any) -> Any:
        """Return a new Bag containing only nodes where cb(node) is truthy."""
        result = self.__class__()
        static = "static" in _mode if isinstance(_mode, str) else bool(_mode)
        for node in self:
            value = node.get_value(static=static)
            if isinstance(value, genro_bag.Bag):
                if hasattr(value, "wrp_filter"):
                    filtered = value.wrp_filter(cb, _mode=_mode, **kwargs)
                else:
                    filtered = self.__class__()
                    for n in value:
                        v = n.get_value(static=static)
                        if isinstance(v, genro_bag.Bag):
                            continue
                        if cb(n):
                            filtered.set_item(
                                n.label, v,
                                _attributes=dict(n.attr) if n.attr else None,
                            )
                if len(filtered):
                    result.set_item(
                        node.label, filtered,
                        _attributes=dict(node.attr) if node.attr else None,
                    )
            elif cb(node):
                result.set_item(
                    node.label, value,
                    _attributes=dict(node.attr) if node.attr else None,
                )
        return result

    # --- Index / Leaves / nodesByAttr ---

    def wrp_getLeaves(self) -> list:
        """Return list of (path_string, value) for all leaf nodes."""
        return list(genro_bag.Bag.query(self, "#p,#v", deep=True, branch=False))

    def wrp_getIndex(self) -> list:
        """Return list of (path_list, BagNode) for ALL nodes recursively."""
        return [(p.split("."), n) for p, n in genro_bag.Bag.query(self, "#p,#n", deep=True)]

    def wrp_getIndexList(self, asText: bool = False) -> Any:
        """Return list of dot-separated path strings for all nodes."""
        paths = list(genro_bag.Bag.query(self, "#p", deep=True))
        if asText:
            return "\n".join(paths)
        return paths

    def wrp_nodesByAttr(self, attr: str, _mode: str = "static", **kwargs: Any) -> list:
        """Return list of BagNodes matching an attribute filter (recursive)."""
        static = "static" in _mode if isinstance(_mode, str) else bool(_mode)
        if "value" in kwargs:
            target_value = kwargs["value"]

            def condition(node: Any) -> bool:
                return node.get_attr(attr) == target_value
        else:
            def condition(node: Any) -> bool:
                return attr in (node.attr or {})

        return list(
            genro_bag.Bag.query(self, "#n", deep=True, condition=condition, static=static)
        )

    # --- Iteration aliases ---

    def wrp_iterkeys(self) -> Any:
        """Yield labels (generator version of keys)."""
        return self.keys(iter=True)

    def wrp_itervalues(self) -> Any:
        """Yield values (generator version of values)."""
        return self.values(iter=True)

    def wrp_iteritems(self) -> Any:
        """Yield (label, value) tuples (generator version of items)."""
        return self.items(iter=True)

    # --- XML serialization ---

    def _node_to_xml(self, node: Any, namespaces: list[str],
                     self_closed_tags: list[str] | None = None,
                     sign_key: str | None = None, expires_in: int | None = None,
                     pretty: bool = False, depth: int = 0) -> str:
        """Override to add _T type annotations matching the original format."""
        local_namespaces = self._extract_namespaces(node.attr)
        current_namespaces = namespaces + local_namespaces

        xml_tag = node.xml_tag or node.node_tag or node.label
        tag, original_tag = self._sanitize_tag(xml_tag, current_namespaces)

        attrs_parts: list[str] = []
        if original_tag is not None:
            attrs_parts.append(f"_tag={saxutils.quoteattr(original_tag)}")

        where = f"node {node.label!r}"
        if node.resolver is not None:
            payload = encode_resolver(node.resolver, sign_key, expires_in, where)
            attrs_parts.append(f"_resolver={saxutils.quoteattr(payload)}")

        value = node.get_value(static=True)

        t_code = _type_code(value)
        if isinstance(value, genro_bag.Bag):
            t_code = "BAG"
        if t_code and t_code not in ("T", ""):
            attrs_parts.append(f'_T="{t_code}"')

        if node.attr:
            for k, v in encode_attrs(node.attr, sign_key, expires_in, where).items():
                if v is not None:
                    attr_type = _type_code(v)
                    if attr_type and attr_type not in ("T", ""):
                        attrs_parts.append(
                            f"{k}={saxutils.quoteattr(str(v) + '::' + attr_type)}"
                        )
                    else:
                        attrs_parts.append(f"{k}={saxutils.quoteattr(str(v))}")

        attrs_str = " " + " ".join(attrs_parts) if attrs_parts else ""

        if hasattr(value, "_bag_to_xml"):
            pretty = pretty and node.attr.get("xml:space") != "preserve"
            inner = value._bag_to_xml(current_namespaces, self_closed_tags, sign_key, expires_in, pretty, depth + 1)
            if inner:
                if pretty:
                    return f"<{tag}{attrs_str}>\n{inner}\n{'  ' * depth}</{tag}>"
                return f"<{tag}{attrs_str}>{inner}</{tag}>"
            if self_closed_tags is None or tag in self_closed_tags:
                return f"<{tag}{attrs_str}/>"
            return f"<{tag}{attrs_str}></{tag}>"

        if value is None or value == "":
            if self_closed_tags is None or tag in self_closed_tags:
                return f"<{tag}{attrs_str}/>"
            return f"<{tag}{attrs_str}></{tag}>"

        if isinstance(value, bool):
            text = saxutils.escape("y" if value else "")
        elif isinstance(value, (list, dict, tuple)):
            text = saxutils.escape(json_module.dumps(value))
        else:
            text = saxutils.escape(str(value))
        return f"<{tag}{attrs_str}>{text}</{tag}>"

    def wrp_toXml(self, filename: Any = None, encoding: str = "UTF-8",
                  typeattrs: bool = True, typevalue: bool = True,
                  unresolved: bool = False, addBagTypeAttr: bool = True,
                  output_encoding: Any = None, autocreate: bool = False,
                  translate_cb: Any = None, self_closed_tags: Any = None,
                  omitUnknownTypes: bool = False, catalog: Any = None,
                  omitRoot: bool = False, forcedTagAttr: Any = None,
                  docHeader: Any = None, pretty: bool = False,
                  mode4d: bool = False) -> Any:
        """Serialize to XML with legacy-compatible format via BagToXml."""
        return BagToXml().build(
            self, filename=filename, encoding=encoding,
            typeattrs=typeattrs, typevalue=typevalue,
            addBagTypeAttr=addBagTypeAttr,
            unresolved=unresolved, autocreate=autocreate,
            forcedTagAttr=forcedTagAttr,
            translate_cb=translate_cb,
            self_closed_tags=self_closed_tags,
            omitUnknownTypes=omitUnknownTypes,
            catalog=catalog, omitRoot=omitRoot,
            docHeader=docHeader, pretty=pretty,
            mode4d=mode4d,
        )

    def wrp_fromXml(self, source: Any, catalog: Any = None, bagcls: Any = None,
                    empty: Any = None, attrInValue: Any = None,
                    avoidDupLabel: Any = None) -> None:
        """Load XML into this Bag (instance method, original API)."""
        bagcls = bagcls or self.__class__
        fromFile = isinstance(source, str) and not source.lstrip().startswith('<')
        if fromFile and not os.path.exists(source):
            fromFile = False
        result = BagFromXml().build(source, fromFile, catalog=catalog,
                                    bagcls=bagcls, empty=empty)
        self.clear()
        if isinstance(result, genro_bag.Bag):
            for node in result:
                self._nodes._dict[node.label] = node
                self._nodes._list.append(node)
                node._parent_bag = self

    # --- JSON serialization ---

    def wrp_toJson(self, typed: bool = True, nested: bool = False) -> str:
        """Serialize to JSON string with original parameter names."""
        return genro_bag.Bag.to_json(self, typed=typed)

    def wrp_fromJson(self, json_data: Any, listJoiner: Any = None) -> None:
        """Load JSON into this Bag (instance method, original API)."""
        if isinstance(json_data, str):
            json_data = json_module.loads(json_data)
        result = self.__class__.from_json(json_data, list_joiner=listJoiner)
        self.clear()
        for node in result:
            self.set_item(
                node.label, node.value,
                _attributes=dict(node.attr) if node.attr else None,
            )

    # --- YAML ---

    def wrp_fromYaml(self, y: str, listJoiner: Any = None) -> None:
        """Load YAML into this Bag (instance method, original API)."""
        if os.path.isfile(y):
            with open(y, "rb") as f:
                docs = list(yaml.safe_load_all(f))
        else:
            docs = list(yaml.safe_load_all(y))

        self.clear()
        if len(docs) == 1:
            self.wrp_fromJson(docs[0], listJoiner=listJoiner)
        else:
            for i, doc in enumerate(docs):
                child = self.__class__()
                child.wrp_fromJson(doc, listJoiner=listJoiner)
                self.set_item(f"r_{i:04d}", child)

    # --- Pickle ---

    def wrp_pickle(self, destination: Any = None, bin: bool = True) -> Any:
        """Serialize to pickle format (original API)."""
        protocol = 2 if bin else 0
        if not destination:
            return pickle_module.dumps(self, protocol)
        if hasattr(destination, "write"):
            pickle_module.dump(self, destination, protocol)
        else:
            with open(destination, mode="wb") as f:
                pickle_module.dump(self, f, protocol)
        return None

    # --- toTree ---

    def wrp_toTree(self, group_by: Any, caption: Any = None, attributes: Any = "*") -> Any:
        """Transform flat Bag into hierarchical Bag grouped by specified keys."""
        if isinstance(group_by, str):
            group_by = [g.strip() for g in group_by.split(",")]

        result = self.__class__()
        for key in self.keys():
            item = self[key]
            path_parts = []
            for g in group_by:
                val = item[g] if isinstance(item, genro_bag.Bag) else None
                path_parts.append(str(val).replace(".", "_") if val is not None else "_none_")

            if caption is not None:
                leaf_label = str(item[caption]) if isinstance(item, genro_bag.Bag) else str(key)
            else:
                leaf_label = str(key)
            path_parts.append(leaf_label.replace(".", "_"))

            if isinstance(item, genro_bag.Bag):
                if attributes == "*":
                    attrs = {k: item[k] for k in item}
                else:
                    attr_list = attributes if isinstance(attributes, (list, tuple)) else [attributes]
                    attrs = {k: item[k] for k in attr_list if k in item}
            else:
                attrs = {}

            result.set_item(".".join(path_parts), None, _attributes=attrs)
        return result

    # --- Bag-level getFormattedValue ---

    def wrp_getFormattedValue(self, joiner: str = "\n", omitEmpty: bool = True, **kwargs: Any) -> str:
        """Return formatted display string joining all node formatted values."""
        r = []
        for n in self:
            if not n.label.startswith("_"):
                fv = n.wrp_getFormattedValue(joiner=joiner, omitEmpty=omitEmpty, **kwargs)
                if fv or not omitEmpty:
                    r.append(fv)
        return joiner.join(r)

    # --- asDictDeeply ---

    def wrp_asDictDeeply(self, ascii: bool = False, lower: bool = False) -> dict:
        """Recursively convert Bag to nested dict."""
        d = self.as_dict(ascii=ascii, lower=lower)
        for k, v in list(d.items()):
            if isinstance(v, genro_bag.Bag):
                d[k] = _as_dict_deeply(v, ascii=ascii, lower=lower)
        return d

    # --- summarizeAttributes ---

    def wrp_summarizeAttributes(self, attrnames: Any = None) -> dict:
        """Recursively sum specified attributes across nodes."""
        result: dict[str, Any] = {}
        for n in self:
            v = n.get_value(static=True)
            if v and isinstance(v, genro_bag.Bag) and hasattr(v, "wrp_summarizeAttributes"):
                n.attr.update(v.wrp_summarizeAttributes(attrnames))
            for k in (attrnames or []):
                result[k] = result.get(k, 0) + (n.attr.get(k, 0) or 0)
        return result

    # --- Bag-level validator stubs ---

    def wrp_addValidator(self, path: str, validator: Any, parameterString: Any) -> None:
        """Stub: validators are not supported."""
        warnings.warn(
            "addValidator() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    def wrp_removeValidator(self, path: str, validator: Any) -> None:
        """Stub: validators are not supported."""
        warnings.warn(
            "removeValidator() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    # --- getDeepestNode ---

    def wrp_getDeepestNode(self, path: Any = None) -> Any:
        """Return the deepest matching node and set _tail_list on it."""
        if not path:
            return self.parent_node

        pathlist = smartsplit(path.replace("../", "#^."), ".")
        pathlist = [x for x in pathlist if x]

        curr = self
        last_node = None
        consumed = 0

        for i, label in enumerate(pathlist):
            if label == "#^":
                if curr.parent is not None:
                    curr = curr.parent
                    consumed = i + 1
                else:
                    break
                continue

            node = curr._nodes.get(label) if hasattr(curr._nodes, "get") else None
            if node is None:
                break
            last_node = node
            consumed = i + 1

            if i < len(pathlist) - 1:
                value = node.get_value(static=True)
                if isinstance(value, genro_bag.Bag):
                    curr = value
                else:
                    break

        if last_node is not None:
            if hasattr(last_node, "_tail_list"):
                last_node._tail_list = pathlist[consumed:]
            return last_node

        return None
