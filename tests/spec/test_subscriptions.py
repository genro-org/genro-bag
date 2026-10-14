"""Spec test: Bag - subscriptions (event observability, sync).

Depends on test_basic.py (set_item, get_item, pop, __delitem__) and
test_population.py (fill_from).

Subscriptions are the observer contract for Bag: register a callback with
an id, Bag notifies when update/insert/delete occur. In sync context we test:

- granular notifications (update, insert, delete, any)
- event propagation along parent chain (backref)
- propagation blocking (callback returns False)
- transaction() as coalescing mechanism

Synchronous refresh and rejected timer subscriptions -> test_async_reactive.

## Scale

1.  subscribe(update=...)               notify on value change
2.  subscribe(insert=...)               notify on new node
3.  subscribe(delete=...)               notify on removal
4.  subscribe(any=...)                  single callback for all
5.  subscribe enables backref automatically
6.  subscribe with no callback          noop
7.  unsubscribe selective               update / insert / delete separate
8.  unsubscribe(any=True)               removes upd/ins/del but NOT transaction
9.  callback receives documented arguments
10. propagation along parent chain
11. callback returns False              stop propagation
12. transaction()                       mutations coalesced
13. transaction()                       granular subscribers silenced
14. transaction() with exception        no transaction event
15. nested transaction()                isolated lists per scope
16. manual set_backref                  fullpath becomes non-None
17. subscribe(timer=...) without interval ValueError
"""

from __future__ import annotations

import pytest

from genro_bag import Bag, BagNode

# =============================================================================
# 1. subscribe(update=...)
# =============================================================================


class TestUpdateSubscription:
    def test_update_callback_fires_on_value_change(self):
        """Changing value of existing node triggers update callback."""
        events = []
        bag = Bag()
        bag["a"] = 1
        bag.subscribe("s1", update=lambda **kw: events.append(kw["evt"]))
        bag["a"] = 2
        assert events == ["upd_value"]

    def test_update_not_fired_on_insert(self):
        """An insert does not fire the update callback."""
        events = []
        bag = Bag()
        bag.subscribe("s1", update=lambda **kw: events.append(kw["evt"]))
        bag["new"] = 1
        assert events == []


# =============================================================================
# 2. subscribe(insert=...)
# =============================================================================


class TestInsertSubscription:
    def test_insert_callback_fires_on_new_node(self):
        """Assigning a new path fires the insert callback."""
        events = []
        bag = Bag()
        bag.subscribe("s1", insert=lambda **kw: events.append(kw["evt"]))
        bag["a"] = 1
        assert events == ["ins"]

    def test_insert_not_fired_on_update(self):
        """Changing existing node does not trigger insert."""
        events = []
        bag = Bag()
        bag["a"] = 1
        bag.subscribe("s1", insert=lambda **kw: events.append(kw["evt"]))
        bag["a"] = 2
        assert events == []


# =============================================================================
# 3. subscribe(delete=...)
# =============================================================================


class TestDeleteSubscription:
    def test_delete_callback_fires_on_pop(self):
        """pop/del trigger delete callback."""
        events = []
        bag = Bag()
        bag["a"] = 1
        bag.subscribe("s1", delete=lambda **kw: events.append(kw["evt"]))
        bag.pop("a")
        assert events == ["del"]

    def test_delete_callback_fires_on_del_item(self):
        """del bag[path] triggers delete callback."""
        events = []
        bag = Bag()
        bag["a"] = 1
        bag.subscribe("s1", delete=lambda **kw: events.append(kw["evt"]))
        del bag["a"]
        assert events == ["del"]


# =============================================================================
# 4. subscribe(any=...)
# =============================================================================


class TestAnySubscription:
    def test_any_callback_fires_on_all_three(self):
        """any=... covers update + insert + delete (not timer/transaction)."""
        events = []
        bag = Bag()
        bag.subscribe("s1", any=lambda **kw: events.append(kw["evt"]))
        bag["a"] = 1         # ins
        bag["a"] = 2         # upd
        bag.pop("a")         # del
        assert events == ["ins", "upd_value", "del"]


# =============================================================================
# 5. subscribe abilita automaticamente backref
# =============================================================================


