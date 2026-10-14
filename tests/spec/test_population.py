"""Spec test: Bag - populate, deepcopy, update, pickle, from_url.

Depends on test_basic.py (set_item, get_item, get_attr, __len__,
__iter__, __contains__) and test_query.py (query, keys, values, items).

Contracts: in-memory fill_from is atomic and chainable, with None clearing
contents and recursive dict/list conversion. Copying, serialized text and files
use deepcopy, from_tytx, from_xml and from_file explicitly. Update, pickle and
from_url retain their existing contracts.
"""

from __future__ import annotations

import pickle
import re
from pathlib import Path

import pytest

from genro_bag import Bag
from genro_bag.resolvers import BagCbResolver

# =============================================================================
# 1. fill_from(None)
# =============================================================================


class TestFillFromNone:
    def test_none_source_clears(self):
        """fill_from(None) clears contents and returns self."""
        bag = Bag({"a": 1})
        result = bag.fill_from(None)
        assert result is bag
        assert len(bag) == 0

    def test_no_args_equivalent_to_none(self):
        """fill_from() without arguments (source default None) clears contents."""
        bag = Bag({"a": 1})
        bag.fill_from()
        assert len(bag) == 0


# =============================================================================
# 2. fill_from(dict)
# =============================================================================


class TestFillFromDict:
    def test_flat_dict_populates_labels(self):
        """fill_from(dict) creates one node per key."""
        bag = Bag()
        bag.fill_from({"a": 1, "b": 2})
        assert bag.get_item("a") == 1
        assert bag.get_item("b") == 2

    def test_dict_replaces_existing_content(self):
        """fill_from(dict) replaces previous content."""
        bag = Bag({"old": 99})
        bag.fill_from({"new": 42})
        assert bag.get_item("old") is None
        assert bag.get_item("new") == 42

    def test_nested_dict_becomes_nested_bag(self):
        """A nested dict as value becomes a nested Bag."""
        bag = Bag()
        bag.fill_from({"outer": {"inner": 7}})
        # the value of 'outer' is a Bag navigable via dotted path
        assert bag.get_item("outer.inner") == 7


# =============================================================================
# 3. fill_from(list)
# =============================================================================


class TestFillFromList:
    def test_list_creates_numbered_nodes(self):
        """fill_from(list) creates padded labels 'r_00', 'r_01', 'r_02'..."""
        bag = Bag()
        bag.fill_from(["a", "b", "c"])
        assert bag.get_item("r_00") == "a"
        assert bag.get_item("r_01") == "b"
        assert bag.get_item("r_02") == "c"

    def test_list_of_dicts_converts_each_to_bag(self):
        """Dict items in a list become nested Bags."""
        bag = Bag()
        bag.fill_from([{"name": "alice"}, {"name": "bob"}])
        assert bag.get_item("r_00.name") == "alice"
        assert bag.get_item("r_01.name") == "bob"

    def test_empty_list_empties_bag(self):
        """fill_from([]) empties the bag."""
        bag = Bag({"a": 1})
        bag.fill_from([])
        assert bag.keys() == []


# =============================================================================
# 4. fill_from(Bag)
# =============================================================================


class TestExplicitBagCopy:
    def test_copies_nodes_from_source_bag(self):
        """deepcopy copies labels, values and attributes."""
        src = Bag()
        src.set_item("a", 1, _attributes={"type": "int"})
        src.set_item("b", "hi")
        dst = Bag()
        dst = src.deepcopy()
        assert dst.get_item("a") == 1
        assert dst.get_attr("a", "type") == "int"
        assert dst.get_item("b") == "hi"

    def test_nested_bag_is_deep_copied(self):
        """Sub-Bags in source nodes are deep copied."""
        src = Bag()
        src["nest.inner"] = 42
        dst = Bag()
        dst = src.deepcopy()
        # modify the destination, original doesn't change
        dst["nest.inner"] = 99
        assert src.get_item("nest.inner") == 42


# =============================================================================
# 5. fill_from(bytes)
# =============================================================================


class TestExplicitBytesRead:
    def test_bytes_json_inline_is_decoded(self):
        """Bytes containing inline JSON is decoded as JSON."""
        bag = Bag()
        bag = Bag.from_tytx(b'{"a": 1, "b": 2}')
        assert bag.get_item("a") == 1
        assert bag.get_item("b") == 2


