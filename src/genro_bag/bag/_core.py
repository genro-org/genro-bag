# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Bag module - main container class.

The Bag is the core hierarchical data container of the Genro framework.
It provides an ordered, dict-like container where elements are BagNodes,
each with a label, value, and optional attributes.

Key features:
    - Hierarchical access via dot-separated paths: bag['a.b.c']
    - Ordered storage: elements maintain insertion order
    - No duplicate labels (unlike original gnrbag)
    - Backref mode for strict tree structure with parent references
    - Event subscription system for change notifications

Example:
    >>> bag = Bag()
    >>> bag['config.database.host'] = 'localhost'
    >>> bag['config.database.port'] = 5432
    >>> print(bag['config.database.host'])
    localhost

Resolver access is synchronous, including inside an active event loop.
Use ``static=True`` to inspect cached values without triggering a resolver.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from genro_bag._camel_names import BagNamesMixin
from genro_bag.bag._events import BagEvents, _current_transaction
from genro_bag.bag._exceptions import (  # noqa: F401 — re-export
    BagException,
    BagSerializationError,
)
from genro_bag.bag._parse import BagParser
from genro_bag.bag._populate import BagPopulate
from genro_bag.bag._query import BagQuery
from genro_bag.bag._repr import BagRepr
from genro_bag.bag._serialize import BagSerializer
from genro_bag.bag._traverse import BagTraverse
from genro_bag.bagnode import BagNode, BagNodeContainer
from genro_bag.resolver import BagCbResolver


