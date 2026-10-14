# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""BagEvents mixin - subscription and event propagation for Bag.

Provides the event subscription system (subscribe/unsubscribe) and
internal event triggers (_on_node_changed, _on_node_inserted, etc.)
that propagate changes up the parent hierarchy.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genro_bag.bagnode import BagNode


_current_transaction: ContextVar[list | None] = ContextVar(
    "genro_bag_current_transaction", default=None
)


class BagEvents:
    """Mixin providing event subscription and propagation for Bag.

    Manages four subscriber dicts (_upd, _ins, _del, _txn) and propagates
    change/insert/delete events up the parent hierarchy via backref chain.
    """

    _upd_subscribers: dict
    _ins_subscribers: dict
    _del_subscribers: dict
    _txn_subscribers: dict
    parent: Any
    parent_node: Any
    backref: Any

    if TYPE_CHECKING:
        def set_backref(self, node: Any = ..., parent: Any = ...) -> None: ...

    # -------------------- event triggers --------------------------------

    def _on_node_changed(
        self,
        node: BagNode,
        pathlist: list,
        evt: str,
        oldvalue: Any = None,
        attrs_diff: dict[str, dict[str, Any]] | None = None,
        reason: str | None = None,
        fired: bool = False,
    ) -> None:
        """Trigger for node change events.

        ``fired`` is True when the change is a fired write
        (``set_item(..., _fired=True)``) and is passed to subscribers.

        Inside an active transaction, appends the mutation to the current
        list and returns (no subscriber dispatch, no parent bubble).
        Otherwise, dispatches to local subscribers and propagates to parent
        unless a subscriber returns False.
        """
        txn = _current_transaction.get()
        if txn is not None:
            txn.append(("upd", node, pathlist, evt, oldvalue, attrs_diff, reason, fired))
            return
        for s in list(self._upd_subscribers.values()):
            if s(
                node=node, pathlist=pathlist,
                oldvalue=oldvalue, attrs_diff=attrs_diff,
                evt=evt, reason=reason, fired=fired,
            ) is False:
                return
        if self.parent and self.parent_node:
            self.parent._on_node_changed(
                node, [self.parent_node.label] + pathlist,
                evt, oldvalue=oldvalue, attrs_diff=attrs_diff, reason=reason, fired=fired,
            )

    def _on_node_inserted(
        self,
        node: BagNode,
        ind: int,
        pathlist: list | None = None,
        reason: str | None = None,
        fired: bool = False,
    ) -> None:
        """Trigger for node insert events.

        ``fired`` is True when the insert is a fired write
        (``set_item(..., _fired=True)`` on a missing path) and is passed
        to subscribers.

        Inside an active transaction, appends the mutation to the current
        list and returns (no subscriber dispatch, no parent bubble).
        Otherwise, dispatches to local subscribers and propagates to parent
        unless a subscriber returns False.
        """
        parent = node.parent_bag
        value = node._value
        if parent is not None and parent.backref and hasattr(value, "_htraverse"):
            value.set_backref(node=node, parent=parent)

        if pathlist is None:
            pathlist = []
        txn = _current_transaction.get()
        if txn is not None:
            txn.append(("ins", node, pathlist, ind, reason, fired))
            return
        for s in list(self._ins_subscribers.values()):
            if s(
                node=node, pathlist=pathlist, ind=ind, evt="ins", reason=reason, fired=fired,
            ) is False:
                return
        if self.parent and self.parent_node:
            self.parent._on_node_inserted(
                node, ind, [self.parent_node.label] + pathlist, reason=reason, fired=fired,
            )

    def _on_node_deleted(
        self, node: Any, ind: int, pathlist: list | None = None, reason: str | None = None
    ) -> None:
        """Trigger for node delete events.

        Inside an active transaction, appends the mutation to the current
        list and returns (no subscriber dispatch, no parent bubble).
        Otherwise, dispatches to local subscribers and propagates to parent
        unless a subscriber returns False.
        """
        txn = _current_transaction.get()
        if txn is not None:
            txn.append(("del", node, pathlist if pathlist is not None else [], ind, reason))
            return
        for s in list(self._del_subscribers.values()):
            if s(node=node, pathlist=pathlist, ind=ind, evt="del", reason=reason) is False:
                return
        if self.parent and self.parent_node:
            if pathlist is None:
                pathlist = []
            self.parent._on_node_deleted(
                node, ind, [self.parent_node.label] + pathlist, reason=reason
            )

    # -------------------- subscription --------------------------------

    def _subscribe(self, subscriber_id: str, subscribers_dict: dict, callback: Any) -> None:
        """Internal subscribe helper."""
        if callback is not None:
            subscribers_dict[subscriber_id] = callback

    def subscribe(
        self,
        subscriber_id: str,
        update: Any = None,
        insert: Any = None,
        delete: Any = None,
        any: Any = None,
        transaction: Any = None,
    ) -> None:
        """Subscribe a callback to bag events.

        Args:
            subscriber_id: Unique identifier for this subscription.
            update: Callback for update events.
            insert: Callback for insert events.
            delete: Callback for delete events.
            any: Callback for update, insert, and delete events (not transaction).
            transaction: Callback for transaction events (separate category,
                not covered by ``any``). Receives ``bag=<bag>, mutations=<list>``.
        """
        if not self.backref:
            self.set_backref()
        self._subscribe(subscriber_id, self._upd_subscribers, update or any)
        self._subscribe(subscriber_id, self._ins_subscribers, insert or any)
        self._subscribe(subscriber_id, self._del_subscribers, delete or any)
        self._subscribe(subscriber_id, self._txn_subscribers, transaction)

    def unsubscribe(
        self,
        subscriber_id: str,
        update: bool = False,
        insert: bool = False,
        delete: bool = False,
        any: bool = False,
        transaction: bool = False,
    ) -> None:
        """Remove a subscription.

        Args:
            subscriber_id: The subscription identifier to remove.
            update: Remove update subscription.
            insert: Remove insert subscription.
            delete: Remove delete subscription.
            any: Remove update/insert/delete subscriptions
                (does not cover ``transaction``, which is a separate category).
            transaction: Remove transaction subscription.
        """
        if update or any:
            self._upd_subscribers.pop(subscriber_id, None)
        if insert or any:
            self._ins_subscribers.pop(subscriber_id, None)
        if delete or any:
            self._del_subscribers.pop(subscriber_id, None)
        if transaction:
            self._txn_subscribers.pop(subscriber_id, None)