# =============================================================================
# 6. fill_from(str) inline XML / JSON (auto-detect)
# =============================================================================


class TestExplicitTextRead:
    def test_json_inline_detected_by_leading_brace(self):
        """String starting with '{' is parsed as JSON."""
        bag = Bag()
        bag = Bag.from_tytx('{"a": 1, "b": 2}')
        assert bag.get_item("a") == 1
        assert bag.get_item("b") == 2

    def test_json_list_inline_detected_by_leading_bracket(self):
        """String starting with '[' is parsed as JSON (list).

        A top-level JSON list produces nodes with 'r_' prefix and index.
        """
        bag = Bag()
        bag = Bag.from_tytx("[10, 20, 30]")
        assert bag.get_item("r_00") == 10
        assert bag.get_item("r_01") == 20
        assert bag.get_item("r_02") == 30

    def test_xml_inline_detected_by_leading_angle(self):
        """String starting with '<' is parsed as XML."""
        bag = Bag()
        bag = Bag.from_xml("<root><a>1</a></root>")
        # the root element is a container wrapping the node 'a'
        assert bag.get_item("root.a") == "1"


# =============================================================================
# 7-9. fill_from(file path) per estensione (JSON / msgpack / XML)
# =============================================================================


class TestFromFile:
    def test_bag_json_file(self, tmp_path: Path):
        """File with .bag.json extension uses JSON transport (TYTX)."""
        # to_tytx automatically adds .bag.json to the filename argument
        src = Bag({"a": 1, "b": "hello"})
        src.to_tytx(filename=str(tmp_path / "sample"), transport="json")
        target = Bag()
        target = Bag.from_file(str(tmp_path / "sample.bag.json"))
        assert target.get_item("a") == 1
        assert target.get_item("b") == "hello"

    def test_bag_mp_file(self, tmp_path: Path):
        """File with .bag.mp extension uses MessagePack transport."""
        src = Bag({"a": 1, "b": "hello"})
        src.to_tytx(filename=str(tmp_path / "sample"), transport="msgpack")
        target = Bag()
        target = Bag.from_file(str(tmp_path / "sample.bag.mp"))
        assert target.get_item("a") == 1
        assert target.get_item("b") == "hello"

    def test_xml_file(self, tmp_path: Path):
        """File with .xml extension is parsed as XML."""
        file = tmp_path / "sample.xml"
        file.write_text("<root><a>1</a></root>", encoding="utf-8")
        bag = Bag()
        bag = Bag.from_file(str(file))
        assert bag.get_item("root.a") == "1"

    def test_file_not_found_raises(self):
        """Non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            Bag.from_file("/nonexistent/path/file.xml")

    def test_unrecognized_extension_raises(self, tmp_path: Path):
        """Unrecognized extension without explicit transport raises ValueError."""
        file = tmp_path / "sample.unknown"
        file.write_text("nothing", encoding="utf-8")
        with pytest.raises(ValueError):
            Bag.from_file(str(file))


# =============================================================================
# 10. fill_from(Path) - pathlib
# =============================================================================


class TestFromFilePath:
    def test_pathlib_path_works_like_str(self, tmp_path: Path):
        """A pathlib.Path is equivalent to a path string."""
        file = tmp_path / "sample.xml"
        file.write_text("<root><a>1</a></root>", encoding="utf-8")
        bag = Bag()
        bag = Bag.from_file(file)
        assert bag.get_item("root.a") == "1"


# =============================================================================
# 11. fill_from con transport esplicito
# =============================================================================


class TestFromFileExplicitTransport:
    def test_transport_overrides_extension(self, tmp_path: Path):
        """transport='json' forces format regardless of extension."""
        src = Bag({"a": 1})
        # save with .bag.json but rename to .dat
        src.to_tytx(filename=str(tmp_path / "sample"), transport="json")
        raw = (tmp_path / "sample.bag.json").read_text(encoding="utf-8")
        weird = tmp_path / "sample.dat"
        weird.write_text(raw, encoding="utf-8")

        target = Bag()
        target = Bag.from_file(str(weird), transport="json")
        assert target.get_item("a") == 1


# =============================================================================
# 12. fill_from raise su tipo invalido
# =============================================================================


class TestFillFromInvalidType:
    def test_unsupported_type_raises_typeerror(self):
        """An unhandled object raises TypeError."""
        with pytest.raises(TypeError):
            Bag().fill_from(42)

    def test_tuple_also_unsupported(self):
        """A tuple (not list) is not supported."""
        with pytest.raises(TypeError):
            Bag().fill_from((1, 2, 3))


# =============================================================================
# 13. fill_from - semantica atomica: errore -> self invariato
# =============================================================================


class TestFillFromAtomic:
    def test_typeerror_on_invalid_source_leaves_bag_unchanged(self):
        """Source with unsupported type: self remains unchanged."""
        bag = Bag({"a": 1, "b": 2})
        with pytest.raises(TypeError):
            bag.fill_from(42)
        # content unchanged
        assert bag.get_item("a") == 1
        assert bag.get_item("b") == 2
        assert bag.keys() == ["a", "b"]

    def test_unsupported_source_leaves_bag_unchanged(self):
        """A path is not a data source: self remains unchanged after rejection."""
        bag = Bag({"a": 1})
        with pytest.raises(TypeError):
            bag.fill_from("/nonexistent/file.xml")
        assert bag.get_item("a") == 1


# =============================================================================
# 14. fill_from chainable
# =============================================================================


class TestFillFromChainable:
    def test_returns_self_for_chaining(self):
        """fill_from returns self for chaining calls."""
        bag = Bag()
        assert bag.fill_from({"a": 1}) is bag


# =============================================================================
# 15. deepcopy
# =============================================================================


class TestDeepcopy:
    def test_deepcopy_creates_independent_bag(self):
        """deepcopy creates new Bag: changes don't propagate."""
        src = Bag({"a": 1, "b": 2})
        copy = src.deepcopy()
        copy["a"] = 99
        assert src.get_item("a") == 1
        assert copy.get_item("a") == 99

    def test_deepcopy_preserves_attributes(self):
        """deepcopy preserves node attributes as independent dict."""
        src = Bag()
        src.set_item("a", 1, _attributes={"type": "int"})
        copy = src.deepcopy()
        assert copy.get_attr("a", "type") == "int"

    def test_deepcopy_recurses_on_nested_bags(self):
        """Sub-Bags are deep copied."""
        src = Bag()
        src["outer.inner"] = 1
        copy = src.deepcopy()
        copy["outer.inner"] = 99
        assert src.get_item("outer.inner") == 1

    def test_deepcopy_same_class(self):
        """Copy is instance of same class (for subclassing)."""
        src = Bag({"a": 1})
        copy = src.deepcopy()
        assert type(copy) is type(src)