class Bag(BagNamesMixin, BagPopulate, BagTraverse, BagEvents, BagRepr, BagParser, BagSerializer, BagQuery):
    """Hierarchical data container with path-based access.

    A Bag is an ordered container of BagNodes, accessible by label, numeric index,
    or hierarchical path. Nested elements can be accessed with dot-separated paths
    like 'a.b.c'.

    Unlike the original gnrbag, this implementation does NOT support duplicate labels.
    Each label at a given level must be unique.

    Inherits from:
        BagParser: Provides from_xml, from_tytx, from_json classmethods.
        BagSerializer: Provides to_xml, to_tytx, to_json instance methods.
        BagQuery: Provides query, digest, for_each, traverse, keys, values, items, sum, sort methods.

    Attributes:
        _nodes: BagNodeContainer holding the BagNodes.
        _backref: If True, enables strict tree mode with parent references.
        _parent: Reference to parent Bag (only in backref mode).
        _parent_node: Reference to the BagNode containing this Bag.
        _upd_subscribers: Callbacks for update events.
        _ins_subscribers: Callbacks for insert events.
        _del_subscribers: Callbacks for delete events.
        _root_attributes: Attributes for the root bag.
        _node_class: Factory class for creating BagNode instances. Subclasses
            can override this to use custom node types. Internal infrastructure
            attribute (underscore prefix); subclasses set it explicitly while
            user code never reads it.
        _container_class: Factory class for creating the internal
            BagNodeContainer. Same convention as _node_class: subclasses may
            override to inject a custom container, user code does not touch it.
        __tytx_suffix__: TYTX type suffix ("X", Bag's historical GenroPy
            datatype). Read by genro_tytx.register_class together with
            to_tytx()/from_tytx(), so a Bag nested inside a plain dict or list
            value travels on the wire as "<payload>::X". The registration call
            lives in genro_bag/__init__.py.
    """

    _node_class: type[BagNode] = BagNode
    _container_class: type[BagNodeContainer] = BagNodeContainer
    __tytx_suffix__ = "X"

    def __init__(self, source: dict[str, Any] | list | None = None, *, recursive: bool = True):
        """Create a new Bag.

        Args:
            source: A dict, list, or None. Keys are literal string labels.
            recursive: Convert nested dicts/lists to Bags; leaves remain shared.

        Example:
            >>> bag = Bag({'a': 1, 'b': 2})
            >>> bag['a']
            1
        """
        self._nodes: BagNodeContainer = self._container_class()
        self._backref: bool | str = False
        self._parent: Bag | None = None
        self._parent_node: BagNode | None = None
        self._upd_subscribers: dict = {}
        self._ins_subscribers: dict = {}
        self._del_subscribers: dict = {}
        self._txn_subscribers: dict = {}
        self._root_attributes: dict | None = None

        if not isinstance(recursive, bool):
            raise TypeError("recursive must be a bool")
        if source is not None:
            self.fill_from(source, recursive=recursive)

    # -------------------- transaction --------------------------------

    @contextmanager
    def transaction(self) -> Iterator[Bag]:
        """Group mutations into a single coalesced event.

        Inside the ``with`` block, granular subscribers (update/insert/delete
        and ``any``) are silenced: mutations are accumulated into a list on
        a module-level ContextVar. On exit, subscribers registered with
        ``subscribe(transaction=...)`` receive the ordered list.

        Each ``with`` (including nested ones) has its own list and emits
        its own event on exit; no propagation to an outer ``with``.

        If the body raises, no transaction event is emitted at this level;
        mutations already applied remain (no rollback).

        Yields:
            This bag itself, so ``as tx`` binds to it. Writing directly on
            the bag inside the block is equivalent to writing via ``tx``.
        """
        mutations: list = []
        token = _current_transaction.set(mutations)
        completed = False
        try:
            yield self
            completed = True
        finally:
            _current_transaction.reset(token)
            if completed and mutations:
                for sub in list(self._txn_subscribers.values()):
                    sub(bag=self, mutations=mutations)

    # -------------------- properties --------------------------------

    @property
    def parent(self) -> Bag | None:
        """Parent Bag in backref mode.

        Returns the parent Bag if this Bag is nested inside another and backref
        mode is enabled. Returns None for root Bags or when backref is disabled.
        """
        return self._parent

    @parent.setter
    def parent(self, value: Bag | None) -> None:
        self._parent = value

    @property
    def parent_node(self) -> BagNode | None:
        """The BagNode that contains this Bag.

        Returns the BagNode whose value is this Bag, or None if this is a
        standalone Bag not contained in any node.
        """
        return self._parent_node

    @parent_node.setter
    def parent_node(self, value: BagNode | None) -> None:
        self._parent_node = value

    @property
    def backref(self) -> bool:
        """Whether backref mode is enabled.

        In backref mode, Bags maintain references to their parent Bag and
        parent node, enabling tree traversal and event propagation up the
        hierarchy.
        """
        return bool(self._backref)

    @property
    def fullpath(self) -> str | None:
        """Full path from root Bag to this Bag.

        Returns the dot-separated path from the root of the hierarchy to this
        Bag. Returns None if backref mode is not enabled or if this is the root.
        """
        if self.parent is not None and self.parent_node is not None:
            parent_fullpath = self.parent.fullpath
            if parent_fullpath:
                return f"{parent_fullpath}.{self.parent_node.label}"
            else:
                return self.parent_node.label
        return None

    def relative_path(self, node: BagNode) -> str | None:
        """Get dot-separated path from this Bag to a descendant node.

        Walks up from the node to this Bag collecting labels.
        Requires backref mode enabled.

        Args:
            node: A BagNode that is a descendant of this Bag.

        Returns:
            The relative path string, or None if the node is not a descendant
            or backref is not enabled.
        """
        parts: list[str] = []
        current: BagNode | None = node
        while current is not None:
            if current.parent_bag is self:
                parts.append(current.label)
                parts.reverse()
                return ".".join(parts)
            parts.append(current.label)
            current = current.parent_node
        return None

    @property
    def root(self) -> Bag:
        """Get the root Bag of the hierarchy.

        Traverses parent chain until reaching a Bag with no parent.
        Returns self if this is already the root.
        """
        curr = self
        while curr.parent is not None:
            curr = curr.parent
        return curr

    @property
    def attributes(self) -> dict[str, Any]:
        """Attributes of the parent node containing this Bag.

        Returns the attributes dict of the BagNode that contains this Bag.
        Returns an empty dict if this is a standalone Bag with no parent node.
        """
        if self.parent_node is not None:
            return self.parent_node.get_attr()  # type: ignore[no-any-return]
        return {}

    @property
    def root_attributes(self) -> dict | None:
        """Attributes for the root Bag."""
        return self._root_attributes

    @root_attributes.setter
    def root_attributes(self, attrs: dict) -> None:
        self._root_attributes = dict(attrs)


    # -------------------- get (single level) --------------------------------

    def get(
        self, label: str, default: Any = None, static: bool = True, **kwargs: Any
    ) -> Any:
        """Get value at a single level (no path traversal).

        Unlike get_item/`__getitem__`, this method only looks at direct children
        of this Bag. It does not traverse paths with dots.

        Args:
            label: Node label to look up. Can be a string label or '#n' index.
                Supports '?attr' suffix to get a node attribute instead of value.
                Supports '?attr1&attr2' to get multiple attributes as tuple.
            default: Value to return if label not found.
            static: If True, don't trigger resolvers. Default True.
            **kwargs: Additional keyword arguments passed to the resolver.
                These override both resolver defaults and node attributes.

        Returns:
            The node's value if found, otherwise default.
            When using ?attr syntax, returns the attribute value.
            When using ?attr1&attr2 syntax, returns tuple of attribute values.

        Example:
            >>> bag = Bag()
            >>> bag['a'] = 1
            >>> bag.get('a')
            1
            >>> bag.get('missing', 'default')
            'default'
            >>> bag.set_item('x', 42, _attributes={'type': 'int', 'size': 4})
            >>> bag.get('x?type')  # get single attribute
            'int'
            >>> bag.get('x?type&size')  # get multiple attributes
            ('int', 4)
        """
        if "mode" in kwargs:
            mode = kwargs.pop("mode")
            static = isinstance(mode, str) and "static" in mode

        if not label:
            return self
        if label == "#parent":
            return self.parent
        _query_string = None
        if "?" in label:
            label, _query_string = label.split("?", 1)
        node = self._nodes.get(label)
        if not node:
            return default
        return node.get_value(static=static, _query_string=_query_string, **kwargs)

    # -------------------- get_item --------------------------------

    def get_item(
        self, path: str, default: Any = None, static: bool = False, **kwargs: Any
    ) -> Any:
        """Get value at a hierarchical path.

        Traverses the Bag hierarchy following the dot-separated path and returns
        the value at the final location.

        By default triggers resolvers (static=False).
        Use static=True to avoid triggering resolvers during traversal.

        Args:
            path: Hierarchical path like 'a.b.c'. Empty path returns self.
                Supports '?attr' suffix to get attribute instead of value.
            default: Value to return if path not found.
            static: If True, do not trigger resolvers during traversal. Default False.
            **kwargs: Additional keyword arguments passed to the resolver at the
                final path. These override both resolver defaults and node attributes.

        Returns:
            The value at the path if found, otherwise default.

        Example:
            >>> bag = Bag()
            >>> bag['config.db.host'] = 'localhost'
            >>> bag.get_item('config.db.host')
            'localhost'
            >>> # Check cache without triggering resolver:
            >>> cached = bag.get_item('path', static=True)
            >>> # Pass params to resolver:
            >>> result = bag.get_item('calc', a=10, b=20)
        """
        if not path:
            return self

        result = self._htraverse(path, static=static)

        def finalize(obj_label):
            obj, label = obj_label
            if isinstance(obj, Bag):
                return obj.get(label, default, static=static, **kwargs)
            return default

        return finalize(result)

    def __getitem__(self, path: str) -> Any:
        """Get value at path, triggering resolvers.

        Delegates to get_item with static=False (default).
        Use bag.get_item(path, static=True) to avoid triggering resolvers.
        """
        return self.get_item(path, static=False)

    # -------------------- set_item --------------------------------

    def set_item(
        self,
        path: str,
        value: Any,
        _attributes: dict | None = None,
        node_position: str | int | None = None,
        _updattr: bool = False,
        _remove_null_attributes: bool = True,
        _reason: str | None = None,
        _fired: bool = False,
        do_trigger: bool = True,
        resolver=None,
        node_tag: str | None = None,
        **kwargs,
    ) -> BagNode:
        """Set value at a hierarchical path.

        An empty path raises ValueError without modifying the Bag.

        Traverses the Bag hierarchy following the dot-separated path, creating
        intermediate Bags as needed, and sets the value at the final location.
        This is the method behind `bag[path] = value`.

        This method is synchronous and never triggers resolvers during traversal.

        If the path already exists, the value is updated. If it doesn't exist,
        a new node is created at the specified position.

        Resolver handling (Issue #5):
            If the target node has a resolver and the `resolver` parameter is not
            explicitly provided, a BagNodeException is raised. To modify a node
            with a resolver, you must explicitly handle the resolver:
            - resolver=False: Remove resolver and set value
            - resolver=NewResolver: Replace resolver with a new one

        Args:
            path: Non-empty hierarchical path like 'a.b.c'.
                Supports '?attr' suffix to set a node attribute instead of value.
                Supports '?attr1&attr2&attr3' to set multiple attributes at once
                (value must be a tuple with matching length).
            value: Value to set at the path. When using ?attr syntax, this is the
                attribute value. When using ?attr1&attr2 syntax, must be a tuple.
            _attributes: Optional dict of attributes to set on the node.
            node_position: Position for new nodes. Supports:
                - '>' or None: Append at end (default)
                - '<': Insert at beginning
                - int n: Insert at index n. Negative values count from the
                  end (Python-style: -1 = before last). Out-of-range values
                  are clamped to [0, len].
                - '#n': Insert at non-negative index n
                - '<label' / '>label': Insert before/after node with given label
                - '<#n' / '>#n': Insert before/after index n
            _updattr: If True, update attributes instead of replacing.
            _remove_null_attributes: If True, remove None attributes.
            _reason: Reason for the change (for events).
            _fired: If True, immediately reset value to None after setting.
                Used for event-like signals (like JavaScript fireItem).
            do_trigger: If True (default), fire events on change.
                Set to False to suppress ins/upd events.
            resolver: Resolver handling for existing nodes with resolvers:
                - None (default): Raise error if node has resolver
                - False: Remove existing resolver and set value
                - BagResolver instance: Replace resolver with new one
            node_tag: Optional semantic type for the node.
            **kwargs: Additional attributes to set on the node.

        Returns:
            The created or updated BagNode.

        Raises:
            ValueError: If path is the empty string.
            BagNodeException: If target node has a resolver and resolver param not provided.

        Example:
            >>> bag = Bag()
            >>> node = bag.set_item('a.b.c', 42)
            >>> node.value
            42
            >>> bag.set_item('a.b.d', 'hello', _attributes={'type': 'greeting'})
            >>> # Set a single attribute using ?attr syntax
            >>> bag.set_item('a.b.c?myattr', 'attr_value')
            >>> bag.get('a.b.c?myattr')  # 'attr_value'
            >>> # Set multiple attributes using ?attr1&attr2 syntax
            >>> bag.set_item('a.b.c?x&y&z', (1, 2, 3))
            >>> bag.get('a.b.c?x')  # 1
            >>> # Fire an event (set then immediately reset to None)
            >>> bag.set_item('event', 'click', _fired=True)
            >>> bag['event']  # None
            >>> # Handle nodes with resolvers
            >>> bag['data'] = BagCbResolver(lambda: 'computed')
            >>> bag.set_item('data', 'new', resolver=False)  # Remove resolver
        """
        if path == "":
            raise ValueError("set_item requires a non-empty path")

        # Merge kwargs into _attributes
        if kwargs:
            _attributes = dict(_attributes or {})
            _attributes.update(kwargs)

        # Traverse path (write_mode=True guarantees label is str)
        result, label = self._htraverse(path, write_mode=True)
        obj = result

        # Delegate EVERYTHING to BagNodeContainer.set
        return obj._nodes.set(  # type: ignore[no-any-return]
            label,
            value,
            node_position,
            attr=_attributes,
            resolver=resolver,
            parent_bag=obj,
            _updattr=_updattr,
            _remove_null_attributes=_remove_null_attributes,
            _reason=_reason,
            do_trigger=do_trigger,
            _fired=_fired,
            node_tag=node_tag,
        )

    def __setitem__(self, path: str, value: Any) -> None:
        """Set value at path using bracket notation."""
        self.set_item(path, value)

    # -------------------- _pop (single level) --------------------------------

    def _pop(self, label: str, _reason: str | None = None) -> BagNode | None:
        """Internal pop by label at current level.

        Args:
            label: Node label to remove.
            _reason: Reason for deletion (for events).

        Returns:
            The removed BagNode, or None if not found.
        """
        p = self._nodes.index(label)
        if p >= 0:
            node = self._nodes.pop(p)
            if self.backref:
                self._on_node_deleted(node, p, reason=_reason)
            return node
        return None

    # -------------------- pop --------------------------------

    def pop(self, path: str, default: Any = None, _reason: str | None = None) -> Any:
        """Remove a node and return its value.

        Traverses to the path, removes the node, and returns its value.
        This is the method behind `del bag[path]`.

        Args:
            path: Hierarchical path to the node to remove.
            default: Value to return if path not found.
            _reason: Reason for deletion (for events).

        Returns:
            The value of the removed node, or default if not found.

        Example:
            >>> bag = Bag()
            >>> bag['a.b'] = 42
            >>> bag.pop('a.b')
            42
            >>> bag.pop('a.b', 'gone')
            'gone'
        """
        node = self.pop_node(path, _reason=_reason)
        return node.value if node is not None else default

    del_item = pop
    __delitem__ = pop

    # -------------------- pop_node --------------------------------

    def pop_node(self, path: str, _reason: str | None = None) -> BagNode | None:
        """Remove and return the BagNode at a path.

        Like pop(), but returns the entire BagNode instead of just its value.
        Useful when you need access to the node's attributes after removal.

        Args:
            path: Hierarchical path to the node to remove.
            _reason: Reason for deletion (for events).

        Returns:
            The removed BagNode, or None if not found.

        Example:
            >>> bag = Bag()
            >>> bag.set_item('a', 42, _attributes={'type': 'int'})
            >>> node = bag.pop_node('a')
            >>> node.value
            42
            >>> node.attr
            {'type': 'int'}
        """
        obj, label = self._htraverse(path, static=True)
        if obj is None or not label:
            return None
        node = obj._nodes[label]
        if node is None:
            return None
        try:
            return obj._pop(label, _reason=_reason)
        finally:
            # Subscribers may fail, reinsert the node, or transfer ownership.
            if node.parent_bag is obj and obj._nodes._dict.get(node.label) is not node:
                node.parent_bag = None

    # -------------------- clear --------------------------------

    def clear(self, trigger: bool = True) -> None:
        """Remove all nodes from this Bag.

        Empties the Bag in place: self remains connected to its parent.

        When trigger is True (default) and the Bag is nested with backref,
        emits a single 'upd_value' event on self.parent_node. oldvalue is
        an orphan Bag holding the old nodes (detached from the tree,
        navigable, garbage-collected when no longer referenced).

        When trigger is False, clears in place without allocating an
        orphans Bag; removed nodes are detached. Use for bulk operations
        where no subscriber cares about the cleared content.

        Args:
            trigger: If True (default), emit upd_value event with oldvalue
                when nested with backref. If False, fast in-place clear
                without event notification.

        Example:
            >>> bag = Bag()
            >>> bag['a'] = 1
            >>> bag['b'] = 2
            >>> len(bag)
            2
            >>> bag.clear()
            >>> len(bag)
            0
        """
        if not len(self._nodes):
            return
        if not trigger or not (
            self.backref and self.parent is not None and self.parent_node is not None
        ):
            old_nodes = list(self._nodes)
            self._nodes.clear()
            for node in old_nodes:
                node.parent_bag = None
            return
        old_container = self._nodes
        self._nodes = self._container_class()
        orphans = self.__class__()
        orphans._nodes = old_container
        orphans.set_backref()
        self.parent._on_node_changed(
            self.parent_node,
            [self.parent_node.label],
            evt="upd_value",
            oldvalue=orphans,
        )

    def move(self, what: int | list[int], position: int, trigger: bool = True) -> None:
        """Move element(s) to a new position.

        Follows the same semantics as JavaScript moveNode:
        - If what is a list, all nodes at those indices are moved together
        - Nodes are removed in reverse order to preserve indices
        - All removed nodes are inserted at the target position
        - Events (del/ins) are fired for each node if trigger=True

        Args:
            what: Index or list of indices to move.
            position: Target index position.
            trigger: If True, fire del/ins events (default True).

        Example:
            >>> bag = Bag()
            >>> bag['a'] = 1
            >>> bag['b'] = 2
            >>> bag['c'] = 3
            >>> bag.move(0, 2)  # move 'a' to position 2
            >>> list(bag.keys())
            ['b', 'c', 'a']
            >>> bag.move([0, 2], 1)  # move indices 0 and 2 to position 1
        """
        self._nodes.move(what, position, trigger=trigger)

    def as_dict(
        self, ascii: bool = False, lower: bool = False,
        recursive: bool = False, exclude_null_values: bool = False,
    ) -> dict[str, Any]:
        """Convert Bag to dict, optionally converting nested Bags recursively.

        Existing ascii/lower positional arguments are preserved. Null exclusion
        removes only None values, retaining empty Bags, strings and containers.
        Ordinary dict/list values are left untouched. Attributes are not exported.
        """
        result = {}
        for el in self._nodes:
            value = el.value
            if exclude_null_values and value is None:
                continue
            key = el.label
            if ascii:
                key = str(key)
            if lower:
                key = key.lower()
            if recursive and isinstance(value, Bag):
                value = Bag.as_dict(value, ascii, lower, recursive, exclude_null_values)
            result[key] = value
        return result

    def setdefault(self, path: str, default: Any = None) -> Any:
        """Return value at path, setting it to default if not present."""
        node = self.get_node(path)
        if not node:
            self[path] = default
            return default
        return node.value  # type: ignore[union-attr]

    @property
    def nodes(self) -> list[BagNode]:
        """Property alias for get_nodes()."""
        return self.get_nodes()

    def node(self, key: str | int) -> BagNode | None:
        """Get a first-level node by label or index.

        Sync method for quick access to direct child nodes.
        Does not traverse paths or trigger resolvers.

        Args:
            key: Node label (str) or index (int).

        Returns:
            The BagNode if found, None otherwise.

        Example:
            >>> bag = Bag({'a': 1, 'b': 2})
            >>> bag.node('a').value
            1
            >>> bag.node(0).label
            'a'
        """
        return self._nodes[key]

    def set_attr(
        self,
        path: str | None = None,
        _attributes: dict | None = None,
        _remove_null_attributes: bool = True,
        **kwargs,
    ) -> None:
        """Set attributes on a node at the given path.

        Args:
            path: Path to the node. If None, uses parent_node.
            _attributes: Dict of attributes to set.
            _remove_null_attributes: If True, remove attributes with None value.
            **kwargs: Additional attributes to set.
        """
        self.get_node(path, autocreate=True).set_attr(  # type: ignore[union-attr]
            attr=_attributes, _remove_null_attributes=_remove_null_attributes, **kwargs
        )

    def get_attr(
        self, path: str | None = None, attr: str | None = None, default: Any = None
    ) -> Any:
        """Get an attribute from a node at the given path.

        Args:
            path: Path to the node. If None, uses parent_node.
            attr: Attribute name to get.
            default: Default value if node or attribute not found.

        Returns:
            Attribute value or default.
        """
        node = self.get_node(path)
        if node:
            return node.get_attr(label=attr, default=default)  # type: ignore[union-attr]
        return default

    def del_attr(self, path: str | None = None, *attrs: str) -> None:
        """Delete attributes from a node at the given path.

        Args:
            path: Path to the node. If None, uses parent_node.
            *attrs: Attribute names to delete.
        """
        node = self.get_node(path)
        if node:
            node.del_attr(*attrs)  # type: ignore[union-attr]

    def get_inherited_attributes(self) -> dict[str, Any]:
        """Get inherited attributes from parent chain.

        Returns:
            Dict of attributes inherited from parent nodes.
        """
        if self.parent_node:
            return self.parent_node.get_inherited_attributes()
        return {}

    # -------------------------------------------------------------------------
    # Resolver Methods
    # -------------------------------------------------------------------------

    def get_resolver(self, path: str):
        """Get the resolver at the given path.

        Args:
            path: Path to the node.

        Returns:
            The resolver, or None if path doesn't exist or has no resolver.
        """
        node = self.get_node(path)
        return node.resolver if node else None  # type: ignore[union-attr]

    def set_resolver(self, path: str, resolver) -> None:
        """Set a resolver at the given path.

        Creates the node if it doesn't exist, with value=None.

        Args:
            path: Path to the node.
            resolver: The resolver to set.
        """
        self.set_item(path, None, resolver=resolver)

    def set_callback_item(self, path: str, callback: Callable, **kwargs) -> None:
        """Set a callback resolver at the given path.

        Shortcut for creating a callback-based resolver and setting it on a
        node. Uses :class:`BagCbResolver` and requires a synchronous callback.

        Args:
            path: Path to the node.
            callback: Synchronous callable that returns the value.
            **kwargs: Arguments passed to the resolver constructor. Common
                kwargs:

                - cache_time: Cache duration in seconds (default 0, no cache).
                - read_only: If True, value not saved in node (default False).

        Note:
            The resolver is passed directly to set_item, which handles it
            via the resolver parameter (not as value).
        """
        resolver = BagCbResolver(callback, **kwargs)
        self.set_item(path, resolver)

    # -------------------- __iter__, __len__, __contains__, __call__ --------------------------------

    def __iter__(self):
        """Iterate over BagNodes.

        Yields BagNode objects in order, not values.

        Example:
            >>> bag = Bag()
            >>> bag['a'] = 1
            >>> for node in bag:
            ...     print(node.label, node.value)
            a 1
        """
        return iter(self._nodes)

    def __len__(self) -> int:
        """Return number of direct child nodes.

        Example:
            >>> bag = Bag()
            >>> bag['a'] = 1
            >>> bag['b.c'] = 2  # nested, but 'b' is one child
            >>> len(bag)
            2
        """
        return len(self._nodes)

    def __call__(self, what: str | None = None) -> Any:
        """Call syntax for quick access.

        Called with no argument, returns list of keys.
        Called with a path, returns value at that path.

        Args:
            what: Optional path to retrieve.

        Returns:
            List of keys if what is None, otherwise value at path.

        Example:
            >>> bag = Bag()
            >>> bag['a'] = 1
            >>> bag['b'] = 2
            >>> bag()
            ['a', 'b']
            >>> bag('a')
            1
        """
        if not what:
            return list(self.keys())
        return self[what]

    def __contains__(self, what: str) -> bool:
        """Check if a path or node exists in the Bag.

        The "in" operator can be used to test the existence of a key in a
        bag. Also nested keys are allowed. With the ``?attr`` query syntax
        it checks the existence of the named attribute on the target node;
        ``?a&b`` requires every named attribute to be present.

        Static check: ``in`` does not trigger resolvers along the path.

        Args:
            what: Path to check, optionally with ``?attr`` or ``?a&b`` suffix,
                or a BagNode to check if it's in this Bag.

        Returns:
            True if the path/node (and named attributes if provided) exists,
            False otherwise.

        Example:
            >>> bag = Bag()
            >>> bag['a.b'] = 1
            >>> 'a.b' in bag
            True
            >>> 'a.c' in bag
            False
        """
        if isinstance(what, str):
            _query_string = None
            if "?" in what:
                what, _query_string = what.split("?", 1)
            node = self.get_node(what, static=True)
            if node is None:
                return False
            if _query_string is None:
                return True
            return all(a in node._attr for a in _query_string.split("&"))
        elif isinstance(what, BagNode):
            return what in list(self._nodes)
        else:
            return False

    def __eq__(self, other: object) -> bool:
        """Check equality with another Bag.

        Two Bags are equal if they have the same nodes in the same order.
        This comparison delegates to BagNodeContainer.__eq__ which in turn
        compares BagNodes (label, attr, value/resolver).

        Args:
            other: Object to compare with.

        Returns:
            True if equal, False otherwise.
        """
        if not isinstance(other, Bag):
            return False
        return self._nodes == other._nodes

    def __ne__(self, other: object) -> bool:
        """Check inequality with another Bag."""
        return not self.__eq__(other)

    # -------------------- _get_node (single level) --------------------------------

    def _get_node(
        self, label: str, autocreate: bool = False, default: Any = None
    ) -> BagNode | None:
        """Internal get node by label at current level.

        Args:
            label: Node label to find.
            autocreate: If True, create node if not found.
            default: Default value for autocreated node.

        Returns:
            The BagNode, or None if not found and not autocreate.
        """
        p = self._nodes.index(label)
        node: BagNode | None
        if p >= 0:
            node = self._nodes[p]
        elif autocreate:
            i = len(self._nodes)
            node = self._nodes.set(label, default, parent_bag=self)
            if self.backref:
                self._on_node_inserted(node, i)
        else:
            node = None
        return node

    # -------------------- get_node --------------------------------

    def get_node(
        self,
        path: str | None = None,
        *,
        autocreate: bool = False,
        default: Any = None,
        static: bool = False,
    ) -> BagNode | None:
        """Get the BagNode at a path.

        Unlike get_item which returns the value, this returns the BagNode itself,
        giving access to attributes and other node properties.

        By default triggers resolvers (static=False).
        Use static=True to avoid triggering resolvers during traversal.

        Args:
            path: Hierarchical path. If None or empty, returns the parent_node
                (the node containing this Bag). Can also be an integer index.
            autocreate: If True, create node if not found.
            default: Default value for autocreated node.
            static: If True, do not trigger resolvers during traversal. Default False.

        Returns:
            The BagNode at the path, or None if not found.

        Example:
            >>> bag = Bag()
            >>> bag.set_item('a', 42, _attributes={'type': 'int'})
            >>> node = bag.get_node('a')
            >>> node.value
            42
            >>> node.attr['type']
            'int'
        """
        if path is None or path == "":
            return self.parent_node

        if isinstance(path, int):
            return self._nodes[path]

        result = self._htraverse(path, write_mode=autocreate, static=static)

        def finalize(obj_label):
            obj, label = obj_label
            if isinstance(obj, Bag):
                node = obj._get_node(label, autocreate, default)
                return node
            return None

        return finalize(result)  # type: ignore[no-any-return]

    # -------------------- backref management --------------------------------

    def set_backref(self, node: BagNode | None = None, parent: Bag | None = None) -> None:
        """Force a Bag to a more strict structure (tree-leaf model).

        Enables backref mode which maintains parent references and
        propagates events up the hierarchy.

        Args:
            node: The BagNode that contains this Bag.
            parent: The parent Bag.
        """
        self.parent = parent
        self.parent_node = node
        if self._backref is not True:
            self._backref = True
            self._nodes._parent_bag = self
            for node in self:
                node.parent_bag = self

    def del_parent_ref(self) -> None:
        """Set False in the parent Bag reference of the relative Bag."""
        self.parent = None
        self.parent_node = None
        self._backref = False

    def clear_backref(self) -> None:
        """Clear all the set_backref() assumption."""
        if self._backref:
            self._backref = False
            self.parent = None
            self.parent_node = None
            self._nodes._parent_bag = None
            for node in self:
                node.parent_bag = None
                value = node.get_value(static=True)
                if isinstance(value, Bag):
                    value.clear_backref()
