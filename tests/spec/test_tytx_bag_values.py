# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Contracts for registered Bag values and nested resolver signing boundaries."""

import json

import pytest
from genro_toolbox import SignatureError
from genro_tytx.registry import SUFFIX_TO_TYPE, TYPE_REGISTRY

from genro_bag import Bag, BagSerializationError
from genro_bag.resolvers import UuidResolver


class TestBagAsTytxCustomType:
    """Bag is registered with genro-tytx under its historical suffix "X".

    Two meanings share the token and are told apart by the payload: an EMPTY
    payload ("::X") is the branch-node marker genro-bag's own flattener emits
    at row level, while "<payload>::X" is a whole serialized Bag travelling
    inside a plain dict or list value.
    """

    def test_bag_declares_the_x_suffix(self):
        """The suffix genro-tytx reads off the class is "X"."""
        assert Bag.__tytx_suffix__ == "X"

    def test_bag_is_in_the_tytx_registry(self):
        """Importing genro_bag registers Bag as a TYTX type."""
        assert TYPE_REGISTRY[Bag][0] == "X"
        assert SUFFIX_TO_TYPE["X"][0] is Bag

    def test_from_tytx_of_an_empty_payload_is_an_empty_bag(self):
        """The branch-node marker decodes to a Bag with no children."""
        bag = Bag.from_tytx("::X")
        assert isinstance(bag, Bag)
        assert len(bag) == 0

    def test_from_tytx_of_empty_bytes_is_an_empty_bag(self):
        """The bytes twin of the empty payload decodes the same way."""
        bag = Bag.from_tytx(Bag().to_tytx("msgpack"), transport="msgpack")
        assert isinstance(bag, Bag)
        assert len(bag) == 0

    def test_from_tytx_of_none_raises(self):
        """Only the empty payload means a branch node; None is a caller error."""
        with pytest.raises(TypeError):
            Bag.from_tytx(None)  # type: ignore[arg-type]

    def test_branch_marker_still_travels_as_double_colon_x(self):
        """Registration must not change the wire shape of a branch node.

        The row slot must hold exactly "::X" (empty payload): a substring
        check would also match a whole-Bag "<payload>::X" encoding.
        """
        bag = Bag()
        bag["a.b"] = 1
        rows = json.loads(bag._to_tytx_payload().removesuffix("::JS"))["rows"]
        assert rows[0][3] == "::X"

    def test_branch_nodes_still_round_trip(self):
        """A nested Bag built as a branch survives the round-trip."""
        bag = Bag()
        bag["a.b"] = 1
        back = Bag.from_tytx(bag.to_tytx())
        assert isinstance(back["a"], Bag)
        assert back["a.b"] == 1

    def test_branch_nodes_still_round_trip_msgpack(self):
        """A nested Bag built as a branch survives the msgpack round-trip.

        Unlike json, msgpack strings are not re-scanned for suffixes on
        decode, so the row-level "::X" marker reaches the parser as a
        literal string — the parser must recognise both forms.
        """
        bag = Bag()
        bag["a.b"] = 1
        back = Bag.from_tytx(bag.to_tytx(transport="msgpack"), transport="msgpack")
        assert isinstance(back["a"], Bag)
        assert back["a.b"] == 1

    def test_bag_nested_in_a_dict_value_round_trips_json(self):
        """A Bag carried inside a plain dict value comes back as a Bag."""
        inner = Bag()
        inner["x"] = 1
        inner["y"] = "two"
        bag = Bag()
        bag["payload"] = {"nested": inner, "n": 7}
        back = Bag.from_tytx(bag.to_tytx())
        payload = back["payload"]
        assert payload["n"] == 7
        assert isinstance(payload["nested"], Bag)
        assert payload["nested"]["x"] == 1
        assert payload["nested"]["y"] == "two"

    def test_bag_nested_in_a_dict_value_round_trips_msgpack(self):
        """Same as the json case, over the msgpack transport.

        Environment canary: under a narrowed coverage scope the msgpack C packer stops
        recognising ``ExtType`` and degrades it to a plain list, so this
        test fails with ``[4, b'X:...']`` instead of a Bag. That is a
        coverage/C-extension interaction outside genro-bag — do not chase
        it here; run with the project's configured coverage scope.
        """
        inner = Bag()
        inner["x"] = 1
        bag = Bag()
        bag["payload"] = {"nested": inner}
        back = Bag.from_tytx(bag.to_tytx(transport="msgpack"), transport="msgpack")
        assert isinstance(back["payload"]["nested"], Bag)
        assert back["payload"]["nested"]["x"] == 1


