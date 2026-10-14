"""Native value-insertion boundaries required by current GenroPy consumers."""

import pytest

from genro_bag import Bag, BagResolver


class _InstanceMetadata:
    def __init__(self):
        self.rootattributes = {"source": "instance", "rank": 1}


class _PropertyMetadata:
    def __init__(self):
        self.reads = 0

    @property
    def rootattributes(self):
        self.reads += 1
        return {"source": "property", "rank": 2}


class _DynamicFallback:
    def __init__(self):
        self.lookups = []

    def __getattr__(self, name):
        self.lookups.append(name)
        return {"source": "dynamic"}


class _DigestResolver(BagResolver):
    classKwargs = {"cacheTime": -1}

    def init(self):
        self.loads = 0

    def load(self):
        self.loads += 1
        result = Bag()
        result.set_item("row", 7, _attributes={"kind": "native"})
        return result


class _NamedList(list):
    def __init__(self, names, values):
        super().__init__(values)
        self.names = names

    def items(self):
        return zip(self.names, self, strict=True)


class _MalformedNamedList(list):
    def items(self):
        return [("good", 1), ("bad", 2, 3)]


def test_real_instance_rootattributes_are_transferred():
    value = _InstanceMetadata()
    bag = Bag()

    bag.set_item("value", value)

    assert bag["value"] is value
    assert bag.get_attr("value") == {"source": "instance", "rank": 1}


def test_rootattributes_property_is_evaluated_and_transferred():
    value = _PropertyMetadata()
    bag = Bag()

    bag.set_item("value", value)

    assert value.reads == 1
    assert bag.get_attr("value") == {"source": "property", "rank": 2}


def test_dynamic_rootattributes_fallback_is_not_called():
    value = _DynamicFallback()
    bag = Bag()

    bag.set_item("value", value)

    assert bag["value"] is value
    assert value.lookups == []
    assert bag.get_attr("value") == {}


def test_private_parent_node_alias_shares_native_state():
    parent = Bag()
    child = Bag()
    parent.setBackRef()
    node = parent.set_item("child", child)

    assert child._parentNode is node
    assert child.parent_node is node

    child._parentNode = None
    assert child.parent_node is None

    child._parentNode = node
    assert child.parent_node is node


def test_node_tag_uses_attribute_then_label_fallback():
    bag = Bag()
    tagged = bag.set_item("named", None, _attributes={"tag": "branch"})
    fallback = bag.set_item("fallback", None)
    empty = bag.set_item("empty", None, _attributes={"tag": ""})

    assert tagged.tag == "branch"
    assert fallback.tag == "fallback"
    assert empty.tag == "empty"


def test_resolver_digest_forwards_through_normal_cache():
    resolver = _DigestResolver()

    assert resolver.digest("#k,#v,#a") == [
        ("row", 7, {"kind": "native"})
    ]
    assert resolver.digest(k="#v") == [7]
    assert resolver.loads == 1


def test_list_subclass_with_items_uses_named_population():
    bag = Bag().fillFrom(_NamedList(["id", "title"], [1, "Integration"]))

    assert bag.keys() == ["id", "title"]
    assert bag["id"] == 1
    assert bag["title"] == "Integration"


def test_plain_list_keeps_positional_population():
    bag = Bag(["first", "second"])

    assert bag.keys() == ["r_00", "r_01"]
    assert bag.values() == ["first", "second"]


def test_mapping_population_preserves_existing_bag_identity():
    child = Bag({"value": 1})

    parent = Bag({"child": child})

    assert parent["child"] is child


def test_malformed_mapping_like_source_is_atomic():
    bag = Bag({"original": 1})

    with pytest.raises(ValueError):
        bag.fillFrom(_MalformedNamedList())

    assert bag.keys() == ["original"]
    assert bag["original"] == 1
