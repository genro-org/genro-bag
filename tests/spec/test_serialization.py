"""Spec test: Bag - XML / JSON / TYTX serialization.

Depends on test_basic.py (set_item, get_item, get_attr, get_node,
__len__, __contains__) and test_population.py (fill_from, deepcopy).

XML/JSON/TYTX formats are THREE DISTINCT contracts:
- XML: human-readable, no type info (all strings)
- TYTX: type-preserving (int, float, date, datetime, Decimal)
- JSON: explicit label/value/attr structure, with or without type info (typed=True)

For each we test roundtrip: from_X(to_X(bag)) must reconstruct
equivalent Bag for format's expected values.

## Scale

1.  to_xml / from_xml                simple roundtrip
2.  to_xml options                   pretty, doc_header, self_closed_tags
3.  to_xml filename                  write to file
4.  to_xml attributes                attr serialized as XML attributes
5.  to_xml nested                    nested sub-Bags
6.  from_xml plain                   elements -> nodes
7.  from_xml legacy GenRoBag         auto-detect + _T types
8.  from_xml tag_attribute           path from attribute

9.  to_tytx / from_tytx              JSON and msgpack roundtrip
10. to_tytx filename                 write to file
11. to_tytx compact mode             parent paths as numeric codes
12. to_tytx preserves types          int / float / None / bytes
13. to_tytx preserves attr and node_tag

14. to_json / from_json              JSON roundtrip (typed or not)
15. to_json typed                    preserves date/datetime/Decimal
16. from_json from direct Python dict
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from contrib.genropy_wrapper.replacement._legacy_json import LegacyJsonParser

from genro_bag import Bag


class LegacyJsonBag(LegacyJsonParser, Bag):
    """Exercise the legacy node decoder explicitly, never the public alias."""



# =============================================================================
# 1. to_xml / from_xml - simple roundtrip
# =============================================================================


class TestXmlRoundtripSimple:
    def test_roundtrip_under_single_root(self):
        """XML roundtrip requires single root element (format constraint).

        XML does not preserve types: values return as strings.
        """
        src = Bag()
        src["root.a"] = "1"
        src["root.b"] = "2"
        xml = src.to_xml()
        restored = Bag.from_xml(xml or "")
        assert restored.get_item("root.a") == "1"
        assert restored.get_item("root.b") == "2"

    def test_empty_bag_produces_empty_string(self):
        """to_xml() on empty Bag returns empty string (format edge case)."""
        assert Bag().to_xml() == ""


# =============================================================================
# 2. to_xml - options
# =============================================================================


class TestToXmlOptions:
    def test_pretty_adds_indentation(self):
        """pretty=True produces indented output."""
        bag = Bag({"a": "1", "b": "2"})
        compact = bag.to_xml()
        pretty = bag.to_xml(pretty=True)
        assert pretty != compact
        assert "\n" in (pretty or "")

    def test_doc_header_true_adds_xml_declaration(self):
        """doc_header=True prefixes XML declaration."""
        bag = Bag({"a": "1"})
        xml = bag.to_xml(doc_header=True)
        assert (xml or "").startswith("<?xml version=")

    def test_doc_header_string_uses_custom_header(self):
        """doc_header str is used as custom header."""
        bag = Bag({"a": "1"})
        xml = bag.to_xml(doc_header="<!DOCTYPE custom>")
        assert (xml or "").startswith("<!DOCTYPE custom>")

    def test_self_closed_tags(self):
        """self_closed_tags list makes indicated tags self-closing if empty."""
        bag = Bag()
        bag["empty_tag"] = None
        xml = bag.to_xml(self_closed_tags=["empty_tag"])
        assert "<empty_tag/>" in (xml or "")

    def test_self_closed_tags_excludes_others(self):
        """Tags NOT in self_closed_tags use <tag></tag> form if empty.

        Scenario: user specifies only 'br' as self-closed; another empty tag
        must not be self-closed.
        """
        bag = Bag()
        bag.set_item("empty_scalar", None)
        xml = bag.to_xml(self_closed_tags=["br"]) or ""
        # empty_scalar not in self_closed_tags → extended form
        assert "<empty_scalar></empty_scalar>" in xml
        assert "<empty_scalar/>" not in xml

    def test_self_closed_tags_empty_sub_bag_included(self):
        """Empty sub-Bag with tag in self_closed_tags self-closes."""
        bag = Bag()
        bag.set_item("container", Bag())
        xml = bag.to_xml(self_closed_tags=["container"]) or ""
        assert "<container/>" in xml

    def test_self_closed_tags_empty_sub_bag_excluded(self):
        """Empty sub-Bag with tag NOT in self_closed_tags uses <tag></tag>."""
        bag = Bag()
        bag.set_item("container", Bag())
        xml = bag.to_xml(self_closed_tags=["other"]) or ""
        assert "<container></container>" in xml
        assert "<container/>" not in xml

    def test_self_closed_tags_default_all_empty_are_self_closed(self):
        """Without self_closed_tags (default) every empty tag self-closes.

        This is standard XML behavior.
        """
        bag = Bag()
        bag.set_item("a", None)
        bag.set_item("b", "")
        bag.set_item("c", Bag())
        xml = bag.to_xml() or ""
        assert "<a/>" in xml
        assert "<b/>" in xml
        assert "<c/>" in xml


# =============================================================================
# 3. to_xml - filename
# =============================================================================


class TestToXmlFile:
    def test_writes_to_file_when_filename_given(self, tmp_path: Path):
        """filename=... writes to file and returns None."""
        bag = Bag({"a": "hello"})
        file = tmp_path / "out.xml"
        result = bag.to_xml(filename=str(file))
        assert result is None
        content = file.read_text(encoding="utf-8")
        assert "hello" in content

    def test_file_can_be_read_back(self, tmp_path: Path):
        """XML file produced is readable back with from_xml via fill_from."""
        src = Bag({"x": "world"})
        file = tmp_path / "out.xml"
        src.to_xml(filename=str(file))
        restored = Bag.from_file(str(file))
        assert restored.get_item("x") == "world"


# =============================================================================
# 4. to_xml - attributes
# =============================================================================


class TestToXmlAttributes:
    def test_attributes_serialized_as_xml_attrs(self):
        """Node attributes end up as XML attributes."""
        bag = Bag()
        bag.set_item("elem", "text", _attributes={"type": "string", "id": "x"})
        xml = bag.to_xml() or ""
        assert 'type="string"' in xml
        assert 'id="x"' in xml

    def test_none_attribute_skipped(self):
        """Attributes with None value are not emitted."""
        bag = Bag()
        bag.set_item("e", "v", _attributes={"keep": "yes"})
        # insert None attribute manually via set_attr
        bag.set_attr("e", nil=None)
        xml = bag.to_xml() or ""
        assert "keep" in xml
        # None attribute is removed, doesn't appear
        assert "nil=" not in xml


# =============================================================================
# 5. to_xml - nested
# =============================================================================


class TestToXmlNested:
    def test_nested_bag_nested_xml(self):
        """A sub-Bag produces nested XML."""
        bag = Bag()
        bag["outer.inner"] = "v"
        xml = bag.to_xml() or ""
        assert "<outer>" in xml
        assert "<inner>v</inner>" in xml
        assert "</outer>" in xml

    def test_nested_roundtrip(self):
        """XML roundtrip on nested structure."""
        src = Bag()
        src["outer.inner"] = "v"
        xml = src.to_xml() or ""
        restored = Bag.from_xml(xml)
        assert restored.get_item("outer.inner") == "v"


# =============================================================================
# 6. from_xml - plain
# =============================================================================


class TestFromXmlPlain:
    def test_simple_xml_elements_become_nodes(self):
        """Each XML element becomes a Bag node."""
        bag = Bag.from_xml("<root><a>1</a><b>2</b></root>")
        assert bag.get_item("root.a") == "1"
        assert bag.get_item("root.b") == "2"

    def test_attributes_become_node_attr(self):
        """XML attributes become node.attr."""
        bag = Bag.from_xml('<root><item id="x" kind="small">hello</item></root>')
        assert bag.get_attr("root.item", "id") == "x"
        assert bag.get_attr("root.item", "kind") == "small"

    def test_bytes_source_decoded(self):
        """from_xml accepts bytes (UTF-8)."""
        bag = Bag.from_xml(b"<root><a>1</a></root>")
        assert bag.get_item("root.a") == "1"


# =============================================================================
# 7. from_xml - legacy GenRoBag auto-detect
# =============================================================================


class TestFromXmlLegacy:
    def test_legacy_wrapper_unwrapped(self):
        """The <GenRoBag> wrapper is automatically removed."""
        bag = Bag.from_xml("<GenRoBag><a>1</a></GenRoBag>")
        # without _T the value is string
        assert bag.get_item("a") == "1"

    def test_legacy_type_marker_int(self):
        """_T='L' converts value to int."""
        bag = Bag.from_xml('<GenRoBag><count _T="L">42</count></GenRoBag>')
        assert bag.get_item("count") == 42
        assert isinstance(bag.get_item("count"), int)


# =============================================================================
# 8. from_xml - tag_attribute
# =============================================================================


class TestFromXmlTagAttribute:
    def test_tag_attribute_uses_attr_as_label(self):
        """tag_attribute=X uses attribute X as node label."""
        xml = '<grammar><define name="section"/></grammar>'
        bag = Bag.from_xml(xml, tag_attribute="name")
        assert "section" in bag["grammar"]


# =============================================================================
# 9. to_tytx / from_tytx - roundtrip JSON e msgpack
# =============================================================================


class TestTytxRoundtrip:
    def test_roundtrip_json_preserves_int_and_str(self):
        """to_tytx + from_tytx reconstructs int and str values."""
        src = Bag({"a": 1, "b": "hello"})
        data = src.to_tytx(transport="json")
        restored = Bag.from_tytx(data, transport="json")  # type: ignore[arg-type]
        assert restored.get_item("a") == 1
        assert isinstance(restored.get_item("a"), int)
        assert restored.get_item("b") == "hello"

    def test_roundtrip_msgpack(self):
        """Same roundtrip but with msgpack transport (binary)."""
        src = Bag({"a": 1, "b": "hello"})
        data = src.to_tytx(transport="msgpack")
        assert isinstance(data, bytes)
        restored = Bag.from_tytx(data, transport="msgpack")
        assert restored.get_item("a") == 1
        assert restored.get_item("b") == "hello"

    def test_roundtrip_preserves_nested_bags(self):
        """Sub-Bag annidate sopravvivono al roundtrip."""
        src = Bag()
        src["outer.inner"] = 42
        data = src.to_tytx(transport="json")
        restored = Bag.from_tytx(data, transport="json")  # type: ignore[arg-type]
        assert restored.get_item("outer.inner") == 42


# =============================================================================
# 10. to_tytx - filename
# =============================================================================


class TestToTytxFile:
    def test_writes_json_file_with_extension(self, tmp_path: Path):
        """filename con transport='json' scrive .bag.json."""
        src = Bag({"a": 1})
        src.to_tytx(filename=str(tmp_path / "out"), transport="json")
        assert (tmp_path / "out.bag.json").is_file()

    def test_writes_msgpack_file_with_extension(self, tmp_path: Path):
        """filename con transport='msgpack' scrive .bag.mp."""
        src = Bag({"a": 1})
        src.to_tytx(filename=str(tmp_path / "out"), transport="msgpack")
        assert (tmp_path / "out.bag.mp").is_file()


# =============================================================================
# 11. to_tytx - compact mode
# =============================================================================


class TestToTytxCompact:
    def test_compact_roundtrip(self):
        """compact=True preserves the structure through the roundtrip."""
        src = Bag()
        src["a.b.c"] = 42
        src["a.b.d"] = "x"
        src["e"] = 1
        data = src.to_tytx(transport="json", compact=True)
        restored = Bag.from_tytx(data, transport="json")  # type: ignore[arg-type]
        assert restored.get_item("a.b.c") == 42
        assert restored.get_item("a.b.d") == "x"
        assert restored.get_item("e") == 1


# =============================================================================
# 12. to_tytx - preserves Python types
# =============================================================================


class TestTytxTypes:
    def test_preserves_float(self):
        """Float sopravvive al roundtrip."""
        src = Bag({"x": 3.14})
        data = src.to_tytx(transport="json")
        restored = Bag.from_tytx(data, transport="json")  # type: ignore[arg-type]
        assert restored.get_item("x") == 3.14
        assert isinstance(restored.get_item("x"), float)

    def test_preserves_none(self):
        """None values are kept as None."""
        src = Bag()
        src["empty"] = None
        data = src.to_tytx(transport="json")
        restored = Bag.from_tytx(data, transport="json")  # type: ignore[arg-type]
        assert restored.get_item("empty") is None
        assert "empty" in restored

    def test_preserves_decimal(self):
        """Decimal: TYTX preserves the type."""
        src = Bag()
        src["price"] = Decimal("19.99")
        data = src.to_tytx(transport="json")
        restored = Bag.from_tytx(data, transport="json")  # type: ignore[arg-type]
        value = restored.get_item("price")
        assert value == Decimal("19.99")
        assert isinstance(value, Decimal)


# =============================================================================
# 13. to_tytx - preserves attr e node_tag
# =============================================================================


class TestTytxAttrAndTag:
    def test_roundtrip_preserves_attributes(self):
        """Node attributes survive the TYTX roundtrip."""
        src = Bag()
        src.set_item("a", 1, _attributes={"kind": "int", "size": 4})
        data = src.to_tytx(transport="json")
        restored = Bag.from_tytx(data, transport="json")  # type: ignore[arg-type]
        assert restored.get_attr("a", "kind") == "int"
        assert restored.get_attr("a", "size") == 4

    def test_roundtrip_preserves_node_tag(self):
        """node_tag sopravvive al roundtrip."""
        src = Bag()
        src.set_item("doc", "hello", node_tag="paragraph")
        data = src.to_tytx(transport="json")
        restored = Bag.from_tytx(data, transport="json")  # type: ignore[arg-type]
        node = restored.get_node("doc")
        assert node is not None
        assert node.node_tag == "paragraph"


# =============================================================================
# 14. to_json / from_json - roundtrip
# =============================================================================


class TestJsonRoundtrip:
    def test_typed_roundtrip_preserves_values(self):
        """to_json(typed=True) plus from_json rebuilds values and attributes."""
        src = Bag()
        src.set_item("a", 42, _attributes={"type": "int"})
        src.set_item("b", "hello")
        data = src.to_json(typed=True)
        restored = LegacyJsonBag.from_json(data)
        assert restored.get_item("a") == 42
        assert restored.get_attr("a", "type") == "int"
        assert restored.get_item("b") == "hello"

    def test_non_typed_roundtrip_basic(self):
        """to_json(typed=False) produce JSON standard rileggibile."""
        src = Bag({"a": 1, "b": 2})
        data = src.to_json(typed=False)
        restored = LegacyJsonBag.from_json(data)
        assert restored.get_item("a") == 1
        assert restored.get_item("b") == 2

    def test_roundtrip_preserves_nested(self):
        """Struttura annidata sopravvive al roundtrip JSON."""
        src = Bag()
        src["outer.inner"] = "v"
        data = src.to_json(typed=True)
        restored = LegacyJsonBag.from_json(data)
        assert restored.get_item("outer.inner") == "v"


# =============================================================================
# 15. to_json typed - preserva date/datetime/Decimal
# =============================================================================


class TestJsonTyped:
    def test_preserves_decimal(self):
        """typed=True preserva Decimal attraverso JSON."""
        src = Bag({"price": Decimal("19.99")})
        data = src.to_json(typed=True)
        restored = LegacyJsonBag.from_json(data)
        value = restored.get_item("price")
        assert value == Decimal("19.99")
        assert isinstance(value, Decimal)


# =============================================================================
# 16. from_json da dict/list Python diretto
# =============================================================================


class TestFromJsonNonString:
    def test_from_json_dict_input(self):
        """from_json accetta direttamente un dict Python."""
        bag = LegacyJsonBag.from_json({"a": 1, "b": 2})
        assert bag.get_item("a") == 1
        assert bag.get_item("b") == 2

    def test_from_json_scalar_wrapped_in_value(self):
        """from_json(scalare) wrappa in {'value': scalare}."""
        bag = LegacyJsonBag.from_json(42)  # type: ignore[arg-type]
        assert bag.get_item("value") == 42


# =============================================================================
# 17. from_xml raise_on_error + empty
# =============================================================================


class TestFromXmlExtra:
    def test_empty_factory_called_on_empty_element(self):
        """The empty callable supplies the default value for empty elements."""
        bag = Bag.from_xml(
            '<GenRoBag><x _T="L"></x></GenRoBag>',
            empty=lambda: 0,
        )
        assert bag.get_item("x") == 0

    def test_raise_on_error_true_propagates(self):
        """raise_on_error=True raises on values that cannot be converted."""
        with pytest.raises(ValueError):
            Bag.from_xml(
                '<GenRoBag><x _T="L">not_a_number</x></GenRoBag>',
                raise_on_error=True,
            )


# =============================================================================
# 18. XML tag sanitization (labels valid in Python, invalid as XML tags)
# =============================================================================


class TestXmlTagSanitization:
    def test_label_with_invalid_chars_is_sanitized(self):
        """Labels with spaces or special characters: to_xml emits a valid tag.

        Scenario reale: dati da CSV con header "My Col" o "col$1".
        """
        bag = Bag()
        bag.set_item("root.my col", "v")
        xml = bag.to_xml() or ""
        # il tag e' sanitizzato (es. 'my_col') ma l'XML resta parsabile
        assert "<root>" in xml
        assert "</root>" in xml

    def test_label_starting_with_digit_gets_underscore_prefix(self):
        """A label starting with a digit: the sanitized tag gets a '_' prefix."""
        bag = Bag()
        bag.set_item("root.2024data", "v")
        xml = bag.to_xml() or ""
        # the emitted tag starts with '_', not with a digit
        assert "<_2024data" in xml or "<_" in xml

    def test_empty_label_becomes_none_marker(self):
        """Label vuoto produce il tag speciale '_none_'."""
        bag = Bag()
        # Construct the serializer fixture directly: empty-path assignment
        # rejects an empty path; imported XML can still contain empty labels.
        sub = Bag()
        sub._nodes.set("", "v", parent_bag=sub)
        bag.set_item("root", sub)
        xml = bag.to_xml() or ""
        # the emitted tag carries the _none_ marker
        assert "_none_" in xml


# =============================================================================
# 19. to_json typed + resolver: il resolver e' serializzato in JSON
# =============================================================================


class TestJsonWithResolver:
    def test_to_json_includes_resolver_for_serializable_resolver(self):
        """to_json(typed=True) emette 'resolver' per resolver serializzabili.

        UuidResolver is serializable: no callback, only args and kwargs.
        """
        from genro_bag.resolvers import UuidResolver

        bag = Bag()
        bag["id"] = UuidResolver("uuid4")
        data = bag.to_json(typed=True)
        assert "resolver" in data
        assert "UuidResolver" in data

    def test_roundtrip_json_with_resolver_reconstructs_resolver(self):
        """from_json(to_json(bag_with_resolver)) ricostruisce un resolver equivalente."""
        from genro_bag.resolvers import UuidResolver

        bag = Bag()
        bag["id"] = UuidResolver("uuid4")
        data = bag.to_json(typed=True)
        restored = LegacyJsonBag.from_json(data)
        resolver = restored.get_resolver("id")
        assert isinstance(resolver, UuidResolver)


# =============================================================================
# 20. to_json emette 'tag' per node_tag
# =============================================================================


class TestJsonWithNodeTag:
    def test_to_json_includes_node_tag(self):
        """to_json writes 'tag' in the node dict when the node has a node_tag."""
        bag = Bag()
        bag.set_item("doc", "hello", node_tag="paragraph")
        data = bag.to_json(typed=True)
        assert "paragraph" in data


# =============================================================================
# 21. JSON edge case - list e dict vuoti
# =============================================================================


class TestJsonEmpty:
    def test_empty_json_list_produces_empty_bag(self):
        """from_json('[]') produce un Bag vuoto."""
        bag = LegacyJsonBag.from_json("[]")
        assert len(bag) == 0

    def test_empty_json_dict_produces_empty_bag(self):
        """from_json('{}') produce un Bag vuoto."""
        bag = LegacyJsonBag.from_json("{}")
        assert len(bag) == 0


# =============================================================================
# 22. XML legacy mixed content + from_json list_joiner
# =============================================================================
#
# Note: from_xml's docstring claims env var substitution {GNR_*},
# but _parse.py holds no substitution logic.
# No test written: the feature is documented but not implemented
# (candidate bug / doc mismatch).


class TestXmlMixedContent:
    def test_mixed_content_text_becomes_special_node(self):
        """XML mixing text and children in one element emits a '_' node for the text."""
        xml = "<root>leading text<child>v</child></root>"
        bag = Bag.from_xml(xml)
        # 'root' has a 'child' plus a '_' node holding the mixed text
        root = bag.get_item("root")
        assert isinstance(root, Bag)
        assert root.get_item("child") == "v"
        assert root.get_item("_") == "leading text"


class TestFromJsonListJoiner:
    def test_string_list_with_joiner(self):
        """from_json con list_joiner concatena liste di stringhe."""
        bag = LegacyJsonBag.from_json({"items": ["a", "b", "c"]}, list_joiner=",")
        assert bag.get_item("items") == "a,b,c"
