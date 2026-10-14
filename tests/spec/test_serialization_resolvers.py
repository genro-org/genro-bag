"""Spec test: BagResolvers survive serialization, both as node value
and as attribute, in all formats.

Depends on test_serialization.py (to_xml/to_json/to_tytx) and
test_resolvers.py (EnvResolver, UuidResolver).

Contract:
- A resolver travels as marked string ``::RSLV:<payload>``. As
  node value: ``_resolver`` attribute in XML, ``"resolver"`` key
  in JSON, value slot in TYTX. As attribute: in place of value.
- The roundtrip returns an equivalent but **distinct** resolver:
  same class, same parameters, different instance.
- ``sign_key`` signs payloads; reading back with a key expects valid
  signature. Used when Bag exits process and returns: resolver
  arguments tell what it will act on.
- Rereading is **inert**: resolver is reconstructed, never called.
  Effect happens on first value read.
- A resolver not in JSON (callback) raises
  ``BagSerializationError`` naming node and attribute.
- ``deepcopy`` reconstructs resolvers instead of sharing them.

## Scale

### Round-trip, 3 formats x 2 positions
1. XML   value / attribute
2. JSON  value / attribute
3. TYTX  value / attribute (also compact)
4. Nested Bag                          recursive resolver
5. value + attribute on same node

### Signing
6. signed roundtrip in 3 formats       resolver reconstructed
7. different key on return             SignatureError
8. unsigned payload, key expected      SignatureError
9. signature expired                   SignatureExpired
10. payload with separator             roundtrip intact

### Inertness
11. serializing doesn't call load()
12. rereading doesn't call load()
13. first read calls load()

### Errors
14. callback resolver                  BagSerializationError with node and attr

### deepcopy
15. value-side resolver preserved
16. distinct instances, not shared
"""

from __future__ import annotations

import json

import pytest
from contrib.genropy_wrapper.replacement._legacy_json import LegacyJsonParser
from genro_toolbox import SignatureError, SignatureExpired

from genro_bag import Bag, BagSerializationError
from genro_bag.resolver import BagSyncResolver
from genro_bag.resolvers import BagCbResolver, EnvResolver, UuidResolver


class LegacyJsonBag(LegacyJsonParser, Bag):
    """Exercise the legacy node decoder explicitly, never the public alias."""



KEY = "server-side-secret"


class CountingResolver(BagSyncResolver):
    """Resolver that counts its own executions, to prove inertness.

    Counter lives at class level because roundtrip reconstructs
    the instance: counting on instance would tell nothing.
    """

    class_kwargs = {"cache_time": 0, "read_only": False, "tag": None}
    calls = 0

    def load(self):
        type(self).calls += 1
        return f"loaded-{self.tag}"


# =============================================================================
# 1-3. Round-trip nei tre formati
# =============================================================================


class TestRoundTripValueSide:
    """Node resolver survives the roundtrip."""

    def test_xml(self):
        b = Bag()
        b["root"] = Bag()
        b["root.id"] = UuidResolver("uuid4")
        back = Bag.from_xml(b.to_xml())
        assert isinstance(back.get_resolver("root.id"), UuidResolver)

    def test_json(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        back = LegacyJsonBag.from_json(b.to_json())
        assert isinstance(back.get_resolver("id"), UuidResolver)

    def test_tytx(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        back = Bag.from_tytx(b.to_tytx())
        assert isinstance(back.get_resolver("id"), UuidResolver)

    def test_tytx_compact(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        back = Bag.from_tytx(b.to_tytx(compact=True))
        assert isinstance(back.get_resolver("id"), UuidResolver)

    def test_resolved_value_still_works(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        back = LegacyJsonBag.from_json(b.to_json())
        assert isinstance(back["id"], str)
        assert len(back["id"]) == 36


class TestRoundTripAttributeSide:
    """Resolver held in an attribute survives the roundtrip."""

    def test_xml(self, monkeypatch):
        monkeypatch.setenv("X_SECRET", "secret")
        b = Bag()
        b["root"] = Bag()
        b.set_item("root.n", "v", who=EnvResolver("X_SECRET"))
        back = Bag.from_xml(b.to_xml())
        assert back["root.n?who"] == "secret"

    def test_json(self, monkeypatch):
        monkeypatch.setenv("X_SECRET", "secret")
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("X_SECRET"))
        back = LegacyJsonBag.from_json(b.to_json())
        assert back["n?who"] == "secret"

    def test_tytx(self, monkeypatch):
        monkeypatch.setenv("X_SECRET", "secret")
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("X_SECRET"))
        back = Bag.from_tytx(b.to_tytx())
        assert back["n?who"] == "secret"

    def test_plain_attributes_untouched(self):
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("HOME"), plain="x", num=3)
        back = LegacyJsonBag.from_json(b.to_json())
        assert back["n?plain"] == "x"
        assert back["n?num"] == 3

    def test_no_memory_address_in_xml(self):
        """Old str() wrote '<... object at 0x...>' in attribute."""
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("HOME"))
        assert "object at 0x" not in b.to_xml()


