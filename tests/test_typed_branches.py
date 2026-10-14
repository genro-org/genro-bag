# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Contract: registered Bag subclasses retain mixed branch types on the wire."""

import pytest
from genro_tytx import from_tytx, register_class, register_type, to_tytx

from genro_bag import Bag, BagSerializationError


def register_bag_type(cls):
    register_type(cls, cls.__tytx_suffix__, lambda bag: bag._to_tytx_payload(), cls._from_tytx_payload)
    return cls


def marked_rows(data, transport):
    text = to_tytx(data) + "::X"
    if transport == "msgpack":
        import msgpack
        return msgpack.packb(msgpack.ExtType(4, ("X:" + text[:-3]).encode()))
    return text


@register_bag_type
class TypedBranch(Bag):
    __tytx_suffix__ = "TESTBRANCH"


class TestTypedBranches:
    def test_msgpack_scalar_marker_text_is_not_hydrated(self):
        bag = Bag({"raw": "::RAW", "date": "::D"})
        result = Bag.from_tytx(bag.to_tytx("msgpack"), "msgpack")
        assert result["raw"] == "::RAW"
        assert result["date"] == "::D"

    @pytest.mark.parametrize("transport", ["json", "msgpack"])
    def test_unknown_parent_never_moves_children_to_root(self, transport):
        rows = [["", "future", None, "::UNKNOWNTYPE", {}], ["future", "child", None, 42, {}]]
        with pytest.raises(BagSerializationError, match="parent branch"):
            Bag.from_tytx(
                marked_rows({"rows": rows}, transport), transport
            )

    @pytest.mark.parametrize("transport", ["json", "msgpack"])
    @pytest.mark.parametrize("compact", [False, True])
    def test_mixed_tree(self, transport, compact):
        tree = TypedBranch()
        tree.set_item("source", TypedBranch(), node_tag="div")
        tree.set_item("source.data", Bag(), _attributes={"hidden": False})
        tree.set_item("source.data.value", 42)
        tree.set_item("source.data.nested", TypedBranch())
        result = TypedBranch.from_tytx(tree.to_tytx(transport, compact=compact), transport)
        assert type(result) is TypedBranch
        assert type(result["source"]) is TypedBranch
        assert type(result["source.data"]) is Bag
        assert type(result["source.data.nested"]) is TypedBranch
        assert result["source.data.value"] == 42
        assert result.get_node("source").node_tag == "div"
        assert result.get_node("source.data").attr == {"hidden": False}

    @pytest.mark.parametrize("transport", ["json", "msgpack"])
    def test_registered_root_inside_envelope(self, transport):
        result = from_tytx(to_tytx({"source": TypedBranch({"data": Bag()})}, transport), transport)
        assert type(result["source"]) is TypedBranch
        assert type(result["source"]["data"]) is Bag


@register_bag_type
class OtherBranch(Bag):
    __tytx_suffix__ = "OTHERTESTBRANCH"


@register_class
class ScalarWithDecoder:
    __tytx_suffix__ = "TESTSCALAR"

    def to_tytx(self):
        return "valid"

    @classmethod
    def from_tytx(cls, value):
        raise RuntimeError("scalar decoder must not run for literal MessagePack text")


@pytest.mark.parametrize("transport", ["json", "msgpack"])
@pytest.mark.parametrize(
    "paths,parent", [(None, "missing"), ({"0": "branch"}, 99), ({}, 0), (None, 0)]
)
def test_missing_parent(transport, paths, parent):
    data = {"rows": [[parent, "child", None, 42, {}]]}
    if paths is not None:
        data["paths"] = paths
    with pytest.raises(BagSerializationError):
        Bag.from_tytx(marked_rows(data, transport), transport)


def test_literal_custom_scalar():
    source = Bag({"text": "::TESTSCALAR"})
    result = Bag.from_tytx(source.to_tytx("msgpack"), "msgpack")
    assert result["text"] == "::TESTSCALAR"


@pytest.mark.parametrize("transport", ["json", "msgpack"])
def test_ordinary_root_mixed(transport):
    source = Bag({"typed": OtherBranch({"data": Bag({"value": 42})}), "last": 9})
    result = Bag.from_tytx(source.to_tytx(transport), transport)
    assert type(result["typed"]) is OtherBranch
    assert type(result["typed.data"]) is Bag
    assert list(result.keys()) == ["typed", "last"]
