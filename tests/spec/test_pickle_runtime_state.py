# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Pickle boundaries for live Bags with transient runtime callbacks."""

import pickle

from genro_bag import Bag, BagNode


class RuntimeNode(BagNode):
    """Picklable module-level node subclass used to check native identity."""


class RuntimeBag(Bag):
    """Picklable module-level Bag subclass with its own node factory."""

    _node_class = RuntimeNode


def test_pickle_excludes_transient_subscribers_without_mutating_live_graph():
    events = []
    node_events = []
    nested = RuntimeBag({'leaf': 1})
    source = RuntimeBag({'root': nested})
    source.set_backref()
    root_node = source.get_node("root")
    leaf_node = nested.get_node("leaf")

    source.subscribe("local", any=lambda **event: events.append(event["evt"]))
    source.subscribe("transaction", transaction=lambda **event: None)
    nested.subscribe("nested", any=lambda **event: None)
    leaf_node.subscribe("node", lambda **event: node_events.append(event["evt"]))
    payload = pickle.dumps(source)

    assert source.backref is True
    assert source.get_node("root") is root_node
    assert root_node.parent_bag is source
    assert nested.parent is source
    assert nested.parent_node is root_node
    assert leaf_node.parent_bag is nested
    assert set(source._upd_subscribers) == {"local"}
    assert set(source._ins_subscribers) == {"local"}
    assert set(source._del_subscribers) == {"local"}
    assert set(source._txn_subscribers) == {"transaction"}
    assert set(nested._upd_subscribers) == {"nested"}
    assert set(leaf_node._node_subscribers) == {"node"}

    nested.set_item("leaf", 2)
    assert events == ["upd_value"]
    assert node_events == ["upd_value"]

    restored = pickle.loads(payload)
    restored_nested = restored.get_item("root")
    restored_root_node = restored.get_node("root")
    restored_leaf_node = restored.get_node("root.leaf")

    assert type(restored) is RuntimeBag
    assert type(restored_nested) is RuntimeBag
    assert type(restored_root_node) is RuntimeNode
    assert type(restored_leaf_node) is RuntimeNode
    assert restored.backref is True
    assert restored_root_node.parent_bag is restored
    assert restored_nested.parent is restored
    assert restored_nested.parent_node is restored_root_node
    assert restored_leaf_node.parent_bag is restored_nested
    assert restored.get_item("root.leaf") == 1
    assert restored._upd_subscribers == {}
    assert restored._ins_subscribers == {}
    assert restored._del_subscribers == {}
    assert restored._txn_subscribers == {}
    assert restored_nested._upd_subscribers == {}
    assert restored_leaf_node._node_subscribers == {}


def test_repeated_pickle_does_not_change_unsubscribed_tree_state():
    source = RuntimeBag({'root': RuntimeBag({'leaf': 1})})
    source.set_backref()
    nested = source.get_item("root")
    root_node = source.get_node("root")

    first = pickle.dumps(source)
    second = pickle.dumps(source)

    assert first == second
    assert source.backref is True
    assert root_node.parent_bag is source
    assert nested.parent is source
    assert nested.parent_node is root_node


def test_pickling_nested_bag_does_not_capture_its_external_parent_graph():
    parent = RuntimeBag({'child': RuntimeBag({'leaf': 1})})
    parent.set_backref()
    nested = parent.get_item("child")
    parent.subscribe("local", any=lambda **event: None)

    payload = pickle.dumps(nested)
    restored = pickle.loads(payload)

    assert nested.parent is parent
    assert nested.parent_node is parent.get_node("child")
    assert set(parent._upd_subscribers) == {"local"}
    assert type(restored) is RuntimeBag
    assert restored.parent is None
    assert restored.parent_node is None
    assert restored.backref is True
    assert restored.get_node("leaf").parent_bag is restored
    assert restored.get_item("leaf") == 1
