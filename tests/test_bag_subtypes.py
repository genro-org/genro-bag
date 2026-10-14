# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Contract: Bag subclasses travel as "::X" plus a ``__cls`` symbolic name.

The name is looked up in the TYTX subtype dictionary of "X". A branch carries
it in the attributes of its row, a root at payload level; it is written only
when the class differs from the inherited one (the parent's class for a
branch, Bag for a root). The payload alone decides the class of the root.
"""

import pytest
from genro_tytx import from_tytx, get_subtype_dict, set_subtype_dict, to_tytx

from genro_bag import Bag, BagNode, BagSerializationError


class SourceNode(BagNode):
    pass


class Source(Bag):
    _node_class = SourceNode


class Page(Source):
    pass


class Unregistered(Bag):
    pass


@pytest.fixture
def subtypes():
    """Register Source and Page next to Bag, restoring the dictionary afterwards."""
    before = get_subtype_dict("X")
    set_subtype_dict("X", {**before, "Source": Source, "Page": Page})
    yield
    set_subtype_dict("X", before)


def roundtrip(bag, transport="json", compact=False, cls=Bag):
    return cls.from_tytx(bag.to_tytx(transport, compact=compact), transport)


class TestRegistration:
    def test_bag_is_registered_under_its_name(self):
        assert get_subtype_dict("X")["Bag"] is Bag


class TestPlainBagsUnchanged:
    def test_no_cls_on_the_wire(self):
        bag = Bag({"a": Bag({"b": 1}), "c": 2})
        assert "__cls" not in bag.to_tytx()

    def test_roundtrip(self):
        result = roundtrip(Bag({"a": Bag({"b": 1})}))
        assert type(result) is Bag and type(result["a"]) is Bag


@pytest.mark.usefixtures("subtypes")
class TestRoot:
    def test_root_name_at_payload_level(self):
        decoded = from_tytx(Source().to_tytx().removesuffix("::X"))
        assert decoded["__cls"] == "Source"

    @pytest.mark.parametrize("caller", [Bag, Source, Page])
    def test_payload_decides_the_root_class(self, caller):
        assert type(roundtrip(Source({"a": 1}), cls=caller)) is Source

    @pytest.mark.parametrize("caller", [Source, Page])
    def test_payload_without_cls_is_a_bag(self, caller):
        assert type(caller.from_tytx(Bag({"a": 1}).to_tytx())) is Bag

    @pytest.mark.parametrize("caller", [Bag, Source])
    def test_empty_marked_bag_is_a_bag(self, caller):
        assert type(caller.from_tytx(Bag().to_tytx())) is Bag

    @pytest.mark.parametrize("caller", [Bag, Source])
    def test_empty_untyped_scalar_is_rejected(self, caller):
        with pytest.raises(TypeError, match="root"):
            caller.from_tytx("")

    def test_node_class_follows_the_bag(self):
        result = roundtrip(Source({"a": 1}))
        assert type(result.get_node("a")) is SourceNode


@pytest.mark.usefixtures("subtypes")
class TestBranches:
    @pytest.mark.parametrize("transport", ["json", "msgpack"])
    @pytest.mark.parametrize("compact", [False, True])
    def test_mixed_tree(self, transport, compact):
        tree = Source()
        tree.set_item("body", Source(), _attributes={"k": 1})
        tree.set_item("body.data", Bag())
        tree.set_item("body.data.value", 42)
        tree.set_item("body.data.page", Page())
        tree.set_item("body.data.page.inner", Page())
        result = roundtrip(tree, transport, compact)
        assert type(result) is Source
        assert type(result["body"]) is Source
        assert type(result["body.data"]) is Bag
        assert type(result["body.data.page"]) is Page
        assert type(result["body.data.page.inner"]) is Page
        assert result["body.data.value"] == 42
        assert result.get_node("body").attr == {"k": 1}
        assert "__cls" not in result.get_node("body.data").attr
        assert type(result["body"].get_node("data")) is SourceNode

    def test_tree_without_backref(self):
        tree = Source({"data": Bag({"page": Page()})})
        tree.clear_backref()
        result = roundtrip(tree)
        assert type(result["data"]) is Bag and type(result["data.page"]) is Page

    def test_name_written_only_when_class_changes(self):
        tree = Source({"same": Source(), "data": Bag({"page": Page()})})
        rows = {row[1]: row[4] for row in from_tytx(tree.to_tytx().removesuffix("::X"))["rows"]}
        assert "__cls" not in rows["same"]
        assert rows["data"]["__cls"] == "Bag"
        assert rows["page"]["__cls"] == "Page"

    @pytest.mark.parametrize("transport", ["json", "msgpack"])
    def test_bag_inside_plain_values(self, transport):
        value = {"source": Source({"a": Bag()}), "list": [Page(), Bag()]}
        result = from_tytx(to_tytx(value, transport), transport)
        assert type(result["source"]) is Source
        assert type(result["source"]["a"]) is Bag
        assert [type(v) for v in result["list"]] == [Page, Bag]

    def test_bag_as_attribute_value(self):
        bag = Bag()
        bag.set_item("n", 1, _attributes={"recipe": Page({"x": 1})})
        result = roundtrip(bag)
        assert type(result.get_node("n").attr["recipe"]) is Page


@pytest.mark.usefixtures("subtypes")
class TestErrors:
    def test_unregistered_subclass_cannot_be_written(self):
        with pytest.raises(BagSerializationError, match="Unregistered"):
            Unregistered().to_tytx()
        with pytest.raises(BagSerializationError, match="Unregistered"):
            Bag({"a": Unregistered()}).to_tytx()

    def test_class_under_two_names_cannot_be_written(self):
        set_subtype_dict("X", {**get_subtype_dict("X"), "Alias": Source})
        with pytest.raises(BagSerializationError, match="several names"):
            Source().to_tytx()

    def test_unknown_branch_name(self):
        payload = to_tytx({"rows": [["", "a", None, "::X", {"__cls": "Missing"}]]}) + "::X"
        with pytest.raises(BagSerializationError, match="Missing"):
            Bag.from_tytx(payload)

    def test_unknown_root_name(self):
        payload = to_tytx({"rows": [], "__cls": "Missing"}) + "::X"
        with pytest.raises(BagSerializationError, match="Missing"):
            Bag.from_tytx(payload)

    def test_user_attribute_is_reserved_on_write(self):
        bag = Bag()
        bag.set_item("a", 1, _attributes={"__cls": "Source"})
        with pytest.raises(BagSerializationError, match="reserved"):
            bag.to_tytx()

    def test_reserved_attribute_on_a_leaf_row(self):
        payload = to_tytx({"rows": [["", "a", None, 1, {"__cls": "Source"}]]}) + "::X"
        with pytest.raises(BagSerializationError, match="reserved"):
            Bag.from_tytx(payload)
