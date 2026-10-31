# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""BagNode module - individual nodes and container for the Bag hierarchy.

This module provides:
    - BagNode: represents a single node in a Bag hierarchy
    - BagNodeContainer: ordered container for BagNodes with positional insert
    - BagNodeException: exception for node operations

Key Features:
    - Dual relationship: node.parent_bag → Bag, Bag.parent_node → node
    - Optional tag for semantic typing
    - Resolver support for lazy/dynamic value computation
    - Per-node subscriptions for change notifications
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from genro_toolbox import safe_is_instance, smartsplit
from genro_tytx import from_tytx

from genro_bag._camel_names import BagNodeNamesMixin

if TYPE_CHECKING:
    from .bag import Bag
    from .resolver import BagResolver

# Type alias for node subscriber callbacks
NodeSubscriberCallback = Callable[..., None]


class BagNodeException(Exception):
    """Exception raised by BagNode operations."""

    pass


class BagNode(BagNodeNamesMixin):
    """BagNode is the element type which a Bag is composed of.

    A BagNode gathers within itself three main things:
    - *label*: can be only a string
    - *value*: can be anything, even a Bag for hierarchical structure
    - *attributes*: dictionary that contains node's metadata

    Attributes:
        label: The node's unique name/key within its parent.
        node_tag: Optional type/tag for the node (semantic typing).

    Internal Attributes (via __slots__):
        _value: The node's actual value storage.
        _attr: Dictionary of node attributes/metadata.
        _parent_bag: Reference to the parent Bag containing this node.
        _resolver: Optional BagResolver for lazy/dynamic value computation.
        _node_subscribers: Dict mapping subscriber_id to callback for change notifications.
        node_tag: Optional semantic type for the node.
        xml_tag: Original XML tag name for serialization.
        _compiled: Dict for external compilation data. Initialized lazily
            on first access via the `compiled` property.
    """

    __slots__ = (
        "label",
        "_value",
        "_attr",
        "_parent_bag",
        "_resolver",
        "_node_subscribers",
        "node_tag",
        "xml_tag",
        "_compiled",
    )

    def __init__(
        self,
        parent_bag: Bag | None,
        label: str,
        value: Any = None,
        attr: dict[str, Any] | None = None,
        resolver: BagResolver | None = None,
        node_tag: str | None = None,
        xml_tag: str | None = None,
        _remove_null_attributes: bool = True,
    ) -> None:
        """Initialize a BagNode.

        Args:
            parent_bag: The parent Bag containing this node.
            label: The node's key/name within the parent Bag.
            value: The node's value (can be scalar or Bag).
            attr: Dict of attributes to set via set_attr() (with processing).
            resolver: A BagResolver for lazy/dynamic value loading.
            node_tag: Optional semantic type for the node.
            xml_tag: Original XML tag name (used for XML serialization).
            _remove_null_attributes: If True, remove None values from attributes.
        """
        # Basic node identity
        self.label = label
        self._value: Any = None
        self._parent_bag: Bag | None = None
        self._resolver: BagResolver | None = None
        self._node_subscribers: dict[str, NodeSubscriberCallback] = {}
        self._attr: dict[str, Any] = {}
        self.node_tag = node_tag
        self.xml_tag = xml_tag
        self._compiled: dict[str, Any] | None = None

        # Set parent (uses property setter)
        self.parent_bag = parent_bag

        # Set resolver if provided (uses property setter for bidirectional link)
        if resolver is not None:
            self.resolver = resolver

        # Process attributes via set_attr
        if attr:
            self.set_attr(attr, trigger=False, _remove_null_attributes=_remove_null_attributes)

        # Process value via set_value
        if value is not None:
            self.set_value(value, trigger=False)

    def __eq__(self, other: object) -> bool:
        """One BagNode is equal to another if label, attr and value/resolver match."""
        try:
            if (
                isinstance(other, BagNode)
                and (self.label == other.label)
                and (self._attr == other._attr)
            ):
                if self._resolver is None:
                    return self._value == other._value  # type: ignore[no-any-return]
                else:
                    return self._resolver == other._resolver  # type: ignore[no-any-return]
            return False
        except Exception:
            return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __str__(self) -> str:
        return f"BagNode : {self.label}"

    def __repr__(self) -> str:
        return f"BagNode : {self.label} at {id(self)}"

    def __getstate__(self) -> dict[str, Any]:
        """Return detached pickle state without live parent or subscribers."""
        state: dict[str, Any] = {}
        for cls in type(self).__mro__:
            slots = cls.__dict__.get("__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            for name in slots:
                if name not in {"__dict__", "__weakref__"} and hasattr(self, name):
                    state[name] = getattr(self, name)
        instance_dict = getattr(self, "__dict__", None)
        if instance_dict is not None:
            state["__dict__"] = dict(instance_dict)
        state["_parent_bag"] = None
        state["_node_subscribers"] = {}
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore slot and optional subclass dictionary state."""
        instance_dict = state.pop("__dict__", None)
        for name, value in state.items():
            setattr(self, name, value)
        if instance_dict is not None:
            self.__dict__.update(instance_dict)


    # -------------------------------------------------------------------------
    # Parent Bag Property
    # -------------------------------------------------------------------------

    @property
    def parent_bag(self) -> Bag | None:
        """Get the parent Bag containing this node."""
        return self._parent_bag

    @parent_bag.setter
    def parent_bag(self, parent_bag: Bag | None) -> None:
        """Set the parent Bag, handling backref setup if needed.

        If the node's value is a Bag and the parent has backref=True,
        establishes the bidirectional parent-child relationship via set_backref().
        """
        if parent_bag is None and getattr(self._value, "parent_node", None) is self:
            self._value.set_backref()
        self._parent_bag = None
        if parent_bag is not None:
            self._parent_bag = parent_bag
            if hasattr(self._value, "_htraverse") and parent_bag.backref:
                self._value.set_backref(node=self, parent=parent_bag)

    def orphaned(self) -> BagNode:
        """Detach this node from its parent bag, clearing backref recursively.

        Mirrors the legacy JS BagNode.orphaned() helper:
        - clears _parent_bag (no back-pointer to the previous parent)
        - if the node's value is a Bag, clears backref on the whole subtree
          so nested events no longer bubble upward through the old tree

        Use when a node is being moved out of its container (e.g. during
        fill_from swap) and must not keep residual references to its former
        parent.

        Returns:
            Self, for chaining.
        """
        self._parent_bag = None
        if hasattr(self._value, "_htraverse"):
            self._value.clear_backref()
        return self

    @property
    def _(self) -> Bag:
        """Return parent Bag for navigation/chaining.

        Example:
            >>> node._.set_item('sibling', 'value')  # add sibling
        """
        if self._parent_bag is None:
            raise ValueError("Node has no parent")
        return self._parent_bag

    # -------------------------------------------------------------------------
    # Value Property and Methods
    # -------------------------------------------------------------------------

    @property
    def value(self) -> Any:
        """Get the node's value, resolving if a resolver is set."""
        return self.get_value()

    @value.setter
    def value(self, value: Any) -> None:
        """Set the node's value."""
        self.set_value(value)

    def get_value(
        self, static: bool = False, _query_string: str | None = None, **kwargs: Any
    ) -> Any:
        """Return the value of the BagNode.

        Args:
            static: If True, do not trigger any resolver — the node's own
                resolver is skipped and attribute-held BagResolvers are
                returned raw (as objects) in the ?attr / ? forms.
            _query_string: Optional query string from path suffix (after '?').
                - None: return node value (possibly via resolver)
                - 'attr': return single attribute value; if it is a
                  BagResolver, it is called and its result is returned
                  (unless static=True).
                - 'attr1&attr2': return tuple of attribute values, with
                  the same resolver-resolution rule applied element-wise.
                - '' (bare '?'): return a dict of ALL attributes,
                  {name: value}, with BagResolvers resolved (unless
                  static=True). No filtering — every attribute in
                  ``node.attr`` is included.
                - 'key=val&key2=val2': pass as kwargs to the node's own
                  resolver (requires ``self._resolver``).
                Values can include type suffix: 'price=34::F' for float
                conversion.
            **kwargs: Parameters passed to the node's own resolver for this
                call only (one-off read: no cache, node.attr untouched).

        Returns:
            The node's value, possibly resolved via resolver.

        Parameter Priority (when the node has ``self._resolver``):
            1. kwargs passed here (highest priority)
            2. node.attr (attributes set on this node)
            3. resolver._kw (resolver's default parameters)

        Note:
            The parameter-merge chain above applies **only** to the
            node's own value-side resolver. A BagResolver held as an
            attribute (e.g. ``set_attr(key=EnvResolver(...))``) is called
            bare, with no kwargs and no context — its own defaults win.

        Example:
            # Resolver with default multiplier=2
            node.resolver = CalcResolver(base=10, multiplier=2)

            node.get_value()  # uses multiplier=2 from resolver
            node.set_attr(multiplier=5)
            node.get_value()  # uses multiplier=5 from node.attr
            node.get_value(multiplier=10)  # uses multiplier=10 from kwargs
        """
        if _query_string is not None:
            parsed_qs = from_tytx(f"{_query_string}::QS")
            if isinstance(parsed_qs, list):
                # ?a or ?a&b (named keys) or ? (bare, empty list -> all attrs)
                keys = parsed_qs if parsed_qs else list(self._attr.keys())
                resolved: dict[str, Any] = {}
                for k in keys:
                    v = self._attr.get(k)
                    if not static and safe_is_instance(
                        v, "genro_bag.resolver.BagResolver"
                    ):
                        v = v()
                    resolved[k] = v
                if not parsed_qs:
                    return resolved                             # bare '?' -> dict
                if len(keys) == 1:
                    return resolved[keys[0]]                    # ?a -> scalar
                return tuple(resolved[k] for k in keys)         # ?a&b -> tuple
            else:
                # Dict → kwargs for resolver
                if not self._resolver:
                    raise BagNodeException("Cannot use kwargs syntax without resolver")
                kwargs.update(parsed_qs)

        if self._resolver is not None:
            return self._resolver(static=static, **kwargs)
        return self._value

    def set_value(
        self,
        value: Any,
        trigger: bool = True,
        _attributes: dict[str, Any] | None = None,
        _updattr: bool | None = None,
        _remove_null_attributes: bool = True,
        _reason: str | None = None,
        _fired: bool = False,
    ) -> None:
        """Set the node's value.

        Args:
            value: The value to set.
            trigger: If True, notify subscribers of the change.
            _attributes: Optional attributes to set along with value.
            _updattr: If False, clear existing attributes first.
            _remove_null_attributes: If True, remove None values from attributes.
            _reason: Optional reason string for the trigger.
            _fired: True when the write is a fired write; carried as
                ``fired`` in the Bag change event. The value is not reset here.

        Special value handling:
            - BagResolver: Assigned to self.resolver, value set to None.
            - BagNode: Extracts value and merges attributes from the node.
            - Objects with rootattributes: Merges rootattributes into _attributes.

        Note:
            Parameters prefixed with '_' are for internal/advanced use.
            The prefix avoids conflicts with user-defined node attributes.
        """
        # Handle BagResolver passed as value (use safe_is_instance to avoid circular import)
        if safe_is_instance(value, "genro_bag.resolver.BagResolver"):
            self.resolver = value
            value = None
        # Handle BagNode passed as value - extract its value and attrs
        elif safe_is_instance(value, "genro_bag.bagnode.BagNode"):
            _attributes = _attributes or {}
            _attributes.update(value._attr)
            value = value._value

        # Read real instance/class attributes without invoking a dynamic
        # __getattr__ fallback on arbitrary values.
        try:
            rootattributes = object.__getattribute__(value, "rootattributes")
        except AttributeError:
            pass
        else:
            if isinstance(rootattributes, dict) and rootattributes:
                _attributes = dict(_attributes or {})
                _attributes.update(rootattributes)

        oldvalue = self._value
        if oldvalue is not value and getattr(oldvalue, "parent_node", None) is self:
            oldvalue.set_backref()
        self._value = value

        value_changed = oldvalue != self._value
        attrs_diff: dict[str, dict[str, Any]] | None = None

        if _attributes is not None:
            oldattr_snapshot = dict(self._attr)
            # Apply attributes silently; emit one event for the actual changes.
            self.set_attr(
                _attributes,
                trigger=False,
                _updattr=_updattr,
                _remove_null_attributes=_remove_null_attributes,
            )
            attrs_diff = self._build_attr_diff(oldattr_snapshot, self._attr) or None

        trigger = trigger and (value_changed or attrs_diff is not None)
        evt = ("upd_value_attr" if value_changed else "upd_attrs") if attrs_diff else "upd_value"

        if trigger:
            if attrs_diff is not None:
                info = {"oldvalue": oldvalue, "attrs_diff": attrs_diff}
            else:
                info = {"oldvalue": oldvalue}
            for subscriber in self._node_subscribers.values():
                subscriber(node=self, info=info, evt=evt)

        if self._parent_bag is not None and self._parent_bag.backref:
            if hasattr(value, "_htraverse"):
                value.set_backref(node=self, parent=self._parent_bag)
            if trigger:
                self._parent_bag._on_node_changed(
                    self, [self.label],
                    evt=evt,
                    oldvalue=oldvalue,
                    attrs_diff=attrs_diff,
                    reason=_reason,
                    fired=_fired,
                )

    def replace(self, other: BagNode) -> BagNode:
        """Copy value, attributes and resolver; keep identity, label and position."""
        if not isinstance(other, BagNode):
            raise TypeError("BagNode.replace expects a BagNode")
        if other is self:
            return self
        value = other.static_value
        if safe_is_instance(value, "genro_bag.bag._core.Bag"):
            value = value.__class__().replace(value)
        memo = {id(other): None}
        resolver = copy.deepcopy(other.resolver, memo)
        attributes = dict(other.attr)
        oldvalue, oldattr, oldresolver = self._value, dict(self._attr), self.resolver
        self.resolver = resolver
        self.set_value(value, trigger=False, _attributes=attributes,
                       _updattr=False, _remove_null_attributes=False)
        diff = self._build_attr_diff(oldattr, self._attr) or None
        value_changed = oldvalue != value or oldresolver is not resolver
        if value_changed or diff:
            evt = ("upd_value_attr" if value_changed else "upd_attrs") if diff else "upd_value"
            info = {"oldvalue": oldvalue}
            if diff:
                info["attrs_diff"] = diff
            for callback in self._node_subscribers.values():
                callback(node=self, info=info, evt=evt)
            if self._parent_bag is not None and self._parent_bag.backref:
                self._parent_bag._on_node_changed(
                    self, [self.label], evt=evt, oldvalue=oldvalue, attrs_diff=diff,
                )
        return self

    @property
    def static_value(self) -> Any:
        """Get node's raw _value (bypassing resolver)."""
        return self._value

    @static_value.setter
    def static_value(self, value: Any) -> None:
        """Set node's _value directly, bypassing set_value processing and triggers.

        Note: This does NOT remove or affect the resolver. It only sets _value.
        """
        self._value = value

    # -------------------------------------------------------------------------
    # Resolver Property
    # -------------------------------------------------------------------------

    @property
    def resolver(self) -> BagResolver | None:
        """Get the node's resolver."""
        return self._resolver

    @resolver.setter
    def resolver(self, resolver: BagResolver | None) -> None:
        """Set the node's resolver, establishing bidirectional link.

        The resolver reads parameters from three sources with priority:
        1. call_kwargs passed to resolver() (one-off read, never cached)
        2. node attributes (set via set_attr), declared parameters only
        3. resolver default parameters (_kw)

        Cache is invalidated when a declared parameter changes via set_attr.
        """
        if self._resolver is not None and self._resolver is not resolver:
            self._resolver.parent_node = None
        if resolver is not None:
            resolver.parent_node = self
        self._resolver = resolver

    def reset_resolver(self) -> None:
        """Reset the resolver and clear the value; reject nodes without a resolver."""
        if self._resolver is None:
            raise ValueError("Cannot reset resolver: node has no resolver")
        self._resolver.reset()
        self.set_value(None)

    # -------------------------------------------------------------------------
    # Compiled Property
    # -------------------------------------------------------------------------

    @property
    def compiled(self) -> dict[str, Any]:
        """Get compilation data dict (lazy initialization).

        Returns a dict for external use (compilation data, metadata, etc.).
        Created on first access.
        """
        if self._compiled is None:
            self._compiled = {}
        return self._compiled

    # -------------------------------------------------------------------------
    # Attribute Methods
    # -------------------------------------------------------------------------

    @property
    def attr(self) -> dict[str, Any]:
        """Get all attributes as a dictionary."""
        return self._attr

    def get_attr(self, label: str | None = None, default: Any = None) -> Any:
        """Get attribute value or all attributes.

        Args:
            label: The attribute's label. If None or '#', returns all attributes.
            default: Default value if attribute not found.

        Returns:
            Attribute value, default, or dict of all attributes.
        """
        if not label or label == "#":
            return self._attr
        return self._attr.get(label, default)

    def _build_attr_diff(
        self,
        oldattr: dict[str, Any],
        newattr: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Compute the symmetric diff between two attribute snapshots.

        Returns a dict mapping each changed key to {"old": <prev>, "new": <curr>},
        covering added (old=None), removed (new=None) and modified entries.
        Keys whose value did not change are omitted.
        """
        diff: dict[str, dict[str, Any]] = {}
        for key in set(oldattr) | set(newattr):
            old_val = oldattr.get(key)
            new_val = newattr.get(key)
            if old_val != new_val:
                diff[key] = {"old": old_val, "new": new_val}
        return diff

    def set_attr(
        self,
        attr: dict[str, Any] | None = None,
        trigger: bool = True,
        _updattr: bool | None = True,
        _remove_null_attributes: bool = True,
        **kwargs: Any,
    ) -> None:
        """Set attributes on the node.

        Args:
            attr: Dictionary of attributes to set.
            trigger: If True, notify subscribers of the change.
            _updattr: If False, clear existing attributes first.
            _remove_null_attributes: If True, remove None values from attributes.
            **kwargs: Additional attributes as keyword arguments.

        Note:
            Parameters prefixed with '_' are for internal/advanced use.
            The prefix avoids conflicts with user-defined node attributes.
        """
        new_attr = (attr or {}) | kwargs

        # Invalidate resolver cache if a resolver parameter's effective value changes
        # Effective value = node.attr if set, else resolver._kw
        if self._resolver is not None and (
            self._resolver.cache_time != 0
        ):
            resolver_kw = self._resolver._kw
            internal = self._resolver.internal_params
            for key, value in new_attr.items():
                if key in resolver_kw and key not in internal:
                    effective_old = self._attr.get(key, resolver_kw[key])
                    if effective_old != value:
                        self._resolver.reset()
                        break

        # Save old state BEFORE any modification (only if needed for subscribers)
        oldattr = dict(self._attr) if (trigger and (self._node_subscribers or
            (self._parent_bag is not None and self._parent_bag.backref))) else None

        if _updattr:
            self._attr.update(new_attr)
        else:
            self._attr = new_attr

        if _remove_null_attributes:
            self._attr = {k: v for k, v in self._attr.items() if v is not None}

        # Synchronous refresh must see the new attributes, not the previous inputs.

        if trigger and oldattr is not None:
            diff = self._build_attr_diff(oldattr, self._attr)

            if diff and self._node_subscribers:
                info = {"attrs_diff": diff}
                for subscriber in self._node_subscribers.values():
                    subscriber(node=self, info=info, evt="upd_attrs")

            if diff and self._parent_bag is not None and self._parent_bag.backref:
                reason = str(trigger) if trigger is True else trigger
                self._parent_bag._on_node_changed(
                    self, [self.label], evt="upd_attrs",
                    attrs_diff=diff,
                    reason=reason
                )

    def del_attr(self, *attrs_to_delete: str) -> None:
        """Remove attributes from the node.

        Args:
            *attrs_to_delete: Attribute labels to remove. Each can be a single
                label or a comma-separated string of labels (e.g., 'a,b,c').
        """
        for attr in attrs_to_delete:
            if isinstance(attr, str) and "," in attr:
                # Handle comma-separated string
                for a in attr.split(","):
                    self._attr.pop(a.strip(), None)
            else:
                self._attr.pop(attr, None)

    def has_attr(self, label: str, value: Any = None) -> bool:
        """Check if a node has the given attribute.

        Args:
            label: Attribute label to check.
            value: If provided, also check if attribute has this value.

        Returns:
            True if attribute exists (and matches value if provided).
        """
        if label not in self._attr:
            return False
        if value is not None:
            return self._attr[label] == value  # type: ignore[no-any-return]
        return True

    # -------------------------------------------------------------------------
    # Navigation Properties
    # -------------------------------------------------------------------------

    @property
    def position(self) -> int | None:
        """Get this node's index position within parent Bag.

        Returns:
            The 0-based index of this node in the parent's node list,
            or None if this node has no parent.
        """
        if self.parent_bag is None:
            return None
        return self.parent_bag._nodes.index(self.label)

    @property
    def fullpath(self) -> str | None:
        """Get dot-separated path from root to this node."""
        if self.parent_bag is not None:
            fullpath = self.parent_bag.fullpath
            return f"{fullpath}.{self.label}" if fullpath else self.label
        return None

    @property
    def parent_node(self) -> BagNode | None:
        """Get the node that contains this node's parent Bag.

        In the hierarchy: grandparent_bag contains parent_node, whose value
        is parent_bag, which contains this node.
        """
        if self.parent_bag is not None:
            return self.parent_bag.parent_node
        return None

    def get_inherited_attributes(self) -> dict[str, Any]:
        """Get attributes inherited from ancestors.

        Returns:
            Dict with all inherited attributes merged with this node's attributes.
        """
        inherited: dict[str, Any] = {}
        if self.parent_bag is not None and self.parent_bag.parent_node is not None:
            inherited = self.parent_bag.parent_node.get_inherited_attributes()
        inherited.update(self._attr)
        return inherited

    def attribute_owner_node(
        self,
        attrname: str,
        attrvalue: Any = None,
    ) -> BagNode | None:
        """Find the ancestor node that owns a given attribute.

        Args:
            attrname: Attribute name to search for.
            attrvalue: If provided, also match this value.

        Returns:
            The node that owns the attribute, or None.
        """
        curr: BagNode | None = self
        if attrvalue is None:
            while curr and (attrname not in curr._attr):
                curr = curr.parent_node
        else:
            while curr and curr._attr.get(attrname) != attrvalue:
                curr = curr.parent_node
        return curr

    # -------------------------------------------------------------------------
    # Subscription Methods
    # -------------------------------------------------------------------------

    def subscribe(
        self,
        subscriber_id: str | None = None,
        callback: NodeSubscriberCallback | None = None,
        *,
        subscriberId: str | None = None,
    ) -> None:  # noqa: N803
        """Subscribe to changes on this specific node.

        Args:
            subscriber_id: Unique identifier for this subscription.
            callback: Function to call on changes.

        Callback signature:
            callback(node, info, evt)
            - node: This BagNode
            - info: oldvalue (for 'upd_value') or list of changed attrs
            - evt: Event type ('upd_value' or 'upd_attrs')
        """
        if subscriber_id is not None and subscriberId is not None:
            raise TypeError("pass only one of subscriber_id and subscriberId")
        subscriber_id = subscriber_id if subscriber_id is not None else subscriberId
        if subscriber_id is None or callback is None:
            raise TypeError("subscriber id and callback are required")
        self._node_subscribers[subscriber_id] = callback

    def unsubscribe(
        self,
        subscriber_id: str | None = None,
        *,
        subscriberId: str | None = None,
    ) -> None:  # noqa: N803
        """Unsubscribe from changes on this node.

        Args:
            subscriber_id: The subscription identifier to remove.
        """
        if subscriber_id is not None and subscriberId is not None:
            raise TypeError("pass only one of subscriber_id and subscriberId")
        subscriber_id = subscriber_id if subscriber_id is not None else subscriberId
        if subscriber_id is None:
            raise TypeError("subscriber id is required")
        self._node_subscribers.pop(subscriber_id, None)

    @property
    def is_branch(self) -> bool:
        """Check if this node's value is a Bag (branch node).

        Returns:
            True if the node's value is a Bag, False otherwise.
        """
        return safe_is_instance(self._value, "genro_bag.bag._core.Bag")

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def diff(self, other: BagNode) -> str | None:
        """Compare this node with another and return differences.

        Args:
            other: Another BagNode to compare with.

        Returns:
            Description of differences, or None if equal.
        """
        if self.label != other.label:
            return f"Other label: {other.label}"
        if self._attr != other._attr:
            return f"attributes self:{self._attr} --- other:{other._attr}"
        if self._value != other._value:
            return f"value self:{self._value} --- other:{other._value}"
        return None

    def as_tuple(self) -> tuple[str, Any, dict[str, Any], BagResolver | None]:
        """Return node data as a tuple.

        Returns:
            Tuple of (label, value, attr, resolver).
        """
        return (self.label, self.value, self._attr, self._resolver)

    def to_json(self, typed: bool = True) -> dict[str, Any]:
        """Convert node to JSON-serializable dict.

        Args:
            typed: If True, include type information.

        Returns:
            Dict with keys 'label', 'value', and 'attr'.
        """
        value = self.value
        if hasattr(value, "to_json"):
            value = value.to_json(typed=typed, nested=True)
        return {"label": self.label, "value": value, "attr": self._attr}


class BagNodeContainer:
    """Ordered container for BagNodes with positional insert and reordering.

    BagNodeContainer combines dict-like access with list-like ordering. Elements can be
    accessed by label, numeric index, or '#n' string index. Supports positional
    insertion and element reordering without removal.

    This class creates and manages BagNode instances directly.

    Internal structure:
        _dict: maps label -> BagNode (for O(1) lookup by label)
        _list: contains BagNodes in order (for O(1) access by index)
        _parent_bag: optional reference to parent Bag (set via set_backref)
    """

    def __init__(self):
        """Create an empty BagNodeContainer."""
        self._dict: dict[str, Any] = {}
        self._list: list[Any] = []
        self._parent_bag: Bag | None = None

    def index(self, label: str) -> int:
        """Return the index of a label in this container.

        Args:
            label: The label or special syntax to look up. Supported formats:
                - 'label': exact label match
                - '#n': numeric index (e.g., '#0', '#1')
                - '#attr=value': find by attribute value (e.g., '#id=34')
                - '#=value': find by node value (e.g., '#=target')

        Returns:
            Index position (0-based), or -1 if not found.
        """
        if label in self._dict:
            return next((i for i, node in enumerate(self._list) if node.label == label), -1)
        if label.startswith("#"):
            rest = label[1:]
            if "=" in rest:
                attr, value = rest.split("=", 1)
                if attr:
                    return next(
                        (i for i, node in enumerate(self._list) if node.attr.get(attr) == value), -1
                    )
                return next((i for i, node in enumerate(self._list) if node._value == value), -1)
            if rest.isdigit():
                idx = int(rest)
                return idx if idx < len(self._list) else -1
        return -1

    def _parse_position(self, position: str | int | None) -> int:
        """Parse position syntax and return insertion index.

        Args:
            position: Position specification. Supported formats:
                - None or '>': append at end
                - '<': insert at beginning
                - int n: insert at index n. Negative values count from the end
                  (Python-style: -1 = before last, -2 = before penultimate).
                  Out-of-range values are clamped to [0, len].
                - '#n': insert at non-negative index n (clamped to len)
                - '<label': insert before node with given label
                - '>label': insert after node with given label
                - '<#n': insert before non-negative index n
                - '>#n': insert after non-negative index n

        Returns:
            Index where to insert (always valid for list.insert).

        Raises:
            ValueError: If the string position is malformed (e.g. '#-1',
                '#abc', '@foo') or references a non-existent label
                (e.g. '<missing_label').
        """
        n = len(self._list)

        if position is None or position == ">":
            return n

        if position == "<":
            return 0

        if isinstance(position, int):
            if position < 0:
                position = n + position
            return max(0, min(position, n))

        if position.startswith("#"):
            idx = self._parse_sharp_index(position[1:], position)
            return min(idx, n)

        if position.startswith("<"):
            ref = position[1:]
            if ref.startswith("#"):
                idx = self._parse_sharp_index(ref[1:], position)
                return min(idx, n)
            label_idx = self.index(ref)
            if label_idx < 0:
                raise ValueError(
                    f"Invalid node_position {position!r}: label {ref!r} not found"
                )
            return label_idx

        if position.startswith(">"):
            ref = position[1:]
            if ref.startswith("#"):
                idx = self._parse_sharp_index(ref[1:], position)
                return min(idx + 1, n)
            label_idx = self.index(ref)
            if label_idx < 0:
                raise ValueError(
                    f"Invalid node_position {position!r}: label {ref!r} not found"
                )
            return label_idx + 1

        raise ValueError(f"Invalid node_position {position!r}: unrecognized syntax")

    def _parse_sharp_index(self, raw: str, original: str) -> int:
        """Parse a non-negative integer from '#n' syntax.

        Args:
            raw: The part after '#' (e.g. '3' from '#3').
            original: Full position string for error messages.

        Returns:
            The parsed non-negative integer.

        Raises:
            ValueError: If raw is not a non-negative integer.
        """
        try:
            idx = int(raw)
        except ValueError:
            raise ValueError(
                f"Invalid node_position {original!r}: {raw!r} is not an integer"
            ) from None
        if idx < 0:
            raise ValueError(
                f"Invalid node_position {original!r}: negative index not allowed in '#n' syntax"
            )
        return idx

    def __getitem__(self, key: str | int) -> BagNode | None:
        """Get item by label or index."""
        if isinstance(key, int):
            return self._list[key] if 0 <= key < len(self._list) else None
        return self._dict.get(key)

    def get(self, key: str | int) -> BagNode | None:
        """Get node by label, index, or #n/#attr=value syntax.

        Args:
            key: Label string, integer index, '#n' syntax,
                '#attr=value' syntax, or '#=value' syntax.

        Returns:
            The BagNode if found, None otherwise.
        """
        if isinstance(key, int):
            return self._list[key] if 0 <= key < len(self._list) else None
        if key.startswith("#"):
            idx = self.index(key)
            return self._list[idx] if idx >= 0 else None
        return self._dict.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        """Set item. For positional insert, use set()."""
        if key in self._dict:
            idx = next((i for i, node in enumerate(self._list) if node.label == key), -1)
            self._list[idx] = value
        else:
            self._list.append(value)
        self._dict[key] = value

    def __delitem__(self, key: str | int) -> None:
        """Delete item by label, index, or '#n'."""
        if isinstance(key, int):
            idx_to_delete = [key]
        else:
            idx_to_delete = [self.index(block) for block in smartsplit(key, ",")]

        for idx in sorted(idx_to_delete, reverse=True):
            if 0 <= idx < len(self._list):
                v = self._list.pop(idx)
                self._dict.pop(v.label)

    def __contains__(self, key: str) -> bool:
        """Check if label exists."""
        return key in self._dict

    def __len__(self) -> int:
        """Return number of elements."""
        return len(self._list)

    def __iter__(self) -> Iterator[BagNode]:
        """Iterate over nodes in order."""
        return iter(self._list)

    def set(
        self,
        label: str,
        value: Any,
        node_position: str | int | None = ">",
        attr: dict | None = None,
        resolver: Any = None,
        parent_bag: Bag | None = None,
        _updattr: bool = False,
        _remove_null_attributes: bool = True,
        _reason: str | None = None,
        do_trigger: bool = True,
        _fired: bool = False,
        node_tag: str | None = None,
    ) -> BagNode:
        """Set or create a BagNode with optional position.

        If label exists, updates the existing node's value.
        If label doesn't exist, creates a new BagNode and inserts it.

        The label can contain a query string suffix (?attr or ?attr1&attr2) to set
        attributes instead of value. When query string is present, value is converted
        to attributes and the node value is not modified (for existing nodes).

        Args:
            label: The node label. Can contain ?attr suffix to set attributes.
            value: The value to set. With ?attr syntax, becomes the attribute value.
            node_position: Position specification (>, <, #n, <label, >label, etc.)
            attr: Optional dict of attributes for new nodes.
            resolver: Optional resolver. Use False to remove existing resolver.
            parent_bag: Parent Bag reference for new nodes.
            _updattr: If True, update attributes instead of replacing.
            _remove_null_attributes: If True, remove None attributes.
            _reason: Reason for the change (for events).
            do_trigger: If True (default), fire events on change.
            _fired: If True, reset value to None after setting (fire event pattern).
                The event of the write carries fired=True; the reset emits no event.

        Returns:
            The created or updated BagNode.

        Raises:
            BagNodeException: If label uses #n syntax, or if trying to set value
                on node with resolver without handling it explicitly.
        """
        # Parse query string from label
        _query_string = None
        if "?" in label:
            label, _query_string = label.split("?", 1)

        # Validate label
        if label is None or label.startswith("#"):
            raise BagNodeException("Cannot create new node with #n syntax")

        # Handle query string: convert value to attributes
        if _query_string:
            qs = _query_string.split("&")
            if len(qs) == 1:
                attr = {qs[0]: value}
            else:
                if not isinstance(value, tuple) or len(value) != len(qs):
                    raise BagNodeException("Wrong attributes assignment")
                attr = dict(zip(qs, value, strict=True))
            value = None

        node = self._dict.get(label)

        if node:
            # Existing node — update node_tag if provided
            if node_tag is not None:
                node.node_tag = node_tag
            if _query_string:
                # ?attr syntax is an alias of set_attr(): merge semantics so
                # that only the named attribute(s) change. set_item defaults
                # _updattr=False (total replacement), which is the right
                # default for the value-write branch but not here — forcing
                # True keeps the other attributes intact.
                node.set_attr(
                    attr,
                    trigger=do_trigger,
                    _updattr=True,
                    _remove_null_attributes=_remove_null_attributes,
                )
            else:
                # Resolver handling
                if resolver is False:
                    node._resolver = None
                elif resolver is not None:
                    node._resolver = resolver
                # Resolver check
                if (node.resolver is not None
                    and value is not None
                    and not safe_is_instance(value, "genro_bag.resolver.BagResolver")):
                    raise BagNodeException(
                        f"Cannot set value on node '{node.label}' that has a resolver. "
                        "Use resolver=False to remove it first."
                    )
                node.set_value(
                    value,
                    _attributes=attr,
                    _updattr=_updattr,
                    _remove_null_attributes=_remove_null_attributes,
                    _reason=_reason,
                    trigger=do_trigger,
                    _fired=_fired,
                )
        else:
            # New node
            if parent_bag is None:
                raise BagNodeException("Cannot create a new node without a parent Bag")
            node = parent_bag._node_class(
                parent_bag,
                label=label,
                value=value,
                attr=attr,
                resolver=resolver,
                node_tag=node_tag,
                _remove_null_attributes=_remove_null_attributes,
            )
            idx = self._parse_position(node_position)
            self._dict[label] = node
            self._list.insert(idx, node)
            if do_trigger and parent_bag is not None and parent_bag.backref:
                parent_bag._on_node_inserted(node, idx, reason=_reason, fired=_fired)

        # Handle _fired
        if _fired:
            node.set_value(None, trigger=False)

        return node

    def pop(self, key: str | int) -> BagNode | None:
        """Remove and return item.

        Args:
            key: Label, index, or '#n'.

        Returns:
            The removed BagNode, or None if not found.
        """
        value = self[key]

        if value is not None:
            del self._dict[value.label]
            self._list.remove(value)
            return value

        return None

    def move(self, what: int | list[int], position: int, trigger: bool = True) -> None:
        """Move element(s) to a new position.

        Follows the same semantics as JavaScript moveNode:
        - If what is a list, nodes are removed in reverse order (highest index first)
          to preserve indices during removal
        - All removed nodes are then inserted at the target position
        - Events (del/ins) are fired for each node if trigger=True

        Args:
            what: Index or list of indices to move.
            position: Target index position.
            trigger: If True, fire del/ins events (default True).
        """
        if position < 0:
            return

        # Normalize to list
        indices = what if isinstance(what, list) else [what]
        if not indices:
            return

        # Get destination label BEFORE any removal (like JS does)
        if position >= len(self._list):
            return
        dest_label = self._list[position].label

        if len(indices) > 1:
            # Multi-node move (like JS)
            indices = sorted(indices)
            delta = 1 if indices[0] < position else 0

            # Pop nodes in reverse order (highest index first)
            popped = []
            for idx in reversed(indices):
                if 0 <= idx < len(self._list):
                    node = self._list[idx]
                    self._list.pop(idx)
                    popped.append(node)
                    if trigger and self._parent_bag is not None and self._parent_bag.backref:
                        self._parent_bag._on_node_deleted(node, idx)

            # Find new position based on dest_label + delta
            new_pos = self.index(dest_label)
            if new_pos < 0:
                new_pos = len(self._list)
            new_pos += delta

            # Insert all popped nodes at new position
            for node in popped:
                self._list.insert(new_pos, node)
                if trigger and self._parent_bag is not None and self._parent_bag.backref:
                    self._parent_bag._on_node_inserted(node, new_pos)
        else:
            # Single node move
            from_idx = indices[0]
            if from_idx == position:
                return
            if from_idx < 0 or from_idx >= len(self._list):
                return

            node = self._list[from_idx]
            self._list.pop(from_idx)

            if trigger and self._parent_bag is not None and self._parent_bag.backref:
                self._parent_bag._on_node_deleted(node, from_idx)

            self._list.insert(position, node)

            if trigger and self._parent_bag is not None and self._parent_bag.backref:
                self._parent_bag._on_node_inserted(node, position)

    def clear(self) -> None:
        """Remove all elements in place.

        Low-level clear: empties the internal list and dict without firing
        events and without touching the removed nodes. The owning Bag
        detaches them once its subscribers have been notified.
        """
        self._dict.clear()
        self._list.clear()

    def keys(self, iter: bool = False) -> list[str] | Iterator[str]:
        """Return node labels in order."""
        if iter:
            return (node.label for node in self._list)
        return [node.label for node in self._list]

    def values(self, iter: bool = False) -> list | Iterator:
        """Return node values in order."""
        if iter:
            return (node.get_value() for node in self._list)
        return [node.get_value() for node in self._list]

    def items(self, iter: bool = False) -> list[tuple[str, Any]] | Iterator[tuple[str, Any]]:
        """Return (label, value) tuples in order."""
        if iter:
            return ((node.label, node.get_value()) for node in self._list)
        return [(node.label, node.get_value()) for node in self._list]

    def __eq__(self, other: object) -> bool:
        """Two containers are equal if they have the same nodes in the same order."""
        if not isinstance(other, BagNodeContainer):
            return False
        return self._list == other._list