# =============================================================================
# 4-5. Casi composti
# =============================================================================


class TestNestedAndCombined:
    def test_resolver_in_nested_bag(self):
        b = Bag()
        b["a"] = Bag()
        b["a.b"] = Bag()
        b["a.b.id"] = UuidResolver("uuid4")
        back = LegacyJsonBag.from_json(b.to_json())
        assert isinstance(back.get_resolver("a.b.id"), UuidResolver)

    def test_value_and_attribute_on_same_node(self, monkeypatch):
        monkeypatch.setenv("X_SECRET", "secret")
        b = Bag()
        b.set_item("n", UuidResolver("uuid4"), who=EnvResolver("X_SECRET"))
        back = LegacyJsonBag.from_json(b.to_json())
        assert isinstance(back.get_resolver("n"), UuidResolver)
        assert back["n?who"] == "secret"

    def test_node_tag_survives(self):
        b = Bag()
        b.set_item("n", UuidResolver("uuid4"), node_tag="item")
        back = LegacyJsonBag.from_json(b.to_json())
        assert back.get_node("n").node_tag == "item"


# =============================================================================
# 6-10. Firma
# =============================================================================


class TestSignedRoundTrip:
    """With a key the payload is signed and must be verified on return."""

    def test_xml(self):
        b = Bag()
        b["root"] = Bag()
        b["root.id"] = UuidResolver("uuid4")
        back = Bag.from_xml(b.to_xml(sign_key=KEY), sign_key=KEY)
        assert isinstance(back.get_resolver("root.id"), UuidResolver)

    def test_json(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        back = LegacyJsonBag.from_json(b.to_json(sign_key=KEY), sign_key=KEY)
        assert isinstance(back.get_resolver("id"), UuidResolver)

    def test_tytx(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        back = Bag.from_tytx(b.to_tytx(sign_key=KEY), sign_key=KEY)
        assert isinstance(back.get_resolver("id"), UuidResolver)

    def test_attribute_side_signed(self, monkeypatch):
        monkeypatch.setenv("X_SECRET", "secret")
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("X_SECRET"))
        back = LegacyJsonBag.from_json(b.to_json(sign_key=KEY), sign_key=KEY)
        assert back["n?who"] == "secret"


class TestSignatureRejection:
    """What comes back from client is no longer what left."""

    def test_wrong_key(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        with pytest.raises(SignatureError):
            LegacyJsonBag.from_json(b.to_json(sign_key=KEY), sign_key="another-secret")

    def test_unsigned_payload_when_key_expected(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        with pytest.raises(SignatureError):
            LegacyJsonBag.from_json(b.to_json(), sign_key=KEY)

    def test_expired_signature(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        data = b.to_json(sign_key=KEY, expires_in=1)
        import time

        time.sleep(1.1)
        with pytest.raises(SignatureExpired):
            LegacyJsonBag.from_json(data, sign_key=KEY)

    def test_tampered_argument_is_refused(self):
        """Real case: client rewrites a parameter and sends it back.

        Signed payload is base64, so client can't edit it in plain text:
        can only replace it with self-made one. That's exactly what
        signature rejects.
        """
        legit = Bag()
        legit.set_item("n", "v", who=EnvResolver("HOME"))
        signed = json.loads(legit.to_json(typed=False, sign_key=KEY))

        attacker = Bag()
        attacker.set_item("n", "v", who=EnvResolver("PATH"))
        theirs = json.loads(attacker.to_json(typed=False, sign_key="attacker-key"))

        signed[0]["attr"]["who"] = theirs[0]["attr"]["who"]
        with pytest.raises(SignatureError):
            LegacyJsonBag.from_json(json.dumps(signed), sign_key=KEY)

    def test_unsigned_substitution_is_refused(self):
        """Sending unsigned payload back is not enough to pass."""
        legit = Bag()
        legit.set_item("n", "v", who=EnvResolver("HOME"))
        signed = json.loads(legit.to_json(typed=False, sign_key=KEY))

        attacker = Bag()
        attacker.set_item("n", "v", who=EnvResolver("PATH"))
        bare = json.loads(attacker.to_json(typed=False))

        signed[0]["attr"]["who"] = bare[0]["attr"]["who"]
        with pytest.raises(SignatureError):
            LegacyJsonBag.from_json(json.dumps(signed), sign_key=KEY)

    def test_attribute_side_rejection(self):
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("HOME"))
        with pytest.raises(SignatureError):
            LegacyJsonBag.from_json(b.to_json(sign_key=KEY), sign_key="another-secret")


@pytest.mark.parametrize("container", ["dict", "list", "mixed"])
@pytest.mark.parametrize("as_text", [False, True], ids=["native", "json-text"])
@pytest.mark.parametrize("position", ["value", "attribute"])
@pytest.mark.parametrize("signature", ["valid", "unsigned", "wrong-key", "tampered", "expired"])
def test_nested_json_resolver_signature(monkeypatch, container, as_text, position, signature):
    """Generic containers must retain signature requirements at every depth."""
    monkeypatch.setattr(CountingResolver, "calls", 0)
    monkeypatch.setattr("genro_toolbox.signing.time.time", lambda: 1000)
    resolver = CountingResolver(tag="trusted")
    bag = Bag()
    if position == "value":
        bag.set_item("n", resolver)
    else:
        bag.set_item("n", "v", who=resolver)

    key = None if signature == "unsigned" else KEY
    if signature == "wrong-key":
        key = "another-secret"
    source = json.loads(bag.to_json(typed=False, sign_key=key, expires_in=60))
    if signature == "tampered":
        holder = source[0] if position == "value" else source[0]["attr"]
        field = "resolver" if position == "value" else "who"
        payload, mac = holder[field].rsplit(".", 1)
        holder[field] = f"{payload}.{'A' if mac[0] != 'A' else 'B'}{mac[1:]}"
    if signature == "expired":
        monkeypatch.setattr("genro_toolbox.signing.time.time", lambda: 1061)

    if container == "dict":
        source, path = {"wrapped": source}, "wrapped.n"
    elif container == "list":
        source, path = [source], "r_0.n"
    else:
        source, path = {"outer": [{"inner": [source]}]}, "outer.outer_0.inner.inner_0.n"
    if as_text:
        source = json.dumps(source)

    if signature == "valid":
        restored = LegacyJsonBag.from_json(source, sign_key=KEY)
        if position == "value":
            restored_resolver = restored.get_resolver(path)
        else:
            restored_resolver = restored.get_node(path).attr["who"]
        assert isinstance(restored_resolver, CountingResolver)
        assert restored_resolver.serialize() == resolver.serialize()
    else:
        error = SignatureExpired if signature == "expired" else SignatureError
        with pytest.raises(error):
            LegacyJsonBag.from_json(source, sign_key=KEY)
    assert CountingResolver.calls == 0


class TestPayloadWithSeparator:
    """Payload containing signature separator stays intact."""

    def test_json_roundtrip(self, monkeypatch):
        monkeypatch.setenv("X.SECRET.DOTTED", "ok")
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("X.SECRET.DOTTED"))
        back = LegacyJsonBag.from_json(b.to_json(sign_key=KEY), sign_key=KEY)
        assert back["n?who"] == "ok"


# =============================================================================
# 11-13. Inerzia
# =============================================================================


class TestInertness:
    """Serializing and rereading execute nothing: only reading does."""

    def test_serializing_does_not_call_load(self):
        CountingResolver.calls = 0
        b = Bag()
        b["x"] = CountingResolver(tag="a")
        b.to_json()
        b.to_xml()
        b.to_tytx()
        assert CountingResolver.calls == 0

    def test_parsing_does_not_call_load(self):
        b = Bag()
        b["x"] = CountingResolver(tag="a")
        data = b.to_json()
        CountingResolver.calls = 0
        LegacyJsonBag.from_json(data)
        assert CountingResolver.calls == 0

    def test_first_read_calls_load(self):
        b = Bag()
        b["x"] = CountingResolver(tag="a")
        back = LegacyJsonBag.from_json(b.to_json())
        CountingResolver.calls = 0
        assert back["x"] == "loaded-a"
        assert CountingResolver.calls == 1


# =============================================================================
# 14. Resolver non serializzabili
# =============================================================================


class TestNonSerializableResolver:
    """Callback doesn't fit in JSON: explicit error, not silence."""

    def test_value_side_raises(self):
        b = Bag()
        b["cb"] = BagCbResolver(lambda: 42)
        with pytest.raises(BagSerializationError):
            b.to_json()

    def test_attribute_side_raises(self):
        b = Bag()
        b.set_item("n", "v", cb=BagCbResolver(lambda: 42))
        with pytest.raises(BagSerializationError):
            b.to_json()

    def test_message_names_node_and_attribute(self):
        b = Bag()
        b.set_item("mynode", "v", mykey=BagCbResolver(lambda: 42))
        with pytest.raises(BagSerializationError) as exc:
            b.to_json()
        assert "mynode" in str(exc.value)
        assert "mykey" in str(exc.value)

    def test_all_formats_agree(self):
        b = Bag()
        b["cb"] = BagCbResolver(lambda: 42)
        for dump in (b.to_json, b.to_xml, b.to_tytx):
            with pytest.raises(BagSerializationError):
                dump()


# =============================================================================
# 15-16. deepcopy
# =============================================================================


class TestDeepcopyResolvers:
    """Copy reconstructs resolvers: no shared instances."""

    def test_value_side_preserved(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        assert isinstance(b.deepcopy().get_resolver("id"), UuidResolver)

    def test_value_side_distinct_instance(self):
        b = Bag()
        b["id"] = UuidResolver("uuid4")
        assert b.deepcopy().get_resolver("id") is not b.get_resolver("id")

    def test_attribute_side_distinct_instance(self):
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("HOME"))
        copy = b.deepcopy()
        assert copy.get_node("n").attr["who"] is not b.get_node("n").attr["who"]

    def test_both_copies_still_resolve(self, monkeypatch):
        monkeypatch.setenv("X_SECRET", "secret")
        b = Bag()
        b.set_item("n", "v", who=EnvResolver("X_SECRET"))
        copy = b.deepcopy()
        assert b["n?who"] == copy["n?who"] == "secret"

    def test_nested_bag_resolver_copied(self):
        b = Bag()
        b["inner"] = Bag()
        b["inner.id"] = UuidResolver("uuid4")
        assert isinstance(b.deepcopy().get_resolver("inner.id"), UuidResolver)


# =============================================================================
# Security: the class named by the payload
# =============================================================================


class TestClassGuard:
    """Payload chooses class name, not its hierarchy."""

    def test_class_outside_hierarchy_is_refused(self):
        forged = json.dumps(
            [
                {
                    "label": "x",
                    "value": None,
                    "attr": {},
                    "resolver": "::RSLV:"
                    + json.dumps(
                        {
                            "resolver_module": "subprocess",
                            "resolver_class": "Popen",
                            "args": [["echo", "pwned"]],
                            "kwargs": {},
                        }
                    ),
                }
            ]
        )
        with pytest.raises(ValueError, match="not a BagResolver subclass"):
            LegacyJsonBag.from_json(forged)

    def test_module_level_function_is_refused(self):
        forged = json.dumps(
            [
                {
                    "label": "x",
                    "value": None,
                    "attr": {},
                    "resolver": "::RSLV:"
                    + json.dumps(
                        {
                            "resolver_module": "os",
                            "resolver_class": "system",
                            "args": ["echo pwned"],
                            "kwargs": {},
                        }
                    ),
                }
            ]
        )
        with pytest.raises(ValueError, match="not a BagResolver subclass"):
            LegacyJsonBag.from_json(forged)