class TestSubscribeEnablesBackref:
    def test_subscribe_enables_backref(self):
        """subscribe enables backref if not already active."""
        bag = Bag()
        assert bag.backref is False
        bag.subscribe("s1", update=lambda **kw: None)
        assert bag.backref is True


# =============================================================================
# 6. subscribe with no callback
# =============================================================================


class TestSubscribeNoCallback:
    def test_subscribe_without_callbacks_is_noop_but_enables_backref(self):
        """subscribe(id) without callbacks registers nothing but enables backref."""
        bag = Bag()
        bag.subscribe("s1")
        assert bag.backref is True
        # no callbacks: no notifications to verify, no error
        bag["a"] = 1  # must work without raising


# =============================================================================
# 7. unsubscribe selettivo
# =============================================================================


class TestUnsubscribeSelective:
    def test_unsubscribe_update_only(self):
        """unsubscribe(update=True) removes the update callback only."""
        events: list[str] = []
        bag = Bag()
        bag.subscribe(
            "s1",
            update=lambda **kw: events.append("u"),
            insert=lambda **kw: events.append("i"),
        )
        bag.unsubscribe("s1", update=True)
        bag["a"] = 1  # insert -> 'i'
        bag["a"] = 2  # update -> non piu' registrato
        assert events == ["i"]

    def test_unsubscribe_insert_only_keeps_others(self):
        """unsubscribe(insert=True) preserva update e delete."""
        events: list[str] = []
        bag = Bag()
        bag.subscribe(
            "s1",
            insert=lambda **kw: events.append("i"),
            update=lambda **kw: events.append("u"),
            delete=lambda **kw: events.append("d"),
        )
        bag.unsubscribe("s1", insert=True)
        bag["a"] = 1     # insert -> non registrato
        bag["a"] = 2     # update
        bag.pop("a")     # delete
        assert events == ["u", "d"]


# =============================================================================
# 8. unsubscribe(any=True) NON tocca transaction
# =============================================================================


class TestUnsubscribeAny:
    def test_unsubscribe_any_removes_upd_ins_del_keeps_transaction(self):
        """any=True removes upd/ins/del/timer but NOT transaction."""
        events: list[str] = []
        bag = Bag()
        bag.subscribe(
            "s1",
            any=lambda **kw: events.append(kw["evt"]),
            transaction=lambda **kw: events.append("txn"),
        )
        bag.unsubscribe("s1", any=True)

        with bag.transaction():
            bag["a"] = 1
        # upd/ins/del removed; transaction still active
        assert events == ["txn"]

    def test_unsubscribe_transaction_only(self):
        """unsubscribe(transaction=True) removes transaction only."""
        events: list[str] = []
        bag = Bag()
        bag.subscribe(
            "s1",
            any=lambda **kw: events.append(kw["evt"]),
            transaction=lambda **kw: events.append("txn"),
        )
        bag.unsubscribe("s1", transaction=True)
        with bag.transaction():
            bag["a"] = 1
        # transaction removed, but granular silenced inside with
        # -> no event (with consumes mutations without dispatch)
        assert events == []


# =============================================================================
# 9. Callback riceve argomenti documentati
# =============================================================================


class TestCallbackArguments:
    def test_update_callback_receives_evt_node_pathlist_oldvalue(self):
        """Update callback receives evt, node, pathlist, oldvalue, reason."""
        captured: list[dict] = []
        bag = Bag()
        bag["a"] = "old"
        bag.subscribe("s1", update=lambda **kw: captured.append(kw))
        bag["a"] = "new"

        assert len(captured) == 1
        kw = captured[0]
        assert kw["evt"] == "upd_value"
        assert kw["node"].label == "a"
        assert kw["oldvalue"] == "old"
        assert "pathlist" in kw
        assert "reason" in kw

    def test_insert_callback_receives_evt_node_pathlist_ind(self):
        """Insert callback receives evt, node, pathlist, ind, reason."""
        captured: list[dict] = []
        bag = Bag()
        bag.subscribe("s1", insert=lambda **kw: captured.append(kw))
        bag["first"] = 1

        assert len(captured) == 1
        kw = captured[0]
        assert kw["evt"] == "ins"
        assert kw["node"].label == "first"
        assert kw["ind"] == 0
        assert "pathlist" in kw

    def test_delete_callback_receives_evt_node_pathlist_ind(self):
        """Delete callback receives evt, node, pathlist, ind, reason."""
        captured: list[dict] = []
        bag = Bag()
        bag["x"] = 1
        bag.subscribe("s1", delete=lambda **kw: captured.append(kw))
        bag.pop("x", _reason="cleanup")

        assert len(captured) == 1
        kw = captured[0]
        assert kw["evt"] == "del"
        assert kw["ind"] == 0
        assert kw["reason"] == "cleanup"