class TestNestedBagJsonValues:
    def test_bag_in_plain_dict_preserves_content(self):
        """Generic JSON serialization retains Bag values inside plain containers."""
        inner = Bag({"x": 1, "y": "two"})
        source = Bag()
        source["payload"] = {"nested": inner}
        restored = Bag.from_tytx(source.to_tytx())
        assert isinstance(restored["payload"]["nested"], Bag)
        assert restored["payload"]["nested"]["x"] == 1
        assert restored["payload"]["nested"]["y"] == "two"


class TestNestedBagSigningRefusal:
    """A Bag nested in a plain dict/list travels through the TYTX type
    registry, whose hooks take no sign_key: a resolver in there can be
    neither signed on encode nor verified on decode. Both sides refuse
    rather than letting the resolver bypass the signature.
    """

    def _bag_with_nested_resolver(self):
        inner = Bag()
        inner["id"] = UuidResolver("uuid4")
        bag = Bag()
        bag["d"] = {"inner": inner}
        return bag

    def test_signed_encode_refuses_a_nested_resolver(self):
        """to_tytx with sign_key refuses what it cannot sign."""
        bag = self._bag_with_nested_resolver()
        with pytest.raises(BagSerializationError):
            bag.to_tytx(sign_key="secret")

    def test_signed_decode_refuses_a_nested_resolver(self):
        """from_tytx with sign_key refuses what it cannot verify."""
        wire = self._bag_with_nested_resolver().to_tytx()
        with pytest.raises(SignatureError):
            Bag.from_tytx(wire, sign_key="secret")

    def test_unsigned_round_trip_still_carries_the_nested_resolver(self):
        """Without a key the nested resolver keeps travelling as before."""
        wire = self._bag_with_nested_resolver().to_tytx()
        back = Bag.from_tytx(wire)
        assert isinstance(back["d"]["inner"].get_resolver("id"), UuidResolver)

    def test_signed_encode_of_a_nested_bag_without_resolvers_passes(self):
        """The refusal is about resolvers, not about nesting itself."""
        inner = Bag()
        inner["x"] = 1
        bag = Bag()
        bag["d"] = {"inner": inner}
        back = Bag.from_tytx(bag.to_tytx(sign_key="secret"), sign_key="secret")
        assert back["d"]["inner"]["x"] == 1

    def _bag_with_nested_resolver_in_attribute(self):
        inner = Bag()
        inner["id"] = UuidResolver("uuid4")
        bag = Bag()
        bag.set_item("n", 1, _attributes={"meta": {"inner": inner}})
        return bag

    def test_signed_tytx_refuses_a_nested_resolver_in_an_attribute(self):
        """The attribute slot rides the same registry: encode refuses too."""
        bag = self._bag_with_nested_resolver_in_attribute()
        with pytest.raises(BagSerializationError):
            bag.to_tytx(sign_key="secret")

    def test_signed_tytx_decode_refuses_a_nested_resolver_in_an_attribute(self):
        """An unsigned attribute-borne nested resolver is refused on decode."""
        wire = self._bag_with_nested_resolver_in_attribute().to_tytx()
        with pytest.raises(SignatureError):
            Bag.from_tytx(wire, sign_key="secret")

    def test_signed_to_json_refuses_a_nested_resolver_in_a_value(self):
        """to_json rides the same registry for plain values: encode refuses."""
        bag = self._bag_with_nested_resolver()
        with pytest.raises(BagSerializationError):
            bag.to_json(sign_key="secret")

    def test_signed_to_json_refuses_a_nested_resolver_in_an_attribute(self):
        """to_json shares encode_attrs with the tytx rail: same refusal."""
        bag = self._bag_with_nested_resolver_in_attribute()
        with pytest.raises(BagSerializationError):
            bag.to_json(sign_key="secret")

    def test_signed_from_json_refuses_a_nested_resolver_in_a_value(self):
        """An unsigned nested resolver in a json payload is refused on decode."""
        wire = self._bag_with_nested_resolver().to_json()
        with pytest.raises(SignatureError):
            Bag.from_json(wire, sign_key="secret")
