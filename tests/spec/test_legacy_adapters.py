# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Behavioral contracts for native legacy adapters."""

import pickle
import re

import pytest

from genro_bag import Bag, BagNode
from genro_bag.resolver import BagResolver


def test_bag_signatures_identity_and_mixed_dispatch():
    class UpperBag(Bag):
        def get_item(self, path, *args, **kwargs):
            value = super().get_item(path, *args, **kwargs)
            return value.upper() if isinstance(value, str) else value

    bag = UpperBag({"customer": UpperBag({"name": "Ada"})})
    assert bag.getItem("customer.name") == "ADA"
    assert type(bag) is UpperBag
    assert type(bag.get_item("customer")) is UpperBag
    assert type(bag.getNode("customer.name")) is BagNode
    assert Bag.getItem(bag, "customer.name") == "ADA"

    class LegacyOverride(Bag):
        def getItem(self, path, default=None, mode=None):
            return super().getItem(path, default, mode).upper()

    assert LegacyOverride({"name": "Ada"}).getItem("name") == "ADA"
    with pytest.raises(TypeError, match="unexpected keyword"):
        Bag({"a": 1}, b=2)


def test_set_item_position_defaults_and_null_attribute_translation():
    bag = Bag()
    assert bag.setItem("b", 2, _attributes={"keep": 1, "drop": None}) is bag
    bag.setItem("a", 1, _position="<")
    bag.setItem("c", 3, _position=">b")
    assert bag.keys() == ["a", "b", "c"]
    assert bag.getNode("b").attr == {"keep": 1}

    bag.setItem(
        "b",
        20,
        _attributes={"keep": None, "null": None},
        _removeNullAttributes=False,
    )
    assert bag.getNode("b").attr == {"keep": None, "null": None}
    assert bag.getItem("missing", "fallback") == "fallback"


def test_static_reads_and_node_keyword_translation():
    calls = []

    class CountingResolver(BagResolver):
        classKwargs = {"cacheTime": 0, "readOnly": True}

        def load(self):
            calls.append(1)
            return len(calls)

    bag = Bag()
    bag.setItem("answer", CountingResolver())
    node = bag.getNode("answer")
    assert node.getValue("static") is None
    assert bag.getItem("answer", mode="static") is None
    assert calls == []
    assert node.getValue() == 1
    node.setValue(5, _attributes={"gone": None}, _removeNullAttributes=False)
    assert node.getStaticValue() == 5
    assert node.getAttr("gone") is None
    node.setStaticValue(8)
    assert node.static_value == 8


def test_parent_navigation_repeated_backref_and_modern_reparenting():
    root = Bag()
    child = Bag({'leaf': 1})
    root.setItem("child", child)
    root.setBackRef()
    attached_node = root.getNode("child")
    assert child.parent is root
    assert child.parentNode is attached_node

    child.setBackRef()
    assert child.parent is root
    assert child.parentNode is attached_node
    assert child.getNode() is attached_node
    assert child.getNode("leaf").parentNode is attached_node

    other = Bag()
    other.set_backref()
    other.set_item("moved", child)
    assert child.parent is other
    assert child.parent_node is other.get_node("moved")


def test_bag_and_node_legacy_subscription_forms():
    bag_events = []
    node_events = []

    def on_bag(**event):
        bag_events.append(event["evt"])

    def on_node(**event):
        node_events.append(event["evt"])

    bag = Bag({'item': 1})
    bag.subscribe(subscriberId="legacy", any=on_bag)
    node = bag.getNode("item")
    node.subscribe(subscriberId="legacy-node", callback=on_node)
    node.setValue(2)
    assert bag_events == ["upd_value"]
    assert node_events == ["upd_value"]
    bag.unsubscribe(subscriberId="legacy", any=True)
    node.unsubscribe(subscriberId="legacy-node")
    node.setValue(3)
    assert bag_events == ["upd_value"]
    assert node_events == ["upd_value"]


def test_add_item_policies_and_original_tag_preservation():
    bag = Bag()
    assert bag.addItem("row", 1) is bag
    assert bag.keys() == ["row"]
    assert bag.getNode("row").xml_tag is None

    with pytest.warns(DeprecationWarning, match="renamed duplicate"):
        bag.addItem("row", 2)
    assert bag.keys() == ["row", "row__dup_1"]
    assert bag.getItem("row") == 1
    assert bag.getItem("row__dup_1") == 2
    assert bag.getNode("row__dup_1").xml_tag == "row"

    before = [(node.label, node.getValue("static")) for node in bag]
    with pytest.raises(KeyError, match="already exists"):
        bag.addItem("row", 3, duplicate_policy="error")
    assert [(node.label, node.getValue("static")) for node in bag] == before
    with pytest.raises(ValueError, match="duplicate_policy"):
        bag.addItem("other", 4, duplicate_policy="replace")