# =============================================================================
# 10. Propagazione eventi lungo la parent chain
# =============================================================================


class TestEventPropagation:
    def test_change_in_child_notifies_root(self):
        """Change on sub-Bag node reaches root subscriber."""
        events: list = []
        root = Bag()
        root.subscribe("root_sub", update=lambda **kw: events.append(kw["pathlist"]))
        # create sub-bag and attach
        root["outer.inner"] = 1
        # change deep leaf
        root["outer.inner"] = 2

        assert len(events) == 1
        # pathlist contains label sequence to modified leaf
        assert events[0] == ["outer", "inner"]

    def test_insert_in_child_notifies_root(self):
        """Insert in sub-Bag propagates to root."""
        events: list = []
        root = Bag()
        root["outer.x"] = 1  # crea sub-bag 'outer'
        root.subscribe("root_sub", insert=lambda **kw: events.append(kw["node"].label))
        root["outer.y"] = 2

        assert "y" in events


# =============================================================================
# 11. A callback returning False stops the propagation
# =============================================================================


class TestPropagationStop:
    def test_false_stops_bubbling_to_parent(self):
        """Callback returning False blocks propagation to parent."""
        root_events: list = []
        child_events: list = []

        root = Bag()
        root["outer.x"] = 0
        child = root.get_item("outer")
        assert isinstance(child, Bag)

        # subscriber on child that blocks; subscriber on root must NOT see
        child.subscribe(
            "child_sub",
            update=lambda **kw: (child_events.append(kw["evt"]), False)[1],
        )
        root.subscribe("root_sub", update=lambda **kw: root_events.append(kw["evt"]))

        root["outer.x"] = 1

        assert child_events == ["upd_value"]
        assert root_events == []  # bloccato


# =============================================================================
# 12-15. transaction()
# =============================================================================


class TestTransaction:
    def test_mutations_coalesced_into_single_event(self):
        """Mutations inside transaction() arrive in single event."""
        received: list[list] = []
        bag = Bag()
        bag.subscribe("s1", transaction=lambda **kw: received.append(kw["mutations"]))

        with bag.transaction():
            bag["a"] = 1
            bag["b"] = 2
            bag["c"] = 3

        assert len(received) == 1
        mutations = received[0]
        assert len(mutations) == 3
        # each item is tuple with event type as first element
        event_kinds = [m[0] for m in mutations]
        assert event_kinds == ["ins", "ins", "ins"]

    def test_granular_subscribers_silenced_inside_transaction(self):
        """Inside with, granular update/insert/delete callbacks are not called."""
        granular: list[str] = []
        txn_received: list = []
        bag = Bag()
        bag.subscribe(
            "s1",
            any=lambda **kw: granular.append(kw["evt"]),
            transaction=lambda **kw: txn_received.append(len(kw["mutations"])),
        )
        with bag.transaction():
            bag["a"] = 1
            bag["b"] = 2

        assert granular == []
        assert txn_received == [2]

    def test_exception_inside_with_suppresses_transaction_event(self):
        """If with body raises, no transaction event is emitted."""
        txn_received: list = []
        bag = Bag()
        bag.subscribe("s1", transaction=lambda **kw: txn_received.append(kw))

        with pytest.raises(RuntimeError), bag.transaction():
            bag["a"] = 1
            raise RuntimeError("boom")

        assert txn_received == []
        # already-applied mutation remains (no rollback documented)
        assert bag.get_item("a") == 1

    def test_nested_transactions_emit_separate_events(self):
        """Each nested with emits own transaction event."""
        received: list[list] = []
        bag = Bag()
        bag.subscribe("s1", transaction=lambda **kw: received.append(kw["mutations"]))

        with bag.transaction():
            bag["outer1"] = 1
            with bag.transaction():
                bag["inner1"] = 2
                bag["inner2"] = 3
            bag["outer2"] = 4

        # two events: inner first (closes first), then outer
        assert len(received) == 2
        assert len(received[0]) == 2  # inner: 2 mutations
        assert len(received[1]) == 2  # outer: 2 mutations (outer1, outer2)


