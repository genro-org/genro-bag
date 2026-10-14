"""Behavioral contracts for the operation-local legacy XML/JSON codec."""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from decimal import Decimal
from xml.sax import SAXParseException

import pytest

from genro_bag import Bag


class _Catalog:
    _names = {
        str: "T",
        int: "L",
        float: "R",
        bool: "B",
        Decimal: "N",
        datetime.date: "D",
        datetime.time: "H",
        datetime.datetime: "DH",
        type(None): "NN",
    }

    def asTextAndType(
        self, value, translate_cb=None, nestedTyping=False
    ):
        code = self._names.get(type(value), "T")
        if code == "NN":
            return "", code
        if code == "B":
            text = str(value)
        elif code in {"D", "H", "DH"}:
            text = value.isoformat()
        else:
            text = str(value)
        if translate_cb and isinstance(value, str):
            text = translate_cb(text)
        return text, code

    def asTypedText(
        self, value, translate_cb=None, nestedTyping=False
    ):
        text, code = self.asTextAndType(value, translate_cb, nestedTyping)
        return text if code == "T" else f"{text}::{code}"

    def asText(self, value, translate_cb=None):
        return self.asTextAndType(value, translate_cb)[0]

    def fromText(self, text, code):
        if code in {None, "", "T"}:
            return text
        if code in {"L", "I"}:
            return int(text or 0)
        if code == "R":
            return float(text or 0)
        if code == "B":
            return text.upper() in {"Y", "TRUE", "YES", "1"}
        if code == "N":
            return Decimal(text or 0)
        if code == "D":
            return datetime.date.fromisoformat(text) if text else None
        if code == "H":
            return datetime.time.fromisoformat(text) if text else None
        if code == "DH":
            return datetime.datetime.fromisoformat(text) if text else None
        if code == "NN":
            return None
        return text

    def fromTypedText(self, value):
        if not isinstance(value, str) or "::" not in value:
            return value
        text, code = value.rsplit("::", 1)
        return self.fromText(text, code)

    def isTypedText(self, value):
        return isinstance(value, str) and "::" in value

    def toTypedJSON(self, value):
        return json.dumps(self._typed(value))

    def toJson(self, value):
        return json.dumps(value)

    def _typed(self, value):
        if isinstance(value, dict):
            return {key: self._typed(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._typed(item) for item in value]
        if type(value) in {Decimal, datetime.date, datetime.time, datetime.datetime}:
            return self.asTypedText(value)
        return value


CATALOG = _Catalog()


def test_legacy_xml_round_trip_preserves_types_attributes_and_empty_values():
    bag = Bag()
    bag.set_item("none", None)
    bag.set_item("empty", "")
    bag.set_item("false", False, _attributes={"count": 4, "active": False})
    bag.set_item("integer", 42)
    bag.set_item("real", 3.5)
    bag.set_item("decimal", Decimal("9.50"))
    bag.set_item("date", datetime.date(2026, 9, 13))
    bag.set_item("time", datetime.time(8, 30, 5))
    bag.set_item("datetime", datetime.datetime(2026, 9, 13, 8, 30, 5))
    bag.set_item("nested.child", "text")
    bag.set_item("empty_bag", Bag())

    xml = bag.to_xml(legacy_mode=True, catalog=CATALOG)
    restored = Bag.from_xml(xml, legacy_mode=True, catalog=CATALOG)

    assert xml.startswith("<?xml version='1.0' encoding='UTF-8'?>\n<GenRoBag>")
    assert restored["none"] is None
    assert restored["empty"] == ""
    assert restored["false"] is False
    assert restored.get_attr("false") == {"count": 4, "active": False}
    assert restored["integer"] == 42
    assert restored["real"] == 3.5
    assert restored["decimal"] == Decimal("9.50")
    assert restored["date"] == datetime.date(2026, 9, 13)
    assert restored["time"] == datetime.time(8, 30, 5)
    assert restored["datetime"] == datetime.datetime(2026, 9, 13, 8, 30, 5)
    assert restored["nested.child"] == "text"
    assert isinstance(restored["empty_bag"], Bag)
    assert not restored["empty_bag"]


def test_repeated_and_invalid_tags_keep_source_tag_independently():
    source = "<GenRoBag><mobile>1</mobile><mobile>2</mobile><_3d _tag='3d'>x</_3d></GenRoBag>"
    bag = Bag.from_xml(source, legacy_mode=True, catalog=CATALOG)

    assert bag.keys() == ["mobile", "mobile__dup_1", "3d"]
    assert bag["mobile"] == "1"
    assert bag["mobile__dup_1"] == "2"
    wire = bag.to_xml(
        legacy_mode=True, catalog=CATALOG, omit_root=True, doc_header=False
    )
    assert wire.count("<mobile>") == 2
    assert '<_3d _tag="3d">x</_3d>' in wire


def test_legacy_xml_file_options_and_array_input(tmp_path):
    target = tmp_path / "nested" / "sample.xml"
    bag = Bag({"value": 5})

    result = bag.toXml(
        filename=target,
        encoding="UTF-8",
        autocreate=True,
        docHeader="<!-- legacy -->\n",
        catalog=CATALOG,
    )
    assert result == target.read_text(encoding="UTF-8")
    assert result.startswith("<!-- legacy -->\n<GenRoBag>")

    restored = Bag()
    assert restored.fromXml(target, catalog=CATALOG) is None
    assert restored["value"] == 5

    array_xml = (
        "<GenRoBag><items _T='AL'><C _T='L'>1</C>"
        "<C _T='L'>2</C></items></GenRoBag>"
    )
    assert Bag.from_xml(
        array_xml, legacy_mode=True, catalog=CATALOG
    )["items"] == [1, 2]


def test_legacy_aliases_populate_receiver_atomically_and_keep_native_identity():
    parent = Bag()
    target = Bag({"old": 1})
    parent.set_item("target", target)
    events = []

    def record_event(**event):
        events.append(event["evt"])

    parent.subscribe("legacy-population", any=record_event)
    target_id = id(target)

    assert target.fromXml(
        "<GenRoBag><branch><leaf _T='L'>7</leaf></branch></GenRoBag>",
        catalog=CATALOG,
    ) is None
    assert id(target) == target_id
    assert type(target["branch"]) is Bag
    assert target["branch"].parent is target
    assert target["branch.leaf"] == 7
    assert events == ["upd_value"]

    with pytest.raises(SAXParseException):
        target.fromXml("<GenRoBag><broken></GenRoBag>", catalog=CATALOG)
    assert target["branch.leaf"] == 7
    assert events == ["upd_value"]


def test_legacy_json_has_historical_shape_and_populates_from_strings():
    source = Bag()
    source.set_item("amount", Decimal("2.50"), _attributes={"on": True})
    source.set_item("nested.flag", False)

    wire = source.to_json(legacy_mode=True, catalog=CATALOG)
    decoded = json.loads(wire)
    assert decoded[0] == {
        "label": "amount",
        "value": "2.50::N",
        "attr": {"on": True},
    }
    assert "resolver" not in decoded[0]

    target = Bag({"stale": True})
    loaded = Bag.from_json(wire, legacy_mode=True, catalog=CATALOG)
    assert isinstance(loaded, Bag)
    assert target.replace(loaded) is target
    assert target["amount"] == Decimal("2.50")
    assert target["nested.flag"] is False


def test_modern_serializers_keep_their_existing_defaults():
    bag = Bag({"count": 3})
    assert bag.to_xml() == "<count>3</count>"
    assert json.loads(bag.to_json()) == [
        {"label": "count", "value": 3, "attr": {}}
    ]
    assert Bag.from_xml("<root><count>3</count></root>")["root.count"] == "3"


def test_legacy_mode_rejects_modern_signature_options_instead_of_downgrading():
    bag = Bag({"count": 3})
    with pytest.raises(ValueError, match="does not support resolver signatures"):
        bag.to_xml(legacy_mode=True, catalog=CATALOG, sign_key="secret")
    with pytest.raises(ValueError, match="does not support resolver signatures"):
        bag.to_json(legacy_mode=True, catalog=CATALOG, expires_in=30)
    with pytest.raises(ValueError, match="does not carry resolver signatures"):
        Bag.from_xml(
            "<GenRoBag/>", legacy_mode=True, catalog=CATALOG, sign_key="secret"
        )
    with pytest.raises(ValueError, match="does not carry resolver signatures"):
        Bag.from_json("[]", legacy_mode=True, catalog=CATALOG, sign_key="secret")


def test_modern_import_and_operations_do_not_require_genropy():
    code = """
import importlib.abc
import sys
class BlockGnr(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'gnr' or fullname.startswith('gnr.'):
            raise ModuleNotFoundError(fullname)
        return None
sys.meta_path.insert(0, BlockGnr())
from genro_bag import Bag
bag = Bag({'answer': 42})
assert bag.to_xml() == '<answer>42</answer>'
assert Bag.from_tytx(bag.to_tytx())['answer'] == 42
try:
    bag.to_xml(legacy_mode=True)
except ImportError as exc:
    assert 'injected GenroPy catalog' in str(exc)
else:
    raise AssertionError('legacy codec unexpectedly found GenroPy')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr


def test_legacy_xml_does_not_treat_dynamic_proxy_as_bag():
    class StoreProxy:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

        def __str__(self):
            return "store proxy"

    bag = Bag({"store": StoreProxy(), "nested": Bag({"answer": 42})})
    xml = bag.toXml(catalog=_Catalog())
    assert "store proxy" in xml
    assert Bag.from_xml(xml, legacy_mode=True, catalog=_Catalog())["nested.answer"] == 42


def test_pretty_legacy_xml_is_written_directly_with_types_and_custom_indent(monkeypatch):
    from xml.dom import minidom

    def forbidden(*args, **kwargs):
        pytest.fail('Legacy serialization must not parse its output')

    monkeypatch.setattr(minidom, 'parseString', forbidden)
    bag = Bag()
    bag.set_item('branch.count', 12)
    bag.set_item('branch.text', '  a & <b>\n  c  ')
    xml = bag.to_xml(legacy_mode=True, catalog=CATALOG, pretty='  ', doc_header=False)
    assert xml == (
        '<GenRoBag>\n  <branch>\n    <count _T="L">12</count>\n'
        '    <text>  a &amp; &lt;b&gt;\n  c  </text>\n  </branch>\n</GenRoBag>'
    )
    restored = Bag.from_xml(xml, legacy_mode=True, catalog=CATALOG)
    assert restored['branch.count'] == 12
    assert restored['branch.text'] == '  a & <b>\n  c  '


def test_pretty_legacy_fragments_flatten_and_raw_html():
    bag = Bag()
    bag.set_item('__flatten__', Bag({'a': 'one', 'b': 'two'}))
    bag.set_item('raw', '<b>do not reformat</b>::HTML')
    bag.set_item('hidden', 'secret', {'__forbidden__': True})
    assert bag.to_xml(legacy_mode=True, catalog=CATALOG, pretty=True,
                      omit_root=True, doc_header=False) == (
        '<a>one</a>\n<b>two</b>\n<raw><b>do not reformat</b></raw>'
    )
    assert Bag().to_xml(legacy_mode=True, catalog=CATALOG, pretty=True,
                        doc_header=False) == '<GenRoBag></GenRoBag>'


def test_pretty_legacy_preserves_space_and_explicit_empty_tag_policy():
    bag = Bag()
    bag.set_item('group', Bag({'a': 'one', 'b': 'two'}), {'xml:space': 'preserve'})
    bag.set_item('empty', '')
    kwargs = {"legacy_mode": True, "catalog": CATALOG, "typeattrs": False, "doc_header": False}
    assert bag.to_xml(pretty=True, self_closed_tags=['empty'], **kwargs) == (
        '<GenRoBag>\n\t<group xml:space="preserve"><a>one</a><b>two</b></group>'
        '\n\t<empty/>\n</GenRoBag>'
    )
    assert bag.to_xml(**kwargs) == (
        '<GenRoBag><group xml:space="preserve"><a>one</a>\n<b>two</b></group>'
        '\n<empty></empty></GenRoBag>'
    )