def test_demonstrated_legacy_utilities():
    bag = Bag()
    branch = Bag()
    branch.appendNode("leaf", 2, _attributes={"qty": 2, "private": True})
    bag.appendNode("branch", branch, _attributes={"qty": 0, "private": True})
    bag.appendNode("top", 3, _attributes={"qty": 3, "private": True})

    assert bag.cbtraverse("branch.leaf", lambda node: node.label) == ["branch", "leaf"]
    assert dict(bag.getLeaves()) == {"branch.leaf": 2, "top": 3}
    assert bag.getIndexList() == ["branch", "branch.leaf", "top"]
    assert bag.getIndexList(asText=True) == "branch\nbranch.leaf\ntop"
    assert bag.summarizeAttributes(["qty"]) == {"qty": 5}
    assert bag.getNode("branch").getAttr("qty") == 2

    bag.popAttributesFromNodes(["private"])
    assert all(
        "private" not in node.attr
        for _path, node in bag.query("#p,#n", deep=True)
    )


def test_update_preserve_pattern_and_legacy_ignore_none():
    current = Bag()
    current.setItem("message", "${KEEP}", note="${KEEP_ATTR}")
    current.setItem("nested.value", "${KEEP_NESTED}")
    current.setItem("nullable", 1)
    incoming = Bag()
    incoming.setItem("message", "replace", note="replace attr")
    incoming.setItem("nested.value", "replace nested")
    incoming.setItem("nullable", None)
    incoming.setItem("added", 4)

    current.update(
        incoming,
        ignoreNone=True,
        preservePattern=re.compile(r"^\$\{"),
    )
    assert current.getItem("message") == "${KEEP}"
    assert current.getAttr("message", "note") == "${KEEP_ATTR}"
    assert current.getItem("nested.value") == "${KEEP_NESTED}"
    assert current.getItem("nullable") == 1
    assert current.getItem("added") == 4


def test_resolver_legacy_declarations_inheritance_and_load_attributes():
    class BaseResolver(BagResolver):
        classKwargs = {"cacheTime": 0, "readOnly": True, "offset": 1}
        classArgs = ["base"]

        def load(self):
            return self.base * self.factor + self.offset

    class ChildResolver(BaseResolver):
        classKwargs = {"factor": 2}

    resolver = ChildResolver(5, factor=3)
    assert resolver() == 16
    assert ChildResolver.class_args == ["base"]
    assert ChildResolver.class_kwargs["offset"] == 1
    assert ChildResolver.class_kwargs["factor"] == 2
    assert resolver.instanceKwargs == {
        "base": 5,
        "cacheTime": 0,
        "readOnly": True,
        "offset": 1,
        "factor": 3,
    }
    resolver.cacheTime = -1
    resolver.readOnly = False
    assert resolver.cache_time == -1
    assert resolver.cacheTime == -1
    assert resolver.read_only is False


def test_resolver_old_new_conflicts_are_explicit():
    with pytest.raises(TypeError, match="classKwargs and class_kwargs"):
        class ConflictingResolver(BagResolver):
            classKwargs = {"cacheTime": 0}
            class_kwargs = {"cache_time": 0}

    with pytest.raises(TypeError, match="classArgs and class_args"):
        class ConflictingArgsResolver(BagResolver):
            classArgs = ["value"]
            class_args = ["value"]

    with pytest.raises(TypeError, match="cacheTime and cache_time"):
        BagResolver(cacheTime=1, cache_time=1)


def test_resolver_serialization_overrides_base_calls_and_init_aliases():
    class SerializableResolver(BagResolver):
        classKwargs = {
            "cacheTime": -1,
            "readOnly": False,
            "_page": None,
            "suffix": "!",
        }
        classArgs = ["text"]

        def load(self):
            return self.text + self.suffix

        def resolverSerialize(self):
            kwargs = dict(self._initKwargs)
            kwargs.pop("_page", None)
            data = BagResolver.resolverSerialize(
                self,
                args=list(self._initArgs),
                kwargs=kwargs,
            )
            data["custom"] = True
            return data

    resolver = SerializableResolver("hello", _page=object(), suffix="?")
    data = resolver.resolverSerialize()
    assert resolver.load() == "hello?"
    assert data["resolverclass"] == "SerializableResolver"
    assert data["args"] == ["hello"]
    assert data["kwargs"] == {
        "suffix": "?",
        "cacheTime": -1,
    }
    assert data["custom"] is True

    direct_mutation = SerializableResolver("hello", _page=object())
    direct_mutation._initKwargs.pop("_page")
    assert "_page" not in BagResolver.resolverSerialize(direct_mutation)["kwargs"]

    class SuperSerializableResolver(BagResolver):
        classKwargs = {"cacheTime": 0, "readOnly": True}

        def resolverSerialize(self):
            data = super().resolverSerialize()
            data["via_super"] = True
            return data

    assert SuperSerializableResolver().resolverSerialize()["via_super"] is True


def test_pickle_roundtrip_restores_native_identity_and_backrefs():
    bag = Bag({'root': Bag({'leaf': 1})})
    bag.setBackRef()
    restored = pickle.loads(pickle.dumps(bag))
    assert type(restored) is Bag
    assert type(restored.getItem("root")) is Bag
    assert type(restored.getNode("root.leaf")) is BagNode
    assert restored.getItem("root").parent is restored