# =============================================================================
# 16. set_backref manuale
# =============================================================================


class TestSetBackref:
    def test_set_backref_enables_backref_flag(self):
        """set_backref() enables backref flag."""
        bag = Bag()
        assert bag.backref is False
        bag.set_backref()
        assert bag.backref is True

    def test_fullpath_none_without_backref(self):
        """fullpath on sub-Bag without backref is None."""
        root = Bag()
        root["outer.inner"] = 1
        outer = root.get_item("outer")
        assert isinstance(outer, Bag)
        assert outer.fullpath is None

    def test_fullpath_reports_path_after_subscribe_enables_backref(self):
        """After subscribe enables backref, fullpath reflects hierarchy."""
        root = Bag()
        root["outer.inner"] = 1
        root.subscribe("s1", update=lambda **kw: None)
        outer = root.get_item("outer")
        assert isinstance(outer, Bag)
        assert outer.fullpath == "outer"


# =============================================================================
# 17. subscribe(timer=...) without interval raises
# =============================================================================


class TestTimerValidation:
    def test_timer_without_interval_raises(self):
        """subscribe(timer=cb) without interval raises TypeError."""
        bag = Bag()
        with pytest.raises(TypeError):
            bag.subscribe("s1", timer=lambda **kw: None)


# =============================================================================
# 18. clear on a sub-Bag with backref -> upd_value notified on the parent
# =============================================================================


class TestClearWithBackref:
    def test_clear_of_nested_bag_notifies_parent_with_oldvalue(self):
        """clear() on nested sub-Bag with backref emits upd_value on parent.

        oldvalue is orphan Bag with prior content (snapshot).
        Scenario: atomic reset of section with external watcher.
        """
        events: list[dict] = []
        root = Bag()
        root["section.a"] = 1
        root["section.b"] = 2
        root.subscribe("w", update=lambda **kw: events.append(kw))

        section = root.get_item("section")
        assert isinstance(section, Bag)
        section.clear()

        # event received by parent
        assert len(events) >= 1
        last = events[-1]
        assert last["evt"] == "upd_value"
        # oldvalue is Bag with prior content
        old = last["oldvalue"]
        assert isinstance(old, Bag)
        assert old.get_item("a") == 1
        assert old.get_item("b") == 2

    def test_clear_of_nested_bag_leaves_it_empty(self):
        """After clear() on nested sub-Bag, sub-Bag is empty."""
        root = Bag()
        root["section.a"] = 1
        root.subscribe("w", update=lambda **kw: None)
        section = root.get_item("section")
        assert isinstance(section, Bag)
        section.clear()
        assert len(section) == 0


# =============================================================================
# 19. fullpath / root su gerarchie profonde (>= 3 livelli)
# =============================================================================


class TestDeepHierarchy:
    def test_fullpath_three_levels(self):
        """fullpath of 3-level leaf: outer.middle.inner."""
        root = Bag()
        root["a.b.c"] = 42
        root.subscribe("w", update=lambda **kw: None)
        middle = root.get_item("a.b")
        assert isinstance(middle, Bag)
        assert middle.fullpath == "a.b"

    def test_root_traverses_full_chain(self):
        """bag.root from deepest node ascends to root."""
        root = Bag()
        root["a.b.c.d"] = 42
        root.subscribe("w", update=lambda **kw: None)
        deepest = root.get_item("a.b.c")
        assert isinstance(deepest, Bag)
        assert deepest.root is root

    def test_attributes_of_nested_bag_reflect_parent_node(self):
        """sub.attributes reads attributes of node containing sub-Bag."""
        root = Bag()
        root["section.inner"] = "v"
        root.set_attr("section", kind="form")
        root.subscribe("w", update=lambda **kw: None)
        section = root.get_item("section")
        assert isinstance(section, Bag)
        assert section.attributes.get("kind") == "form"