# =============================================================================
# 16. update
# =============================================================================


class TestUpdate:
    def test_positional_contract_and_camel_keyword_aliases(self):
        current = Bag({"keep": None, "template": "${KEEP}"})
        incoming = Bag({"keep": 2, "template": "replace", "added": 3})

        current.update(incoming, False, True, re.compile(r"^\$\{"))

        assert current["keep"] == 2
        assert current["template"] == "${KEEP}"
        assert current["added"] == 3

        camel = Bag({"keep": None, "template": "${KEEP}"})
        camel.update(incoming, ignoreNone=True, preservePattern=re.compile(r"^\$\{"))
        assert camel["keep"] == 2
        assert camel["template"] == "${KEEP}"

    def test_preserve_pattern_protects_attributes_without_blocking_value_update(self):
        current = Bag()
        current.set_item("item", "old", _attributes={"caption": "${KEEP}", "other": "old"})
        incoming = Bag()
        incoming.set_item("item", "new", _attributes={"caption": "replace", "other": "new"})

        current.update(incoming, preserve_pattern=re.compile(r"^\$\{"))

        assert current["item"] == "new"
        assert current.get_attr("item", "caption") == "${KEEP}"
        assert current.get_attr("item", "other") == "new"

    def test_unresolved_update_preserves_existing_and_inserted_resolvers(self):
        calls = []
        source = Bag()
        source["existing"] = BagCbResolver(lambda: calls.append("existing") or 10)
        source["inserted"] = BagCbResolver(lambda: calls.append("inserted") or 20)
        target = Bag({"existing": 1})

        target.update(source)

        assert calls == []
        assert target.get_resolver("existing") is source.get_resolver("existing")
        assert target.get_resolver("inserted") is source.get_resolver("inserted")
        assert target.get_item("existing") == 10
        assert target.get_item("inserted") == 20
        assert calls == ["existing", "inserted"]

    def test_resolved_update_executes_and_copies_plain_values(self):
        calls = []
        source = Bag()
        source["existing"] = BagCbResolver(lambda: calls.append("existing") or 10)
        source["inserted"] = BagCbResolver(lambda: calls.append("inserted") or 20)
        target = Bag()
        target["existing"] = BagCbResolver(lambda: -1)

        target.update(source, True)

        assert calls == ["existing", "inserted"]
        assert target.get_resolver("existing") is None
        assert target.get_resolver("inserted") is None
        assert target["existing"] == 10
        assert target["inserted"] == 20

    def test_bag_update_preserves_nested_null_attrs_and_tags(self):
        source = Bag()
        branch = Bag()
        inner = branch.set_item("inner", 2, _attributes={"nullable": None},
                                _remove_null_attributes=False, node_tag="innerTag")
        inner.xml_tag = "innerXml"
        outer = source.set_item("outer", branch, _attributes={"nullable": None},
                                _remove_null_attributes=False, node_tag="outerTag")
        outer.xml_tag = "outerXml"
        target = Bag({"outer": {"kept": 1}})

        target.update(source)

        assert target["outer.kept"] == 1
        assert target["outer.inner"] == 2
        assert target.get_node("outer").attr == {"nullable": None}
        assert target.get_node("outer").node_tag == "outerTag"
        assert target.get_node("outer").xml_tag == "outerXml"
        assert target.get_node("outer.inner").attr == {"nullable": None}
        assert target.get_node("outer.inner").node_tag == "innerTag"
        assert target.get_node("outer.inner").xml_tag == "innerXml"

    def test_update_accepts_xml_and_dict_and_emits_value_and_insert_events(self):
        target = Bag({"existing": 1})
        events = []
        target.set_backref()
        target.subscribe("audit", update=lambda **kw: events.append(("upd", kw["node"].label)),
                         insert=lambda **kw: events.append(("ins", kw["node"].label)))

        target.update("<GenRoBag><existing _T=\"L\">2</existing><xmlnew>yes</xmlnew></GenRoBag>")
        target.update({"dictnew": 3})

        assert target["existing"] == 2
        assert target["xmlnew"] == "yes"
        assert target["dictnew"] == 3
        assert events == [("upd", "existing"), ("ins", "xmlnew"), ("ins", "dictnew")]

    def test_update_with_dict_adds_new_and_overwrites(self):
        """update(dict) adds new keys and overwrites existing."""
        bag = Bag({"a": 1, "b": 2})
        bag.update({"a": 10, "c": 3})
        assert bag.get_item("a") == 10
        assert bag.get_item("b") == 2
        assert bag.get_item("c") == 3

    def test_update_with_bag_merges_attributes(self):
        """update(Bag) also merges attributes of existing nodes."""
        dst = Bag()
        dst.set_item("a", 1, _attributes={"x": 10})
        src = Bag()
        src.set_item("a", 2, _attributes={"y": 20})
        dst.update(src)
        assert dst.get_item("a") == 2
        assert dst.get_attr("a", "x") == 10
        assert dst.get_attr("a", "y") == 20

    def test_update_ignore_none_preserves_existing(self):
        """With ignore_none=True, None values don't overwrite."""
        bag = Bag({"a": 1})
        bag.update({"a": None}, ignore_none=True)
        assert bag.get_item("a") == 1

    def test_update_ignore_none_false_overwrites(self):
        """With ignore_none=False (default), None overwrites."""
        bag = Bag({"a": 1})
        bag.update({"a": None})
        assert bag.get_item("a") is None

    def test_update_recurses_on_nested_bags(self):
        """If both have Bag at same label, update is recursive."""
        dst = Bag()
        dst["outer.a"] = 1
        dst["outer.b"] = 2
        src = Bag()
        src["outer.a"] = 99
        src["outer.c"] = 3
        dst.update(src)
        assert dst.get_item("outer.a") == 99
        assert dst.get_item("outer.b") == 2
        assert dst.get_item("outer.c") == 3

    def test_update_carries_node_tag_on_new_nodes(self):
        """A node added by update keeps its node_tag and xml_tag."""
        dst = Bag()
        src = Bag()
        node = src.set_item("a", 1, node_tag="alpha")
        node.xml_tag = "alphaXml"
        dst.update(src)
        added = dst.get_node("a")
        assert added.node_tag == "alpha"
        assert added.xml_tag == "alphaXml"

    def test_update_incoming_tag_wins_on_collision(self):
        """On label collision a non-None incoming tag wins (as in set_item)."""
        dst = Bag()
        dst.set_item("a", 1, node_tag="old")
        src = Bag()
        src.set_item("a", 2, node_tag="new")
        dst.update(src)
        assert dst.get_node("a").node_tag == "new"

    def test_update_none_incoming_tag_keeps_existing(self):
        """On label collision an incoming None tag leaves the target's tag."""
        dst = Bag()
        dst.set_item("a", 1, node_tag="kept")
        src = Bag({"a": 2})
        dst.update(src)
        assert dst.get_node("a").node_tag == "kept"
        assert dst.get_item("a") == 2

    def test_update_carries_tags_in_nested_recursion(self):
        """Tags travel on nodes added inside a recursive bag-bag merge."""
        dst = Bag()
        dst.set_item("outer", Bag(), node_tag="outer")
        src = Bag()
        src.set_item("outer", Bag(), node_tag="outer")
        src["outer"].set_item("inner", 1, node_tag="inner")
        dst.update(src)
        assert dst.get_node("outer").node_tag == "outer"
        assert dst["outer"].get_node("inner").node_tag == "inner"

    def test_update_from_dict_leaves_tags_untouched(self):
        """A dict source carries no tags: existing tags survive, new nodes have none."""
        bag = Bag()
        bag.set_item("a", 1, node_tag="alpha")
        bag.update({"a": 10, "c": 3})
        assert bag.get_node("a").node_tag == "alpha"
        assert bag.get_node("c").node_tag is None


