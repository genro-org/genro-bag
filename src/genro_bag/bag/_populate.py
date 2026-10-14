# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""BagPopulate mixin - initialization, population, copy and pickle for Bag.

Provides replacement, source loading, copying, pickle support, and updates.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from genro_toolbox import safe_is_instance

from genro_bag._resolver_wire import is_resolver

if TYPE_CHECKING:
    from genro_bag.bag._core import Bag

_IS_BAG = "genro_bag.bag._core.Bag"


class BagPopulate:
    """Mixin providing population, copy, pickle and update methods for Bag.

    Supports internal loading from multiple sources (dict, list, file path, XML/JSON
    string, another Bag), plus deepcopy, pickle, and update semantics.
    """

    _nodes: Any
    _backref: Any
    parent: Any
    parent_node: Any

    if TYPE_CHECKING:
        def set_item(self, path: str, value: Any, _attributes: dict[Any, Any] | None = ..., node_position: str | int | None = ..., _updattr: bool = ..., _remove_null_attributes: bool = ..., _reason: str | None = ..., _fired: bool = ..., do_trigger: bool = ..., resolver: Any = ..., node_tag: str | None = ..., **kwargs: Any) -> Any: ...
        def set_backref(self, node: Any = ..., parent: Any = ...) -> None: ...
        def clear(self) -> None: ...
        def __iter__(self) -> Iterator: ...

    def fill_from(self, source: dict | list | None = None, *, recursive: bool = True) -> Self:
        """Atomically replace contents from native containers, with literal labels."""
        if not isinstance(recursive, bool):
            raise TypeError("recursive must be a bool")
        prepared = self.__class__()
        self._populate_into(prepared, source, recursive=recursive)
        return self._replace_prepared(prepared)

    def _append_literal(self, label: str, value: Any, attr=None, resolver=None, node_tag=None):
        """Append a node to an offline Bag without interpreting label syntax."""
        if not isinstance(label, str):
            raise TypeError("Bag labels must be strings")
        node = self._node_class(
            self, label, None, attr=attr, resolver=resolver, node_tag=node_tag,
            _remove_null_attributes=False,
        )
        if is_resolver(value):
            node.resolver = value
        else:
            node._value = value
        self._nodes[label] = node
        return node

    def _populate_into(self, target: Bag, source: Any, *, recursive: bool = True) -> None:
        if source is None:
            return
        if not isinstance(source, (dict, list)):
            raise TypeError(f"Bag source must be dict, list or None, got {type(source).__name__}")
        active: set[int] = set()

        def populate(bag, data, path):
            if id(data) in active:
                raise ValueError(f"Cyclic Bag source at {path}")
            active.add(id(data))
            try:
                if isinstance(data, dict):
                    entries = data.items()
                else:
                    width = len(str(len(data))) + 1
                    entries = ((f"r_{i:0{width}d}", value) for i, value in enumerate(data))
                for label, value in entries:
                    if not isinstance(label, str):
                        raise TypeError(f"Bag labels must be strings at {path}: {label!r}")
                    if recursive and isinstance(value, (dict, list)):
                        child = self.__class__()
                        populate(child, value, f"{path}[{label!r}]")
                        value = child
                    bag._append_literal(label, value)
            finally:
                active.remove(id(data))

        populate(target, source, "$")

    @classmethod
    def from_file(cls, path: str | Path, transport: str | None = None) -> Bag:
        """Read an XML, .bag.json or .bag.mp file explicitly."""
        target = cls()
        target._fill_from_file(os.fspath(path), target, transport=transport)
        return target


    def _load_source(self, source: Any = None, transport: str | None = None) -> Self:
        """Decode a construction/compatibility source before replacing contents."""
        if source is None:
            return self
        prepared = self.__class__()
        self._populate_legacy_into(prepared, source, transport=transport)
        return self._replace_prepared(prepared)

    def replace(self, other: Bag) -> Self:
        """Replace all contents from a Bag, preserving this Bag's identity.

        Copy structural nodes without resolving values. Preparation precedes any
        mutation, and attached Bags emit one parent update. Self replacement is
        a no-op. Returns self.
        """
        if not safe_is_instance(other, _IS_BAG):
            raise TypeError("Bag.replace expects a Bag")
        if other is self:
            return self
        prepared = self.__class__()
        self._fill_from_bag(other, prepared)
        return self._replace_prepared(prepared)

    def _replace_prepared(self, new_bag: Bag) -> Self:
        # 2. Orphan the current nodes: detach them from self before the swap.
        #    BagNode.orphaned() clears _parent_bag and recursively clear_backref
        #    on any nested Bag value — mirrors the JS legacy helper.
        for node in list(self._nodes):
            node.orphaned()

        # 3. Swap the containers atomically.
        old_nodes = self._nodes
        self._nodes = new_bag._nodes
        new_bag._nodes = old_nodes

        # 4. Adopt the new nodes through the parent_bag setter, which
        #    propagates set_backref recursively to nested Bag values when
        #    self has backref. For orphan self this is a plain re-pointer.
        for node in self._nodes:
            node.parent_bag = self

        # 5. Place the old nodes inside new_bag as a navigable snapshot.
        #    new_bag is orphan (no backref, no parent), so assigning directly
        #    to _parent_bag is enough and avoids firing any event.
        for node in new_bag._nodes:
            node._parent_bag = new_bag

        # 6. Emit a single atomic upd_value event on the parent when attached.
        if self.backref and self.parent is not None and self.parent_node is not None:
            self.parent._on_node_changed(
                self.parent_node,
                [self.parent_node.label],
                evt="upd_value",
                oldvalue=new_bag,
            )

        return self

    def _populate_legacy_into(
        self, target: Bag, source: Any, transport: str | None = None
    ) -> None:
        """Dispatch source decoding and populate target in place.

        Target can be self (default populate path) or an offline orphan Bag
        (atomic fill_from path): the helpers that actually write nodes
        operate on `target`, leaving self untouched.
        """
        if safe_is_instance(source, _IS_BAG):
            self._fill_from_bag(source, target)
        elif callable(getattr(source, "items", None)):
            self._fill_from_dict(source, target)
        elif isinstance(source, tuple) and self._is_node_spec(source):
            self._fill_from_node_specs([source], target)
        elif isinstance(source, (list, tuple)) and source and all(
            self._is_node_spec(item) for item in source
        ):
            self._fill_from_node_specs(source, target)
        elif isinstance(source, list):
            self._fill_from_list(source, target)
        elif isinstance(source, bytes):
            self._populate_legacy_into(target, source.decode("utf-8").strip(), transport)
        elif isinstance(source, Path):
            self._fill_from_file(str(source), target, transport=transport)
        elif isinstance(source, str):
            stripped = source.strip()
            if stripped.startswith("<"):
                loaded = self.__class__.from_xml(stripped)
                self._fill_from_bag(loaded, target)
            elif stripped.startswith("{") or stripped.startswith("["):
                loaded = self.__class__.from_json(stripped)
                self._fill_from_bag(loaded, target)
            else:
                self._fill_from_file(source, target, transport=transport)
        else:
            raise TypeError(
                f"fill_from: unsupported source type {type(source).__name__}"
            )

    @staticmethod
    def _is_node_spec(item: Any) -> bool:
        """Tuples distinguish explicit nodes from ordinary positional lists."""
        return isinstance(item, tuple) and len(item) in (2, 3) and isinstance(item[0], str)

    def _fill_from_node_specs(self, specs: Any, target: Bag) -> None:
        """Populate named nodes from (label, value[, attributes]) tuples."""
        for spec in specs:
            attributes = spec[2] if len(spec) == 3 else None
            if attributes is not None and not isinstance(attributes, Mapping):
                raise TypeError("Node tuple attributes must be a mapping or None")
            target.set_item(spec[0], spec[1], _attributes=(
                dict(attributes) if attributes is not None else None
            ))

    def _fill_from_list(self, data: list, target: Bag) -> None:
        """Populate target from a list. Items become numbered nodes."""
        target.clear()
        for i, item in enumerate(data):
            if isinstance(item, dict):
                item = self.__class__(item)
            target.set_item(str(i), item)

    def _fill_from_file(
        self, path: str, target: Bag, transport: str | None = None
    ) -> None:
        """Load bag contents from a file into target.

        Detects transport from file extension (unless transport is specified):
        - .bag.json: TYTX JSON format
        - .bag.mp: TYTX MessagePack format
        - .xml: XML format (with auto-detect for legacy GenRoBag)

        Args:
            path: Path to the file to load.
            target: Bag to populate.
            transport: Force transport ('xml', 'json', 'msgpack'). If None, detect from extension.

        Raises:
            FileNotFoundError: If file does not exist.
            ValueError: If file extension is not recognized and format not specified.
        """
        if os.path.isdir(path):
            from genro_bag.resolvers.directory_resolver import DirectoryResolver

            label = os.path.basename(path.rstrip(os.sep))
            target.set_item(label, DirectoryResolver(path))
            return

        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")

        # Determine transport: explicit or from extension
        if transport is None:
            if path.endswith(".bag.json"):
                transport = "json"
            elif path.endswith(".bag.mp"):
                transport = "msgpack"
            elif path.endswith(".xml"):
                transport = "xml"
            else:
                raise ValueError(
                    f"Unrecognized file extension: {path}. Supported: .bag.json, .bag.mp, .xml"
                )

        # Load based on transport
        cls = self.__class__
        if transport == "json":
            with open(path, encoding="utf-8") as f:
                data = f.read()
            loaded = cls.from_tytx(data, transport="json")
            self._fill_from_bag(loaded, target)

        elif transport == "msgpack":
            with open(path, "rb") as f:
                data_bytes = f.read()
            loaded = cls.from_tytx(data_bytes, transport="msgpack")
            self._fill_from_bag(loaded, target)

        elif transport == "xml":
            with open(path, encoding="utf-8") as f:
                data = f.read()
            loaded = cls.from_xml(data)
            self._fill_from_bag(loaded, target)

    def _fill_from_bag(self, other: Bag, target: Bag) -> None:
        """Copy nodes from another Bag into target.

        Clears target's current contents first and copies all nodes from the
        source Bag.

        Args:
            other: Source Bag to copy from.
            target: Bag to populate.
        """
        target.clear()
        for node in other:
            # Deep copy the value if it's a Bag
            value = node.get_value(static=True)
            if safe_is_instance(value, _IS_BAG):
                value = value.__class__().replace(value)
            new_node = target._append_literal(
                node.label,
                value,
                attr=dict(node.attr),
                resolver=copy.deepcopy(node.resolver, {id(node): None}),
                node_tag=node.node_tag,
            )
            new_node.set_attr(dict(node.attr), trigger=False, _updattr=False,
                              _remove_null_attributes=False)
            new_node.xml_tag = node.xml_tag

    def _fill_from_dict(
        self, data: Any, target: Bag
    ) -> None:
        """Populate target from a dictionary-like source.

        Clears target's current contents first and creates nodes from dict items.
        Nested dicts are converted to nested Bags.

        Args:
            data: Dict where keys become labels and values become node values.
            target: Bag to populate.
        """
        target.clear()
        for key, value in data.items():
            if isinstance(value, dict):
                value = self.__class__(value)
            target.set_item(key, value)

    # -------------------- class methods --------------------------------

    @classmethod
    def from_url(cls, url: str, timeout: int = 30) -> Bag:
        """Load Bag from URL synchronously.

        Fetches content from URL and parses based on HTTP content-type header.
        Uses UrlResolver internally for DRY implementation.

        Args:
            url: HTTP/HTTPS URL to fetch.
            timeout: Request timeout in seconds. Default 30.

        Returns:
            Bag: Parsed content as Bag. Format auto-detected from content-type:
                - application/json, text/json -> from_json
                - application/xml, text/xml -> from_xml

        Raises:
            httpx.HTTPError: If HTTP request fails.
            ValueError: If content-type is not supported.

        Example:
            >>> bag = Bag.from_url('https://example.com/data.xml')
        """
        from genro_bag.resolvers import UrlResolver

        resolver = UrlResolver(url, timeout=timeout, as_bag=False)
        result = resolver()

        bag = cls()
        bag._load_source(result)
        return bag  # type: ignore[return-value]

    # -------------------- deepcopy --------------------------------

    def deepcopy(self) -> Self:
        """Return a deep copy of this Bag.

        Creates a new Bag with copies of all nodes. Nested Bags are
        recursively deep copied. Values are copied by reference unless
        they are Bags. Node attributes are copied as a new dict.

        Resolvers are rebuilt rather than shared, both a node's own and
        those held in attributes: a copy that shared them would let a
        parameter change on one Bag reach the other.

        Returns:
            A new Bag with copied nodes.

        Example:
            >>> bag = Bag({'a': 1, 'b': Bag({'c': 2})})
            >>> copy = bag.deepcopy()
            >>> copy['b.c'] = 3
            >>> bag['b.c']  # Original unchanged
            2
        """
        result = self.__class__()
        for node in self:
            value = node.static_value
            if safe_is_instance(value, _IS_BAG):
                value = value.__class__().replace(value)
            attr = {k: self._copy_resolver(v) if is_resolver(v) else v
                    for k, v in node.attr.items()}
            resolver = self._copy_resolver(node.resolver) if node.resolver is not None else None
            copied = result._append_literal(
                node.label, value, attr=attr, resolver=resolver, node_tag=node.node_tag
            )
            copied.xml_tag = node.xml_tag
        return result

    def _copy_resolver(self, resolver: Any) -> Any:
        """Rebuild a resolver from its own parameters.

        Reuses the serialize/deserialize pair rather than copy.deepcopy:
        a resolver holds a back-reference to its node, which should not be duplicated.
        """
        return resolver.deserialize(resolver.serialize())

    # -------------------- pickle support --------------------------------

    def __getstate__(self) -> dict:
        """Return detached pickle state without mutating the live Bag graph."""
        state = dict(self.__dict__)
        for name in (
            "_upd_subscribers",
            "_ins_subscribers",
            "_del_subscribers",
            "_txn_subscribers",
        ):
            state[name] = {}
        state["_parent"] = None
        state["_parent_node"] = None
        if state.get("_backref"):
            state["_backref"] = "x"
        return state

    def __setstate__(self, state: dict) -> None:
        """Restore state after unpickling."""
        self.__dict__.update(state)
        self._restore_from_picklable()

    def _restore_from_picklable(self) -> None:
        """Restore Bag from its picklable form (internal)."""
        if self._backref == "x":
            self.set_backref()
        else:
            for node in self:
                node._parent_bag = None
                value = node.static_value
                if safe_is_instance(value, _IS_BAG):
                    value._restore_from_picklable()

    # -------------------- update --------------------------------

    def update(
        self,
        source: Bag | Mapping[Any, Any] | str | Any,
        resolved: bool = False,
        ignore_none: bool = False,
        preserve_pattern: Any = None,
        *,
        ignoreNone: bool | None = None,
        preservePattern: Any = None,
    ) -> None:
        """Update this Bag with nodes from source.

        Merges nodes from source into this Bag. For existing labels,
        updates the value, merges attributes and carries ``node_tag`` /
        ``xml_tag`` (an incoming non-None tag wins, mirroring
        ``set_item``). For new labels, adds the node with its tags. Unlike the
        legacy implementation, an unresolved resolver on a newly inserted node
        is retained; legacy accidentally inserted that node with a null value
        and discarded its resolver.

        Args:
            source: A Bag or dict to merge from.
            resolved: Resolve incoming resolver values before copying.
            ignore_none: If True, don't overwrite existing values with None.
            preserve_pattern: Compiled regex protecting matching current strings.
            ignoreNone: Legacy spelling of ``ignore_none``.
            preservePattern: Compiled pattern protecting matching strings.

        Example:
            >>> bag = Bag({'a': 1, 'b': 2})
            >>> bag.update({'a': 10, 'c': 3})
            >>> bag['a'], bag['b'], bag['c']
            (10, 2, 3)
        """
        if ignoreNone is not None:
            if ignore_none is not False and ignore_none != ignoreNone:
                raise TypeError("conflicting ignore_none and ignoreNone values")
            ignore_none = ignoreNone
        if preservePattern is not None:
            if preserve_pattern is not None and preserve_pattern is not preservePattern:
                raise TypeError("conflicting preserve_pattern and preservePattern values")
            preserve_pattern = preservePattern

        def updatable(value: Any) -> bool:
            return not (
                preserve_pattern is not None
                and isinstance(value, str)
                and preserve_pattern.search(value) is not None
            )

        if isinstance(source, str):
            source = self.__class__()._load_source(source)

        if isinstance(source, Mapping):
            for label, value in source.items():
                current = self.get_item(label, default=None, static=True)
                if (not ignore_none or value is not None) and updatable(current):
                    self.set_item(label, value)
            return

        for incoming in list(source):
            label = incoming.label
            resolver = incoming.resolver
            value = incoming.get_value(static=False) if resolved else incoming.static_value
            copied_resolver = None if resolved else resolver
            attr = dict(incoming.attr)
            node_tag = incoming.node_tag
            xml_tag = incoming.xml_tag
            if label in self._nodes:
                curr_node = self._nodes[label]
                attrs_to_update = {
                    attr_name: attr_value
                    for attr_name, attr_value in attr.items()
                    if updatable(curr_node.attr.get(attr_name))
                }
                if node_tag is not None:
                    curr_node.node_tag = node_tag
                if xml_tag is not None:
                    curr_node.xml_tag = xml_tag
                curr_value = curr_node.static_value
                if copied_resolver is not None:
                    if (not ignore_none or value is not None) and updatable(curr_value):
                        curr_node.set_value(
                            value,
                            _attributes=attrs_to_update,
                            _updattr=True,
                            _remove_null_attributes=False,
                        )
                    else:
                        curr_node.attr.update(attrs_to_update)
                    curr_node.resolver = copied_resolver
                elif safe_is_instance(value, _IS_BAG) and safe_is_instance(curr_value, _IS_BAG):
                    curr_node.attr.update(attrs_to_update)
                    curr_node.resolver = None
                    curr_value.update(
                        value,
                        resolved,
                        ignore_none,
                        preserve_pattern,
                    )
                else:
                    if (not ignore_none or value is not None) and updatable(curr_value):
                        curr_node.resolver = None
                        curr_node.set_value(
                            value,
                            _attributes=attrs_to_update,
                            _updattr=True,
                            _remove_null_attributes=False,
                        )
                    else:
                        curr_node.attr.update(attrs_to_update)
            else:
                new_node = self.set_item(
                    label,
                    value,
                    _attributes=attr,
                    _remove_null_attributes=False,
                    resolver=copied_resolver,
                    node_tag=node_tag,
                )
                if xml_tag is not None:
                    new_node.xml_tag = xml_tag