# =============================================================================
# 20. Bag.get_inherited_attributes (a sub-Bag inherits from its parent)
# =============================================================================


class TestBagGetInheritedAttributes:
    def test_sub_bag_inherits_from_parent_node(self):
        """Bag.get_inherited_attributes gathers attributes from parent chain.

        Scenario: form section inheriting 'permission' from ancestor.
        """
        root = Bag()
        root["outer.inner"] = "v"
        root.set_attr("outer", permission="read")
        root.subscribe("w", update=lambda **kw: None)
        root.get_item("outer.inner")
        # 'outer.inner' is not Bag but scalar value; requires that
        # we test mechanism at internal container level
        # Restart: create true sub-Bag as value
        root2 = Bag()
        deep = Bag()
        deep["k"] = 1
        root2.set_item("section", deep, _attributes={"permission": "write"})
        root2.subscribe("w", update=lambda **kw: None)
        section = root2.get_item("section")
        assert isinstance(section, Bag)
        inherited = section.get_inherited_attributes()
        assert inherited.get("permission") == "write"


# =============================================================================
# 21. relative_path: from the Bag down to a descendant node
# =============================================================================


class TestRelativePath:
    def test_relative_path_from_root_to_leaf(self):
        """bag.relative_path(leaf_node) returns path from bag to node."""
        root = Bag()
        root["a.b.c"] = 42
        root.subscribe("w", update=lambda **kw: None)
        leaf = root.get_node("a.b.c")
        assert isinstance(leaf, BagNode)
        assert root.relative_path(leaf) == "a.b.c"

    def test_relative_path_from_intermediate_to_leaf(self):
        """Relative path from intermediate to direct child."""
        root = Bag()
        root["outer.inner.leaf"] = 1
        root.subscribe("w", update=lambda **kw: None)
        outer = root.get_item("outer")
        assert isinstance(outer, Bag)
        leaf = root.get_node("outer.inner.leaf")
        assert isinstance(leaf, BagNode)
        # path from 'outer' to leaf is 'inner.leaf'
        assert outer.relative_path(leaf) == "inner.leaf"


# =============================================================================
# 22. clear_backref: detach ricorsivo del sub-tree
# =============================================================================


class TestClearBackref:
    def test_clear_backref_disables_backref(self):
        """clear_backref() disables backref on Bag."""
        bag = Bag()
        bag["x"] = 1
        bag.set_backref()
        assert bag.backref is True
        bag.clear_backref()
        assert bag.backref is False

    def test_clear_backref_recursive_on_nested_bags(self):
        """clear_backref() disables backref on sub-Bags too."""
        root = Bag()
        root["section.inner"] = 1
        root.subscribe("w", update=lambda **kw: None)
        section = root.get_item("section")
        assert isinstance(section, Bag)
        assert section.backref is True  # inherited from root
        root.clear_backref()
        assert root.backref is False
        assert section.backref is False


# =============================================================================
# 23. get_node with autocreate on a Bag with subscribers -> ins event
# =============================================================================


class TestAutocreateWithSubscribers:
    def test_autocreate_fires_insert_event(self):
        """get_node(path, autocreate=True) on Bag with backref emits ins event."""
        events: list = []
        bag = Bag()
        bag.subscribe("w", insert=lambda **kw: events.append(kw["node"].label))
        bag.get_node("newnode", autocreate=True)
        assert "newnode" in events


# =============================================================================
# 24. Stop propagation su insert e delete
# =============================================================================


