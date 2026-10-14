"""Compatibility wrapper for gnr.core.gnrbag API over genro_bag.

Provides Bag, BagNode, and BagNodeContainer classes that expose the original
camelCase API (getItem, setItem, addItem, getNode, etc.) while delegating to
genro_bag internally. This enables drop-in replacement of gnr.core.gnrbag imports.

Usage:
    from replacement.gnrbag import Bag, BagNode
    # Use with the same API as gnr.core.gnrbag
"""

import asyncio
import copy as copy_module
import datetime
import json as json_module
import os
import pickle as pickle_module
import warnings
from decimal import Decimal
from typing import Any, cast
from xml.sax import saxutils

import yaml

import urllib.parse
import urllib.request

import genro_bag
from genro_bag._resolver_wire import encode_attrs, encode_resolver
from replacement._legacy_json import LegacyJsonParser
from genro_bag.bagnode import BagNodeContainer, smartsplit
from genro_bag.resolver import BagCbResolver as _NewBagCbResolver
from genro_bag.resolver import BagResolver as _NewBagResolver
from replacement.gnrbagxml import BagAsXml
from replacement.gnrbagxml import BagFromXml
from replacement.gnrbagxml import BagToXml


# Type code mapping for _T XML attributes (matches original GnrClassCatalog)
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
# Module-level symbols for gnr.core.gnrbag API compatibility
# ---------------------------------------------------------------------------


class AllowMissingDict(dict):
    """Dict that returns '{key}' for missing keys (template substitution)."""

    def __missing__(self, key):
        return "{" + key + "}"


def normalizeItemPath(item_path):
    """Normalize item path — port from gnr.core.gnrbag.normalizeItemPath."""
    if isinstance(item_path, (str, list)):
        return item_path
    return str(item_path).replace(".", "_")


class BagException(Exception):
    """Base exception for Bag operations."""


class BagNodeException(BagException):
    """Exception for BagNode operations."""


# BagAsXml is imported from replacement.gnrbagxml (see top-level imports)


class BagValidationError(BagException):
    """Raised when BagNode validation fails."""


class BagDeprecatedCall(BagException):
    """Raised for deprecated Bag API calls."""

    def __init__(self, errcode, message):
        self.errcode = errcode
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Resolver wrappers — camelCase compatibility for subclassing
# ---------------------------------------------------------------------------

# camelCase → snake_case key remapping for classKwargs/class_kwargs
_CAMEL_TO_SNAKE_KEYS = {
    "cacheTime": "cache_time",
    "readOnly": "read_only",
    "retryPolicy": "retry_policy",
    "asBag": "as_bag",
}

_SNAKE_TO_CAMEL_KEYS = {v: k for k, v in _CAMEL_TO_SNAKE_KEYS.items()}


class BagResolver(_NewBagResolver):
    """BagResolver wrapper with camelCase API for gnr.core.gnrbag compatibility.

    Subclasses can define classKwargs and classArgs (camelCase) which are
    automatically translated to class_kwargs and class_args (snake_case)
    via __init_subclass__.

    Property aliases: parentNode, cacheTime, readOnly, instanceKwargs.
    Method alias: resolverSerialize() -> serialize().
    """

    classKwargs = {"cacheTime": 0, "readOnly": True}
    classArgs = []

    def __init_subclass__(cls, **kwargs):
        """Translate camelCase class attributes to snake_case for the new API."""
        super().__init_subclass__(**kwargs)
        # Translate classKwargs -> class_kwargs if defined on this subclass
        if "classKwargs" in cls.__dict__:
            translated = {}
            for k, v in cls.__dict__["classKwargs"].items():
                translated[_CAMEL_TO_SNAKE_KEYS.get(k, k)] = v
            cls.class_kwargs = translated
        # Translate classArgs -> class_args if defined on this subclass
        if "classArgs" in cls.__dict__:
            cls.class_args = list(cls.__dict__["classArgs"])

    def __init__(self, *args, **kwargs):
        # Remap camelCase kwargs to snake_case before passing to parent
        translated_kwargs = {}
        for k, v in kwargs.items():
            translated_kwargs[_CAMEL_TO_SNAKE_KEYS.get(k, k)] = v
        super().__init__(*args, **translated_kwargs)

    @property
    def parentNode(self):
        """Alias for parent_node (original naming)."""
        return self.parent_node

    @parentNode.setter
    def parentNode(self, value):
        self.parent_node = value

    @property
    def cacheTime(self):
        """Alias for cache_time (original naming)."""
        return self._kw.get("cache_time", 0)

    @cacheTime.setter
    def cacheTime(self, value):
        self._kw["cache_time"] = value

    @property
    def readOnly(self):
        """Alias for read_only (original naming)."""
        return self.read_only

    @readOnly.setter
    def readOnly(self, value):
        self._kw["read_only"] = value
        self._init_kwargs["read_only"] = value

    @property
    def instanceKwargs(self):
        """Return dict of current parameter values (original API).

        Mirrors the original's behavior: reads current values for all
        class_kwargs and class_args parameters, returning them with
        camelCase keys for compatibility.
        """
        result = {}
        for par in self.class_args:
            result[par] = self._kw.get(par)
        for par in self.class_kwargs:
            camel_key = _SNAKE_TO_CAMEL_KEYS.get(par, par)
            result[camel_key] = self._kw.get(par)
        return result

    def resolverSerialize(self, args=None, kwargs=None):
        """Serialize to dict with original format keys.

        Returns dict with: resolverclass, resolvermodule, args, kwargs.
        Adds cacheTime to kwargs for compatibility with original.
        """
        data = self.serialize()
        result = {
            "resolverclass": data["resolver_class"],
            "resolvermodule": data["resolver_module"],
            "args": data.get("args", []),
            "kwargs": dict(data.get("kwargs", {})),
        }
        result["kwargs"]["cacheTime"] = self.cacheTime
        return result


