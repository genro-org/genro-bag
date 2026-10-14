"""Shared Python/JS ingestion contract introduced for issue #69."""
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from genro_toolbox import SignatureError
from genro_tytx import from_tytx, to_tytx

from genro_bag import Bag, BagNode
from genro_bag.resolver import BagSyncResolver


class IngestionResolver(BagSyncResolver):
    calls = 0

    def load(self):
        type(self).calls += 1
        return "loaded"


@pytest.mark.parametrize("source", [0, False, "", "{}", b"{}", (), Path("x"), Bag()])
def test_root_rejections_are_atomic(source):
    with pytest.raises(TypeError):
        Bag(source)
    bag = Bag({"old": 1})
    node = bag.get_node("old")
    with pytest.raises(TypeError):
        bag.fill_from(source)
    assert bag.get_node("old") is node


@pytest.mark.parametrize("size", [0, 1, 5, 9, 10, 25, 99, 100, 150])
def test_list_labels_and_order(size):
    bag = Bag(list(range(size)))
    labels = [f"r_{i:0{len(str(size)) + 1}d}" for i in range(size)]
    assert bag.keys() == labels == sorted(labels)
    assert bag.values() == list(range(size))


def test_recursive_converts_containers_and_shares_leaves():
    class Custom:
        pass

    leaves = [datetime(2026, 10, 3, tzinfo=UTC), Decimal("12.30"),
              b"binary", bytearray(b"mutable"), Custom(), BagNode(None, "node", 1), Bag({"kept": [1]}, recursive=False), (1, 2)]
    for leaf in leaves:
        data = {"outer": [{"value": leaf}]}
        bag = Bag(data)
        assert isinstance(bag["outer"], Bag)
        assert bag["outer.r_00.value"] is leaf
        data["outer"][0]["value"] = "changed"
        assert bag["outer.r_00.value"] is leaf
    assert isinstance(leaves[-2]["kept"], list)


def test_shallow_leaves_containers_shared():
    data = {"dict": {"x": 1}, "list": [1, 2]}
    bag = Bag(data, recursive=False)
    assert bag["dict"] is data["dict"]
    assert bag["list"] is data["list"]
    assert Bag([data], recursive=False)["r_00"] is data
    assert bag.fill_from(None) is bag
    assert not bag


def test_cycles_and_repeated_references():
    data = {"inner": []}
    data["inner"].append(data)
    bag = Bag({"old": 1})
    with pytest.raises(ValueError, match="Cyclic.*inner"):
        bag.fill_from(data)
    assert bag["old"] == 1
    assert Bag(data, recursive=False)["inner"] is data["inner"]
    child = {"x": 1}
    repeated = Bag({"a": child, "b": child})
    assert repeated["a"] is not repeated["b"]


@pytest.mark.parametrize("transport", ["json", "msgpack"])
@pytest.mark.parametrize("compact", [False, True])
def test_literal_labels_and_structure_roundtrip(transport, compact):
    data = {"a.b": {"c": 1}, "a": {"b.c": 2}, "?attr": 3, "#0": 4, "": 5,
            "__proto__": 6, "constructor": 7, "literal::X": {"leaf::N": 9}}
    original = Bag(data)
    decoded = Bag.from_tytx(original.to_tytx(transport, compact=compact), transport)
    assert decoded.keys() == list(data)
    assert decoded._nodes["a.b"].value["c"] == 1
    assert decoded["a"]._nodes["b.c"].value == 2
    for label in ("?attr", "#0", "", "__proto__", "constructor"):
        assert decoded._nodes[label].value == data[label]
    assert decoded._nodes["literal::X"].value._nodes["leaf::N"].value == 9
    assert original.deepcopy().keys() == list(data)


def test_non_string_key_rejected_before_replacement():
    bag = Bag({"old": 1})
    with pytest.raises(TypeError, match="strings"):
        bag.fill_from({"valid": 1, 1: 2})
    assert bag.keys() == ["old"]


@pytest.mark.parametrize("recursive", [True, False])
def test_generic_tytx_and_plain_json_use_same_conversion(recursive):
    data = {"rows": [{"label": "Ada", "value": 1, "attr": {"x": 2}}]}
    expected = Bag(data, recursive=recursive)
    for wire in (json.dumps(data), to_tytx(data)):
        actual = Bag.from_tytx(wire, recursive=recursive)
        assert actual == expected
    with pytest.warns(DeprecationWarning):
        assert Bag.from_json(json.dumps(data), recursive=recursive) == expected


@pytest.mark.parametrize("transport", ["json", "msgpack"])
def test_typed_ingestion_and_marked_bag(transport):
    leaf = {"date": datetime(2026, 10, 3, 9, 30, tzinfo=UTC), "decimal": Decimal("12.30")}
    data = {"nested": [leaf]}
    bag = Bag.from_tytx(to_tytx(data, None if transport == "json" else transport), transport)
    assert bag["nested.r_00.date"] == leaf["date"]
    assert bag["nested.r_00.decimal"] == leaf["decimal"]
    original = Bag(data, recursive=False)
    wire = original.to_tytx(transport)
    if transport == "json":
        assert wire.endswith("::X") and not wire.endswith("::X::X")
    assert isinstance(from_tytx(wire, None if transport == "json" else transport), Bag)
    for recursive in (True, False):
        restored = Bag.from_tytx(wire, transport, recursive=recursive)
        assert isinstance(restored["nested"], list)
        assert restored["nested"][0] == leaf


@pytest.mark.parametrize("wire", ['"text"', '42', 'false', 'null', '', 'not-json'])
def test_serialized_scalar_rejected(wire):
    with pytest.raises(TypeError):
        Bag.from_tytx(wire)


@pytest.mark.parametrize("transport", ["json", "msgpack"])
def test_signed_marked_bag_and_generic_nested_bag_require_signature(transport):
    IngestionResolver.calls = 0
    bag = Bag()
    bag.set_item("r", IngestionResolver())
    wire = bag.to_tytx(transport, sign_key="secret")
    assert isinstance(Bag.from_tytx(wire, transport, sign_key="secret").get_resolver("r"), IngestionResolver)
    with pytest.raises(SignatureError):
        Bag.from_tytx(wire, transport, sign_key="wrong")
    unsigned = to_tytx({"wrapped": [bag]}, None if transport == "json" else transport)
    with pytest.raises(SignatureError):
        Bag.from_tytx(unsigned, transport, sign_key="secret")
    assert IngestionResolver.calls == 0


@pytest.mark.parametrize("transport", ["json", "msgpack"])
def test_marker_like_strings_are_literal_leaves(transport):
    values = ["::X", "::NN", "::T", "12::N", "::RAW", "::D"]
    bag = Bag({"values": values}, recursive=False)
    bag.set_item("scalar", "::X", _attributes={"marker": "::NN"}, node_tag="kind::X")
    restored = Bag.from_tytx(bag.to_tytx(transport), transport)
    assert restored["values"] == values
    assert restored["scalar"] == "::X"
    assert restored.get_attr("scalar", "marker") == "::NN"
    assert restored.get_node("scalar").node_tag == "kind::X"