class TestStopPropagationInsertDelete:
    def test_false_on_child_insert_blocks_parent(self):
        """Insert callback on child returning False blocks propagation."""
        root = Bag()
        # create sub-Bag 'section' with preexisting node
        root["section.x"] = 0
        section = root.get_item("section")
        assert isinstance(section, Bag)

        root_events: list = []
        child_events: list = []
        section.subscribe(
            "child_sub",
            insert=lambda **kw: (child_events.append(kw["node"].label), False)[1],
        )
        root.subscribe("root_sub", insert=lambda **kw: root_events.append(kw["node"].label))

        # new insert inside section
        root["section.new"] = 1

        assert child_events == ["new"]
        assert root_events == []

    def test_false_on_child_delete_blocks_parent(self):
        """Delete callback on child returning False blocks propagation."""
        root = Bag()
        root["section.x"] = 0
        section = root.get_item("section")
        assert isinstance(section, Bag)

        root_events: list = []
        child_events: list = []
        section.subscribe(
            "child_sub",
            delete=lambda **kw: (child_events.append(kw["node"].label), False)[1],
        )
        root.subscribe("root_sub", delete=lambda **kw: root_events.append(kw["node"].label))

        # delete inside section
        root.pop("section.x")

        assert child_events == ["x"]
        assert root_events == []


# =============================================================================
# 25. Update inside a transaction (recorded as an 'upd' mutation)
# =============================================================================


class TestTransactionUpdates:
    def test_update_inside_transaction_captured_as_upd_mutation(self):
        """Value changes inside transaction end up in batch as 'upd'."""
        received: list[list] = []
        bag = Bag()
        bag["x"] = 1  # pre-existing
        bag.subscribe("s1", transaction=lambda **kw: received.append(kw["mutations"]))

        with bag.transaction():
            bag["x"] = 99        # update value
            bag["new"] = "ins"   # insert
            bag.pop("x")         # delete

        assert len(received) == 1
        kinds = [m[0] for m in received[0]]
        assert kinds == ["upd", "ins", "del"]


# =============================================================================
# 26. Delete propagazione verso root (pathlist)
# =============================================================================


class TestDeletePropagation:
    def test_delete_in_child_bubbles_with_pathlist(self):
        """pop on leaf in sub-Bag notifies root with pathlist."""
        captured: list[list] = []
        root = Bag()
        root["section.x"] = 1
        root.subscribe("w", delete=lambda **kw: captured.append(kw["pathlist"]))
        root.pop("section.x")

        assert len(captured) == 1
        # pathlist contains label sequence from parent to deleted node
        assert captured[0] == ["section"]


# =============================================================================
# 27. bag.move with backref: emits del/ins events for the reordering
# =============================================================================


class TestMoveWithBackref:
    def test_single_move_fires_del_and_ins_events(self):
        """move(0, 2) with backref emits del on moved node then ins."""
        events: list[str] = []
        bag = Bag()
        bag["a"] = 1
        bag["b"] = 2
        bag["c"] = 3
        bag.subscribe(
            "w",
            delete=lambda **kw: events.append(f"del:{kw['node'].label}"),
            insert=lambda **kw: events.append(f"ins:{kw['node'].label}"),
        )
        bag.move(0, 2)
        # 'a' removed then reinserted at position 2
        assert "del:a" in events
        assert "ins:a" in events
        # final order consistent with move semantics
        assert bag.keys() == ["b", "c", "a"]

    def test_single_move_trigger_false_suppresses_events(self):
        """move(..., trigger=False) does not emit ins/del events."""
        events: list[str] = []
        bag = Bag()
        bag["a"] = 1
        bag["b"] = 2
        bag.subscribe(
            "w",
            any=lambda **kw: events.append(kw["evt"]),
        )
        bag.move(0, 1, trigger=False)
        assert events == []

    def test_multi_move_fires_events_for_each_node(self):
        """move([0, 2], 1) with backref emits events for each moved node."""
        events: list[str] = []
        bag = Bag()
        bag["a"] = 1
        bag["b"] = 2
        bag["c"] = 3
        bag["d"] = 4
        bag.subscribe(
            "w",
            delete=lambda **kw: events.append(f"del:{kw['node'].label}"),
            insert=lambda **kw: events.append(f"ins:{kw['node'].label}"),
        )
        bag.move([0, 2], 1)
        # both moved nodes receive del + ins
        assert any(e.startswith("del:a") for e in events)
        assert any(e.startswith("ins:a") for e in events)
        assert any(e.startswith("del:c") for e in events)
        assert any(e.startswith("ins:c") for e in events)