class BagCbResolver(BagResolver):
    """BagCbResolver wrapper accepting 'method' as first arg (original naming).

    The original gnr.core.gnrbag.BagCbResolver uses classArgs=['method'] and
    calls self.method(**self.kwargs). The new genro_bag.BagCbResolver uses
    class_args=['callback']. This wrapper bridges both: accepts 'method' as
    the positional arg name, stores it as 'callback' internally.
    """

    classKwargs = {"cacheTime": 0, "readOnly": False}
    classArgs = ["method"]

    def __init__(self, *args, **kwargs):
        # The parent __init_subclass__ translates classArgs=['method'] to
        # class_args=['method']. But the new BagCbResolver expects 'callback'.
        # We need to remap 'method' -> 'callback' in the _kw dict after init.
        super().__init__(*args, **kwargs)
        if "method" in self._kw and "callback" not in self._kw:
            self._kw["callback"] = self._kw.pop("method")

    @property
    def is_async(self):
        """Detect if the callback is a coroutine function."""
        cb = self._kw.get("callback")
        if cb is None:
            return False
        return asyncio.iscoroutinefunction(cb)

    def load(self):
        """Call sync callback with non-internal parameters."""
        cb = self._kw["callback"]
        params = {k: v for k, v in self._kw.items()
                  if k not in self.internal_params and k != "callback"}
        return cb(**params)

    async def async_load(self):
        """Call async callback with non-internal parameters."""
        cb = self._kw["callback"]
        params = {k: v for k, v in self._kw.items()
                  if k not in self.internal_params and k != "callback"}
        return await cb(**params)