# =============================================================================
# 17. pickle roundtrip
# =============================================================================


class TestPickle:
    def test_roundtrip_preserves_values(self):
        """Pickle-unpickle ricostruisce valori identici."""
        src = Bag({"a": 1, "b": "hi"})
        restored = pickle.loads(pickle.dumps(src))
        assert restored.get_item("a") == 1
        assert restored.get_item("b") == "hi"

    def test_roundtrip_preserves_attributes(self):
        """Pickle preserves node attributes."""
        src = Bag()
        src.set_item("a", 1, _attributes={"type": "int"})
        restored = pickle.loads(pickle.dumps(src))
        assert restored.get_attr("a", "type") == "int"

    def test_roundtrip_preserves_nested(self):
        """Pickle preserva strutture annidate."""
        src = Bag()
        src["outer.inner"] = 42
        restored = pickle.loads(pickle.dumps(src))
        assert restored.get_item("outer.inner") == 42

    def test_roundtrip_preserves_backref_state(self):
        """Pickling a Bag with backref on gives back a Bag with backref on.

        Scenario: salviamo una Bag "vivente" (pronta a notificare) e dobbiamo
        ricostruirla nello stesso stato dopo l'unpickle.
        """
        src = Bag()
        src["outer.inner"] = 42
        src.set_backref()
        assert src.backref is True
        data = pickle.dumps(src)
        restored = pickle.loads(data)
        assert restored.backref is True
        # the nested sub-Bag must have backref enabled too
        inner = restored.get_item("outer")
        assert isinstance(inner, Bag)
        assert inner.backref is True

    def test_roundtrip_without_backref_stays_without_backref(self):
        """A Bag pickled without backref stays without it after the restore."""
        src = Bag()
        src["outer.inner"] = 42
        assert src.backref is False
        restored = pickle.loads(pickle.dumps(src))
        assert restored.backref is False


# =============================================================================
# 18. from_url (marker network - smoke test)
# =============================================================================


class TestFromUrl:
    def test_from_url_json_endpoint(self, http_server):
        """from_url fetches a JSON endpoint and parses it."""
        bag = Bag.from_url(http_server.make_url("/json"))
        assert "slideshow" in bag
        assert bag["slideshow.title"] == "Sample Slide Show"