class BagNode(genro_bag.BagNode):
    """BagNode wrapper with camelCase aliases for gnr.core.gnrbag compatibility.

    Supports duplicate labels via _display_label slot:
    - label slot: always the VISIBLE name (what keys/items/iteration return)
    - _display_label slot: the suffixed DICT KEY for duplicates (e.g. "key__dup_1"),
      or None for non-duplicate nodes

    This way node.label is a direct slot access (no property), same speed as
    genro_bag.BagNode.label. The dict key override is only used in container
    operations (pop, del, add_duplicate).
    """

    __slots__ = ("_display_label", "_tail_list")

    def __init__(self, *args, _attributes=None, validators=None, **kwargs):
        self._display_label = None
        self._tail_list = []
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

    # label is NOT overridden — direct slot access from parent, zero overhead

    @property
    def _dict_key(self):
        """Return the dict key (suffixed for duplicates, or label)."""
        dk = self._display_label
        return dk if dk is not None else self.label

    @property
    def tag(self):
        """Return tag (from attr or label), matching original BagNode.tag."""
        return self.attr.get("tag") or self.label

    def _get_fullpath(self):
        """Return fullpath or None — alias for original BagNode._get_fullpath()."""
        return self.fullpath if self.parent_bag and self.parent_bag.backref else None

    # --- Label access ---

    def getLabel(self):
        """Return the node's label."""
        return self.label

    def setLabel(self, label):
        """Set node's label."""
        self.label = label

    # --- Value access ---

    def getValue(self, mode=""):
        """Return the value of the BagNode.

        Maps the original mode string to the new static bool parameter.
        mode='static' -> static=True (return cached value without resolver).
        """
        static = "static" in mode if mode else False
        return self.get_value(static=static)

    def setValue(
        self,
        value,
        trigger=True,
        _attributes=None,
        _updattr=None,
        _removeNullAttributes=True,
        _reason=None,
    ):
        """Set the node's value with original parameter names."""
        self.set_value(
            value,
            trigger=trigger,
            _attributes=_attributes,
            _updattr=_updattr,
            _remove_null_attributes=_removeNullAttributes,
            _reason=_reason,
        )

    def getStaticValue(self):
        """Get node's value in static mode (bypassing resolver)."""
        return self.static_value

    def setStaticValue(self, value):
        """Set node's value directly, bypassing processing and triggers."""
        self.static_value = value

    # --- Attribute access ---

    def getAttr(self, label=None, default=None):
        """Get attribute by name, or all attributes if label is None."""
        return self.get_attr(label=label, default=default)

    def setAttr(
        self,
        attr=None,
        trigger=True,
        _updattr=True,
        _removeNullAttributes=True,
        **kwargs,
    ):
        """Set attributes via dict or kwargs."""
        self.set_attr(
            attr=attr,
            trigger=trigger,
            _updattr=_updattr,
            _remove_null_attributes=_removeNullAttributes,
            **kwargs,
        )

    def delAttr(self, *attrToDelete):
        """Remove attributes by name(s)."""
        self.del_attr(*attrToDelete)

    # --- Navigation aliases ---

    @property
    def parentbag(self):
        """Alias for parent_bag (original naming)."""
        return self.parent_bag

    @parentbag.setter
    def parentbag(self, value):
        self.parent_bag = value

    @property
    def parentNode(self):
        """Alias for parent_node (original naming)."""
        return self.parent_node

    # --- Event aliases ---

    def subscribe(self, subscriberId=None, subscriber_id=None, callback=None):
        """Subscribe to node changes. Accepts both subscriberId and subscriber_id."""
        sid = subscriberId if subscriberId is not None else subscriber_id
        super().subscribe(subscriber_id=sid, callback=callback)

    def unsubscribe(self, subscriberId=None, subscriber_id=None):
        """Unsubscribe from node changes. Accepts both naming conventions."""
        sid = subscriberId if subscriberId is not None else subscriber_id
        super().unsubscribe(subscriber_id=sid)

    # --- Formatted value ---

    def getFormattedValue(self, joiner=None, omitEmpty=True, mode="", **kwargs):
        """Return formatted display value with caption prefix.

        Port of original gnr.core.gnrbag.BagNode.getFormattedValue (line 211).
        Uses _formattedValue or _displayedValue attr if present, otherwise raw value.
        Caption comes from _valuelabel, name_long, or label.capitalize().
        If value is a Bag, recurses into Bag.getFormattedValue.
        """
        static = "static" in mode if mode else False
        v = self.get_value(static=static)
        if isinstance(v, genro_bag.Bag):
            if hasattr(v, "getFormattedValue"):
                v = v.getFormattedValue(joiner=joiner, omitEmpty=omitEmpty, mode=mode, **kwargs)
            else:
                v = str(v)
        else:
            attr = self.attr or {}
            v = attr.get("_formattedValue") or attr.get("_displayedValue") or v
        if v or not omitEmpty:
            attr = self.attr or {}
            caption = attr.get("_valuelabel") or attr.get("name_long") or self.label.capitalize()
            return "%s: %s" % (caption, v)
        return ""

    # --- Validator stubs ---

    def addValidator(self, validator, parameterString):
        """Stub: validators are not supported in genro_bag."""
        warnings.warn(
            "addValidator() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    def removeValidator(self, validator):
        """Stub: validators are not supported in genro_bag."""
        warnings.warn(
            "removeValidator() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    def getValidatorData(self, validator, label=None, dflt=None):
        """Stub: validators are not supported in genro_bag. Returns dflt."""
        warnings.warn(
            "getValidatorData() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )
        return dflt

    # --- Diff override (recursive into Bag values) ---

    def diff(self, other):
        """Compare with another BagNode, recursing into Bag values.

        Returns None if equal, or a string describing the difference.
        The parent genro_bag BagNode.diff does NOT recurse into Bag values;
        the original gnr.core.gnrbag BagNode.diff does (line 310-311).
        """
        if self.label != other.label:
            return "Other label: %s" % other.label
        if self.attr != other.attr:
            return "attributes self:%s --- other:%s" % (self.attr, other.attr)
        sv, ov = self._value, other._value
        if sv != ov:
            if isinstance(sv, genro_bag.Bag) and isinstance(ov, genro_bag.Bag):
                return "value:%s" % sv.diff(ov)
            return "value self:%s --- other:%s" % (sv, ov)
        return None


class WrapperBagNodeContainer(BagNodeContainer):
    """BagNodeContainer with duplicate label support.

    For duplicates, node.label is the visible name and node._display_label
    stores the suffixed dict key (e.g. "key__dup_1"). This means keys()/items()
    use node.label directly (slot access, no property overhead).

    Overrides __delitem__ and pop to use _node_dict_key() for correct dict removal.
    Adds add_duplicate() for inserting nodes with suffixed dict keys.
    """

    def _node_dict_key(self, node):
        """Return the dict key for a node (suffixed key for duplicates, or label)."""
        dk = getattr(node, "_display_label", None)
        return dk if dk is not None else node.label

    def __delitem__(self, key):
        """Delete item using _dict_key for correct dict removal."""
        if isinstance(key, int):
            idx_to_delete = [key]
        else:
            idx_to_delete = [self.index(block) for block in smartsplit(key, ",")]

        for idx in sorted(idx_to_delete, reverse=True):
            if 0 <= idx < len(self._list):
                v = self._list.pop(idx)
                self._dict.pop(self._node_dict_key(v), None)

    def pop(self, key):
        """Remove and return item using _dict_key for correct dict removal."""
        idx = self.index(key) if isinstance(key, str) else key
        if 0 <= idx < len(self._list):
            node = self._list.pop(idx)
            self._dict.pop(self._node_dict_key(node), None)
            return node
        return None

    def add_duplicate(self, label, node, position=">"):
        """Add a node with a duplicate label.

        Generates a unique suffixed dict key (label__dup_N) stored in
        node._display_label. The visible label (node.label slot) stays
        as the original name so keys()/items()/iteration show duplicates.
        """
        if label in self._dict:
            dup_n = 1
            while f"{label}__dup_{dup_n}" in self._dict:
                dup_n += 1
            dict_key = f"{label}__dup_{dup_n}"
            node.label = label           # visible name
            node._display_label = dict_key  # dict key override
        else:
            dict_key = label

        idx = self._parse_position(position)
        self._dict[dict_key] = node
        self._list.insert(idx, node)
        return node


def _as_dict_deeply(bag, ascii=False, lower=False):
    """Recursively convert any genro_bag.Bag to nested dict."""
    d = bag.as_dict(ascii=ascii, lower=lower)
    for k, v in list(d.items()):
        if isinstance(v, genro_bag.Bag):
            d[k] = _as_dict_deeply(v, ascii=ascii, lower=lower)
    return d


class Bag(LegacyJsonParser, genro_bag.Bag):
    """Bag wrapper with camelCase aliases for gnr.core.gnrbag compatibility.

    All original camelCase methods (getItem, setItem, addItem, getNode, etc.)
    are available alongside the new snake_case methods. The addItem method
    supports duplicate labels via WrapperBagNodeContainer.
    """

    _node_class = BagNode
    _container_class = WrapperBagNodeContainer

    def __init__(self, source=None, **kwargs):
        """Create a new Bag from various source types.

        Extends genro_bag.Bag.__init__ to support all original source types:
        - None: empty Bag
        - dict: keys become labels, values become node values
        - Bag: copy constructor
        - str containing '<' or '<?xml': parse as XML
        - str starting with http/https: fetch URL and parse
        - str (file path): load from file
        - list/tuple: sequence of (label, value) or (label, value, attrs)

        Keyword args:
            _template_kwargs: dict for template variable substitution in XML.
        """
        self._template_kwargs = kwargs.pop("_template_kwargs", {})
        # Initialize the base Bag (empty — we handle source ourselves)
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
                if t[3]:  # resolver
                    target = self.get_node(t[0])
                    if target:
                        target.resolver = t[3]
        elif callable(getattr(source, "items", None)):
            for key, value in list(source.items()):
                if not isinstance(value, genro_bag.Bag) and hasattr(value, "items"):
                    value = self.__class__(value)
                self.set_item(str(key), value)
        elif isinstance(source, (list, tuple)):
            if len(source) > 0:
                if not isinstance(source[0], (list, tuple)):
                    source = [source]
                for x in source:
                    if len(x) == 3:
                        self.set_item(x[0], x[1], _attributes=x[2])
                    else:
                        self.set_item(x[0], x[1])

    def _fill_from_string_source(self, source):
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

        # Short string — check if file path or URL
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
            # File path — load XML with duplicate-tag awareness
            if os.path.exists(source):
                self._fill_from_file_with_duplicates(source)
                return

    def _fill_from_file_with_duplicates(self, filepath):
        """Load a file, detect format, apply duplicate-tag post-processing for XML."""
        ext = os.path.splitext(filepath)[1].lower()
        if ext in (".xml", ".html", ".xhtml", ".htm", ""):
            # XML file — read content, parse, import with duplicate detection
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            if self._template_kwargs:
                content = content.format_map(AllowMissingDict(self._template_kwargs))
            result = self.__class__.from_xml(content)
            self._import_nodes_with_duplicates(result)
        else:
            # Non-XML file — delegate to genro_bag.Bag.fill_from
            self._fill_from_bag(genro_bag.Bag.from_file(filepath), self)

    def _copy_nodes_from(self, result):
        """Copy all nodes from result Bag into self, handling duplicates."""
        self._import_nodes_with_duplicates(result)

    # --- Access methods ---

    def getItem(self, path, default=None, mode=None):
        """Get value at path with original mode parameter.

        Maps mode='static' to static=True on get_item.
        """
        static = False
        if mode and "static" in mode:
            static = True
        return self.get_item(path, default=default, static=static)

    def setItem(
        self,
        item_path,
        item_value,
        _attributes=None,
        _position=None,
        _duplicate=False,
        _updattr=False,
        _validators=None,
        _removeNullAttributes=True,
        _reason=None,
        **kwargs,
    ):
        """Set item at path with original parameter names.

        Returns self (the Bag) for chaining, matching original behavior.
        When _duplicate=True, delegates to addItem.
        """
        if _duplicate:
            return self.addItem(
                item_path,
                item_value,
                _attributes=_attributes,
                _position=_position or ">",
                **kwargs,
            )
        if kwargs:
            _attributes = dict(_attributes or {})
            _attributes.update(kwargs)
        self.set_item(
            item_path,
            item_value,
            _attributes=_attributes,
            node_position=_position,
            _updattr=_updattr,
            _remove_null_attributes=_removeNullAttributes,
            _reason=_reason,
        )
        return self

    def addItem(
        self,
        item_path,
        item_value,
        _attributes=None,
        _position=">",
        _validators=None,
        **kwargs,
    ):
        """Add item allowing duplicate labels.

        Navigates to the target container via _htraverse, creates a new
        BagNode, and delegates to WrapperBagNodeContainer.add_duplicate()
        which handles suffixed dict keys and _display_label.
        """
        if kwargs:
            _attributes = dict(_attributes or {})
            _attributes.update(kwargs)

        result, label = self._htraverse(item_path, write_mode=True)
        obj = cast("Bag", result)

        node = obj._node_class(
            obj,
            label=label,
            value=item_value,
            attr=_attributes,
            _remove_null_attributes=True,
        )
        obj._nodes.add_duplicate(label, node, _position)
        return self

    __setitem__ = genro_bag.Bag.__setitem__

    # --- Node access ---

    def getNode(self, path=None, asTuple=False, autocreate=False, default=None):
        """Get node at path with original parameter names."""
        node = self.get_node(path=path, autocreate=autocreate, default=default)
        return (node.parent_bag, node) if asTuple and node is not None else node

    def popNode(self, path, _reason=None):
        """Remove and return BagNode at path."""
        return self.pop_node(path, _reason=_reason)

    def getNodes(self, condition=None):
        """Get filtered list of BagNode objects."""
        return self.get_nodes(condition=condition)

    # --- Attribute methods at Bag level ---

    def setAttr(self, _path=None, _attributes=None, _removeNullAttributes=True, **kwargs):
        """Set attributes on node at path with original parameter names."""
        self.set_attr(path=_path, _attributes=_attributes, _remove_null_attributes=_removeNullAttributes, **kwargs)

    def getAttr(self, path=None, attr=None, default=None):
        """Get node attribute at path."""
        return self.get_attr(path=path, attr=attr, default=default)

    def delAttr(self, path=None, attr=None):
        """Delete attribute from node at path.

        Original signature takes (path, attr) where attr is a single string.
        """
        if attr is not None:
            self.del_attr(path, attr)
        else:
            self.del_attr(path)

    # --- Pop/delete ---

    def pop(self, path, dflt=None, _reason=None):
        """Remove and return value at path. Maps dflt to default."""
        return genro_bag.Bag.pop(self, path, default=dflt, _reason=_reason)

    delItem = pop

    # --- Iteration aliases ---

    def iterkeys(self):
        """Yield labels (generator version of keys)."""
        return self.keys(iter=True)

    def itervalues(self):
        """Yield values (generator version of values)."""
        return self.values(iter=True)

    def iteritems(self):
        """Yield (label, value) tuples (generator version of items)."""
        return self.items(iter=True)

    # --- Digest alias ---

    def digest(self, what=None, condition=None, asColumns=False):
        """Extract filtered data with original parameter names."""
        return genro_bag.Bag.digest(self, what=what, condition=condition, as_columns=asColumns)

    # --- Navigation aliases ---

    @property
    def parentNode(self):
        """Alias for parent_node (original naming)."""
        return self.parent_node

    def has_key(self, path):
        """Test presence of key (boolean). Legacy dict-like API."""
        return path in self

    # --- Dict conversion ---

    def asDict(self, ascii=False, lower=False):
        """Convert Bag to flat dict (first level only). CamelCase alias."""
        return self.as_dict(ascii=ascii, lower=lower)

    # --- String representation ---

    def asString(self, encoding="UTF-8", mode="weak"):
        """Return encoded string representation. CamelCase alias."""
        return str(self).encode(encoding, "ignore")

    # --- __pow__ (attribute update shorthand) ---

    def __pow__(self, kwargs):
        """Update parent node's attributes. Usage: bag ** {'color': 'red'}."""
        if self.parent_node:
            self.parent_node.attr.update(kwargs)

    # --- __call__ ---

    def __call__(self, what=None):
        """Return keys list if no arg, or value at path."""
        if not what:
            return list(self.keys())
        return self[what]

    # --- Node lookup aliases ---

    def getNodeByAttr(self, attr, value, path=None):
        """Find node with specific attribute value (recursive). CamelCase alias.

        The original mutates the `path` list param to build the found path.
        This wrapper delegates to get_node_by_attr which doesn't support
        the path param. The path param is accepted but not populated.
        """
        return self.get_node_by_attr(attr, value)

    def getNodeByValue(self, label, value):
        """Find node by sub-value match. CamelCase alias."""
        return self.get_node_by_value(label, value)

    # --- Deep copy ---

    def deepcopy(self):
        """Return a deep recursive copy. CamelCase alias."""
        return genro_bag.Bag.deepcopy(self)

    # --- Backref aliases ---

    def setBackRef(self, node=None, parent=None):
        """Enable backref mode (original naming)."""
        self.set_backref(node=node, parent=parent)

    def clearBackRef(self):
        """Clear backref mode (original naming)."""
        self.clear_backref()

    def delParentRef(self):
        """Disconnect from parent (original naming)."""
        self.del_parent_ref()

    # --- Event aliases ---

    def subscribe(self, subscriberId=None, subscriber_id=None, **kwargs):
        """Subscribe to bag events. Accepts both subscriberId and subscriber_id."""
        sid = subscriberId if subscriberId is not None else subscriber_id
        super().subscribe(subscriber_id=sid, **kwargs)

    def unsubscribe(self, subscriberId=None, subscriber_id=None, **kwargs):
        """Unsubscribe from bag events. Accepts both naming conventions."""
        sid = subscriberId if subscriberId is not None else subscriber_id
        super().unsubscribe(subscriber_id=sid, **kwargs)

    # --- Resolver aliases ---

    def setCallBackItem(self, path, callback, **kwargs):
        """Set a BagCbResolver at path (original API)."""
        resolver = BagCbResolver(callback, **kwargs)
        self.set_item(path, resolver)

    def setResolver(self, path, resolver):
        """Set a resolver at path (original API)."""
        self.set_item(path, resolver)

    def getResolver(self, path):
        """Get the resolver of the node at path (original API)."""
        node = self.get_node(path)
        return node.resolver if node else None

    getFormula = getResolver

    def formula(self, formula, **kwargs):
        """Deprecated. Use BagCbResolver for dynamic computation."""
        warnings.warn(
            "formula() is deprecated and not supported in genro_bag. "
            "Use BagCbResolver instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return None

    def defineSymbol(self, **kwargs):
        """Deprecated. BagFormula symbols are not supported in genro_bag."""
        warnings.warn(
            "defineSymbol() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    def defineFormula(self, **kwargs):
        """Deprecated. BagFormula is not supported in genro_bag."""
        warnings.warn(
            "defineFormula() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    # --- Copy / Diff / Merge ---

    def copy(self):
        """Return a shallow copy of this Bag.

        Uses manual iteration instead of copy.copy() to avoid triggering
        __getstate__/_make_picklable which clears parent references.
        """
        result = self.__class__()
        for node in self:
            result.set_item(
                node.label,
                node.get_value(static=True),
                _attributes=dict(node.attr) if node.attr else None,
            )
        return result

    def diff(self, other):
        """Compare two Bags. Returns None if equal, or a description string.

        Matches original gnr.core.gnrbag.Bag.diff (gnrbag.py:1165).
        """
        if self == other:
            return None
        if not isinstance(other, genro_bag.Bag):
            return "Other class is %s, self class is %s" % (
                other.__class__,
                self.__class__,
            )
        if len(other) != len(self):
            return "Different length"
        result = []
        other_nodes = list(other)
        for k, node in enumerate(self):
            if node != other_nodes[k]:
                result.append(
                    "Node %i label %s difference %s"
                    % (k, node.label, node.diff(other_nodes[k]))
                )
        return "\n".join(result)

    def merge(self, otherbag, upd_values=True, add_values=True, upd_attr=True, add_attr=True):
        """Merge two Bags into a new Bag.

        Matches original gnr.core.gnrbag.Bag.merge (gnrbag.py:1188).
        """
        result = self.__class__()
        othernodes = {}
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
                    if hasattr(v, "merge"):
                        v = v.merge(
                            ov,
                            upd_values=upd_values,
                            add_values=add_values,
                            upd_attr=upd_attr,
                            add_attr=add_attr,
                        )
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

    def rowchild(self, childname="R_#", _pkey=None, **kwargs):
        """Create a row child with auto-numbered name.

        Matches original gnr.core.gnrbag.Bag.rowchild (gnrbag.py:2408).
        Replaces '#' with zero-padded counter based on current Bag length.
        """
        if not childname:
            childname = "R_#"
        childname = childname.replace("#", str(len(self)).zfill(8))
        _pkey = _pkey or childname
        return self.setItem(childname, None, _pkey=_pkey, **kwargs)

    def child(self, tag, childname="*_#", childcontent=None, _parentTag=None, **kwargs):
        """Create or access a named child Bag in the structure.

        Matches original gnr.core.gnrbag.Bag.child (gnrbag.py:2419).
        This is the core method for building Genropy structures (HTML, menus,
        DB models, configs). The '*' in childname is replaced with tag,
        '#' is replaced with the current child count.
        """
        where = self
        if not childname:
            childname = "*_#"

        # Navigate dotted path, creating intermediate Bags
        if "." in childname:
            namelist = childname.split(".")
            childname = namelist.pop()
            for label in namelist:
                if label not in where:
                    item = self.__class__()
                    where[label] = item
                where = where[label]

        # Replace * with tag, # with counter
        childname = childname.replace("*", tag).replace("#", str(len(where)))

        # Determine child content
        if childcontent is None:
            childcontent = self.__class__()
            result = childcontent
        else:
            result = None

        # Validate parent tag constraint
        if _parentTag:
            if isinstance(_parentTag, str):
                _parentTag = [s.strip() for s in _parentTag.split(",")]
            actual_parent_tag = where.getAttr("", "tag")
            if actual_parent_tag not in _parentTag:
                raise genro_bag.BagException(
                    '%s "%s" cannot be inserted in a %s'
                    % (tag, childname, actual_parent_tag)
                )

        # Check if child already exists
        if childname in where and where[childname] != "" and where[childname] is not None:
            existing_tag = where.getAttr(childname, "tag")
            if existing_tag != tag:
                raise genro_bag.BagException(
                    "Cannot change %s from %s to %s"
                    % (childname, existing_tag, tag)
                )
            else:
                # Default kwargs don't clear old attributes
                update_kwargs = {k: v for k, v in kwargs.items() if v is not None}
                result = where[childname]
                if update_kwargs:
                    where.setAttr(childname, **update_kwargs)
        else:
            where.setItem(childname, childcontent, tag=tag, _attributes=kwargs)

        return result

    # --- Walk / Traverse ---

    def walk(self, callback=None, _mode="static", **kwargs):
        """Walk the tree depth-first with original parameter names.

        Maps _mode string to static bool. If callback is None, returns
        generator of (path, node) tuples (new-style generator mode).
        Handles recursive calls from genro_bag.Bag.walk which pass static=
        as a keyword argument.
        """
        if "static" in kwargs:
            static = kwargs.pop("static")
        else:
            static = "static" in _mode if isinstance(_mode, str) else bool(_mode)
        if callback is None:
            return self._iter_nodes_with_paths(static=static)
        return genro_bag.Bag.for_each(self, callback, static=static, deep=True, **kwargs)

    def traverse(self):
        """Generator yielding all BagNodes depth-first (original API).

        Unlike walk() which yields (path, node) tuples, traverse() yields
        only the BagNode objects, matching the original gnr.core.gnrbag behavior.
        """
        for node in self:
            yield node
            value = node.get_value(static=True)
            if isinstance(value, genro_bag.Bag):
                yield from value.traverse() if hasattr(value, "traverse") else (
                    n for _p, n in value.walk()
                )

    def isEmpty(self, zeroIsNone=False, blankIsNone=False):
        """Check if Bag is empty with original parameter names."""
        return self.is_empty(zero_is_none=zeroIsNone, blank_is_none=blankIsNone)

    # --- Filter ---

    def filter(self, cb, _mode="static", **kwargs):
        """Return a new Bag containing only nodes where cb(node) is truthy.

        Recursively filters nested Bags. Empty sub-Bags after filtering
        are excluded. Matches the original gnr.core.gnrbag.Bag.filter behavior.
        """
        result = self.__class__()
        static = "static" in _mode if isinstance(_mode, str) else bool(_mode)
        for node in self:
            value = node.get_value(static=static)
            if isinstance(value, genro_bag.Bag):
                if hasattr(value, "filter"):
                    filtered = value.filter(cb, _mode=_mode, **kwargs)
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

    def getLeaves(self):
        """Return list of (path_string, value) for all leaf nodes.

        Only returns leaf nodes (non-Bag values), with dot-separated paths.
        """
        return list(genro_bag.Bag.query(self, "#p,#v", deep=True, branch=False))

    def getIndex(self):
        """Return list of (path_list, BagNode) for ALL nodes recursively.

        path_list is a list of label strings (not dot-joined).
        """
        return [(p.split("."), n) for p, n in genro_bag.Bag.query(self, "#p,#n", deep=True)]

    def getIndexList(self, asText=False):
        """Return list of dot-separated path strings for all nodes.

        If asText=True, returns a single newline-joined string.
        """
        paths = list(genro_bag.Bag.query(self, "#p", deep=True))
        if asText:
            return "\n".join(paths)
        return paths

    def nodesByAttr(self, attr, _mode="static", **kwargs):
        """Return list of BagNodes matching an attribute filter (recursive).

        If value kwarg is provided, matches nodes where node.attr[attr] == value.
        Otherwise matches nodes that have the attribute (any value).
        """
        static = "static" in _mode if isinstance(_mode, str) else bool(_mode)
        if "value" in kwargs:
            target_value = kwargs["value"]

            def condition(node):
                return node.get_attr(attr) == target_value
        else:

            def condition(node):
                return attr in (node.attr or {})

        return list(
            genro_bag.Bag.query(self, "#n", deep=True, condition=condition, static=static)
        )

    # --- XML serialization ---

    def _node_to_xml(self, node, namespaces, self_closed_tags=None,
                     sign_key=None, expires_in=None, pretty=False, depth=0):
        """Override to add _T type annotations matching the original format.

        Adds _T="L" for int, _T="R" for float, _T="D" for date, etc.
        Also types attribute values with ::TYPE suffix for non-string types.
        """
        local_namespaces = self._extract_namespaces(node.attr)
        current_namespaces = namespaces + local_namespaces

        xml_tag = node.xml_tag or node.node_tag or node.label
        tag, original_tag = self._sanitize_tag(xml_tag, current_namespaces)

        attrs_parts = []
        if original_tag is not None:
            attrs_parts.append(f"_tag={saxutils.quoteattr(original_tag)}")

        where = f"node {node.label!r}"
        if node.resolver is not None:
            payload = encode_resolver(node.resolver, sign_key, expires_in, where)
            attrs_parts.append(f"_resolver={saxutils.quoteattr(payload)}")

        value = node.get_value(static=True)

        # Add _T for typed values
        t_code = _type_code(value)
        # For Bag values, add _T="BAG" if the value is a Bag
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

    def toXml(
        self,
        filename=None,
        encoding="UTF-8",
        typeattrs=True,
        typevalue=True,
        unresolved=False,
        addBagTypeAttr=True,
        output_encoding=None,
        autocreate=False,
        translate_cb=None,
        self_closed_tags=None,
        omitUnknownTypes=False,
        catalog=None,
        omitRoot=False,
        forcedTagAttr=None,
        docHeader=None,
        pretty=False,
        mode4d=False,
    ):
        """Serialize to XML with legacy-compatible format via BagToXml.

        Delegates to replacement.gnrbagxml.BagToXml for full legacy compatibility
        including translate_cb, catalog, resolver serialization, __forbidden__,
        BagAsXml, forcedTagAttr, omitUnknownTypes, typeattrs/typevalue.
        """
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

    def fromXml(self, source, catalog=None, bagcls=None, empty=None,
                attrInValue=None, avoidDupLabel=None):
        """Load XML into this Bag (instance method, original API).

        Delegates to replacement.gnrbagxml.BagFromXml for full legacy
        compatibility including catalog, array handling, and duplicates.
        """
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

    def _import_nodes_with_duplicates(self, source_bag):
        """Import nodes from a parsed Bag, converting renamed duplicates.

        When the XML parser encounters duplicate tags like <mobile>...<mobile>,
        it renames them to 'mobile' and 'mobile_1'. We detect this by comparing
        node.xml_tag to node.label and use addItem for duplicates.

        Used by fill_from, _fill_from_file_with_duplicates, and _copy_nodes_from.
        """
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
                self.addItem(xml_tag, value, _attributes=node._attr or None)
            else:
                new_node = node_cls(self, label=label, value=value, attr=node._attr or None)
                if xml_tag:
                    new_node.xml_tag = xml_tag
                self._nodes._dict[label] = new_node
                self._nodes._list.append(new_node)

    # --- JSON serialization ---

    def toJson(self, typed=True, nested=False):
        """Serialize to JSON string with original parameter names."""
        return genro_bag.Bag.to_json(self, typed=typed)

    def fromJson(self, json_data, listJoiner=None):
        """Load JSON into this Bag (instance method, original API).

        Accepts both parsed data (dict/list) and JSON strings.
        The original gnr.core.gnrbag.fromJson only accepts parsed data
        (dict/list), not strings — callers are expected to call json.loads()
        first. The wrapper handles both for convenience.
        """
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

    def fromYaml(self, y, listJoiner=None):
        """Load YAML into this Bag (instance method, original API).

        Handles file paths and raw YAML strings. Multi-document YAML
        creates numbered children (r_0000, r_0001, ...).
        """
        if os.path.isfile(y):
            with open(y, "rb") as f:
                docs = list(yaml.safe_load_all(f))
        else:
            docs = list(yaml.safe_load_all(y))

        self.clear()
        if len(docs) == 1:
            self.fromJson(docs[0], listJoiner=listJoiner)
        else:
            for i, doc in enumerate(docs):
                child = self.__class__()
                child.fromJson(doc, listJoiner=listJoiner)
                self.set_item(f"r_{i:04d}", child)

    # --- Pickle ---

    def pickle(self, destination=None, bin=True):
        """Serialize to pickle format (original API).

        Args:
            destination: File path or file-like object. If None, return bytes.
            bin: If True (default), use binary protocol 2. If False, protocol 0.
        """
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

    def toTree(self, group_by, caption=None, attributes="*"):
        """Transform flat Bag into hierarchical Bag grouped by specified keys.

        Each item in self should be a Bag with fields. group_by specifies which
        fields to use as grouping levels. The result is a nested Bag hierarchy.

        Args:
            group_by: Comma-separated string or list of field names to group by.
            caption: Field to use as leaf label. If None, uses original key.
            attributes: "*" for all attributes, or list of attribute names to keep.
        """
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
                    attrs = {k: item[k] for k in item.keys()}
                else:
                    attr_list = attributes if isinstance(attributes, (list, tuple)) else [attributes]
                    attrs = {k: item[k] for k in attr_list if k in item}
            else:
                attrs = {}

            result.set_item(".".join(path_parts), None, _attributes=attrs)
        return result

    # --- Bag-level getFormattedValue ---

    def getFormattedValue(self, joiner="\n", omitEmpty=True, **kwargs):
        """Return formatted display string joining all node formatted values.

        Port of original gnr.core.gnrbag.Bag.getFormattedValue (line 878).
        Skips nodes whose labels start with '_'.
        """
        r = []
        for n in self:
            if not n.label.startswith("_"):
                fv = n.getFormattedValue(joiner=joiner, omitEmpty=omitEmpty, **kwargs)
                if fv or not omitEmpty:
                    r.append(fv)
        return joiner.join(r)

    # --- asDictDeeply ---

    def asDictDeeply(self, ascii=False, lower=False):
        """Recursively convert Bag to nested dict.

        Port of original gnr.core.gnrbag.Bag.asDictDeeply (line 1466).
        First level via asDict, then recursively converts Bag values.
        """
        d = self.as_dict(ascii=ascii, lower=lower)
        for k, v in list(d.items()):
            if isinstance(v, genro_bag.Bag):
                d[k] = _as_dict_deeply(v, ascii=ascii, lower=lower)
        return d

    # --- summarizeAttributes ---

    def summarizeAttributes(self, attrnames=None):
        """Recursively sum specified attributes across nodes.

        Port of original gnr.core.gnrbag.Bag.summarizeAttributes (line 709).
        Mutates node attrs in place with summarized child values.
        """
        result = {}
        for n in self:
            v = n.get_value(static=True)
            if v and isinstance(v, genro_bag.Bag):
                if hasattr(v, "summarizeAttributes"):
                    n.attr.update(v.summarizeAttributes(attrnames))
            for k in (attrnames or []):
                result[k] = result.get(k, 0) + (n.attr.get(k, 0) or 0)
        return result

    # --- Bag-level validator stubs ---

    def addValidator(self, path, validator, parameterString):
        """Stub: validators are not supported in genro_bag."""
        warnings.warn(
            "addValidator() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    def removeValidator(self, path, validator):
        """Stub: validators are not supported in genro_bag."""
        warnings.warn(
            "removeValidator() is deprecated and not supported in genro_bag.",
            DeprecationWarning,
            stacklevel=2,
        )

    # --- update (full original signature) ---

    def update(self, otherbag, resolved=False, ignoreNone=False, preservePattern=None):
        """Update Bag with key/value pairs from otherbag.

        Full port of original gnr.core.gnrbag.Bag.update (line 1102).
        Handles dict, string (XML), and Bag inputs.
        resolved: if True, resolve values before copying.
        preservePattern: compiled regex protecting matching string values.
        """
        def updatable(value):
            if preservePattern and isinstance(value, str):
                return preservePattern.search(value) is None
            return True

        if isinstance(otherbag, dict):
            for k, v in otherbag.items():
                if updatable(self.get_item(k, default=None, static=True)):
                    self.setItem(k, v)
            return

        if isinstance(otherbag, str):
            b = self.__class__()
            b.fromXml(otherbag)
            otherbag = b

        for n in otherbag:
            node_resolver = n.resolver if hasattr(n, "resolver") else None
            node_value = None
            if node_resolver is None or resolved:
                node_value = n.get_value(static=True) if hasattr(n, "get_value") else n.getValue("static")
                node_resolver = None
            if n.label in self._nodes:
                curr_node = self.getNode(n.label)
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
                            node_value,
                            resolved=resolved,
                            ignoreNone=ignoreNone,
                            preservePattern=preservePattern,
                        )
                else:
                    if not ignoreNone or node_value is not None:
                        if updatable(curr_value):
                            curr_node.value = node_value
            else:
                self.setItem(
                    n.label,
                    node_value,
                    _attributes=dict(n.attr) if n.attr else None,
                )

    # --- getDeepestNode ---

    def getDeepestNode(self, path=None):
        """Return the deepest matching node and set _tail_list on it.

        Port of original gnr.core.gnrbag.Bag.getDeepestNode (line 1279).
        Traverses path segments as far as possible. Returns the deepest
        node found, with _tail_list set to remaining unmatched segments.
        """
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
