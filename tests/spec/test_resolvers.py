"""Spec test: Bag - resolver (lazy-evaluated values).

Depends on test_basic.py (set_item, get_item, get_attr, set_attr,
set_callback_item, set_resolver, get_resolver, get_node).

Resolvers are public objects: the user instantiates them and passes them to
the Bag as a value. The test exercises them PRIMARILY through the Bag
(bag['path'] triggers the load). Only for aspects concerning exclusively
the resolver's lifetime (reset, expired, serialize) does the test call the
resolver's public methods directly.

## Scale

1.  UuidResolver              unique id generator, cache_time=-1
2.  EnvResolver               env var + default
3.  BagCbResolver sync        sync callback with kwargs
4.  BagCbResolver with cache  cache_time > 0
5.  BagCbResolver async       coroutine callback (smartawait)
6.  FileResolver              filesystem, formats txt/json/csv
7.  node.attr vs resolver._kw parameter priority
8.  static=True               read without trigger
9.  reset / expired           manual invalidation
10. read_only                 does not save the value in the node
11. boolean cache_time        error in __init__
12. serialize roundtrip       resolver serialization
13. get_resolver / set_resolver  node accessors
14. UrlResolver               network (marker)

## Resolver in place - public API of resolver obtained via bag

Like BagNode, a resolver is not instantiated alone in a test and then
used in isolation: you place it in a Bag (bag['x'] = Resolver(...))
and then access it via bag.get_resolver(path). Once in place, all
public methods/properties of the resolver are testable API.

15. property cache_time / interval / reactive / read_only / is_async
16. cached_value getter/setter
17. __eq__ among resolvers     same class + same args
18. kw pre-processed (on_loading)
19. container proxy            resolver['x'], resolver.keys/items/values
"""

from __future__ import annotations

import pytest

from genro_bag import Bag, BagResolver
from genro_bag.resolvers import (
    BagAsyncCbResolver,
    BagCbResolver,
    DirectoryResolver,
    EnvResolver,
    FileResolver,
    UrlResolver,
    UuidResolver,
)

# =============================================================================
# 1. UuidResolver
# =============================================================================


class TestUuidResolver:
    def test_generates_string(self):
        """bag['id'] with UuidResolver produces a non-empty string."""
        bag = Bag()
        bag["id"] = UuidResolver()
        value = bag["id"]
        assert isinstance(value, str)
        assert len(value) > 0

    def test_cached_by_default(self):
        """With cache_time=-1 (default) two reads return the same UUID."""
        bag = Bag()
        bag["id"] = UuidResolver()
        first = bag["id"]
        second = bag["id"]
        assert first == second

    def test_version_uuid1(self):
        """UuidResolver('uuid1') generates a uuid1-type UUID."""
        bag = Bag()
        bag["id"] = UuidResolver("uuid1")
        value = bag["id"]
        assert isinstance(value, str)
        # UUID1 has version '1' in the third group (e.g. xxxxxxxx-xxxx-1xxx-...)
        assert value[14] == "1"

    def test_unsupported_version_raises_on_load(self):
        """An unknown version raises ValueError on first access."""
        bag = Bag()
        bag["id"] = UuidResolver("uuid99")
        with pytest.raises(ValueError):
            _ = bag["id"]


# =============================================================================
# 2. EnvResolver
# =============================================================================


class TestEnvResolver:
    def test_reads_env_variable(self, monkeypatch: pytest.MonkeyPatch):
        """EnvResolver reads an existing environment variable."""
        monkeypatch.setenv("GENRO_BAG_TEST_VAR", "hello")
        bag = Bag()
        bag["v"] = EnvResolver("GENRO_BAG_TEST_VAR")
        assert bag["v"] == "hello"

    def test_returns_default_if_unset(self, monkeypatch: pytest.MonkeyPatch):
        """If the variable does not exist, returns default."""
        monkeypatch.delenv("GENRO_BAG_MISSING_VAR", raising=False)
        bag = Bag()
        bag["v"] = EnvResolver("GENRO_BAG_MISSING_VAR", default="fallback")
        assert bag["v"] == "fallback"

    def test_reflects_runtime_changes_without_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """cache_time=0 (default): each access re-reads the env."""
        monkeypatch.setenv("GENRO_BAG_VAR_B", "first")
        bag = Bag()
        bag["v"] = EnvResolver("GENRO_BAG_VAR_B")
        assert bag["v"] == "first"
        monkeypatch.setenv("GENRO_BAG_VAR_B", "second")
        assert bag["v"] == "second"


# =============================================================================
# 3. BagCbResolver (sync)
# =============================================================================


class TestBagCbResolverSync:
    def test_calls_callback_sync(self):
        """bag['calc'] triggers the callback on first read."""
        bag = Bag()
        bag["calc"] = BagCbResolver(lambda: 42)
        assert bag["calc"] == 42

    def test_callback_kwargs_passed_through(self):
        """The resolver's kwargs are passed to the callback."""
        def add(a, b):
            return a + b

        bag = Bag()
        bag["sum"] = BagCbResolver(add, a=3, b=5)
        assert bag["sum"] == 8

    def test_set_callback_item_shortcut(self):
        """set_callback_item is a shortcut for BagCbResolver."""
        bag = Bag()
        bag.set_callback_item("now", lambda: "fixed")
        assert bag["now"] == "fixed"


# =============================================================================
# 4. BagCbResolver con cache
# =============================================================================


class TestBagCbResolverCache:
    def test_cache_time_zero_recomputes(self):
        """cache_time=0: each access calls the callback."""
        counter = {"n": 0}

        def cb():
            counter["n"] += 1
            return counter["n"]

        bag = Bag()
        bag["c"] = BagCbResolver(cb)  # cache_time=0 default
        assert bag["c"] == 1
        assert bag["c"] == 2
        assert bag["c"] == 3

    def test_cache_time_infinite(self):
        """cache_time=-1: the value remains stable after the first load."""
        counter = {"n": 0}

        def cb():
            counter["n"] += 1
            return counter["n"]

        bag = Bag()
        bag["c"] = BagCbResolver(cb, cache_time=-1)
        assert bag["c"] == 1
        assert bag["c"] == 1
        assert bag["c"] == 1


# =============================================================================
# 5. BagAsyncCbResolver
# =============================================================================


class TestBagAsyncCbResolverAsync:
    def test_async_api_rejected(self):
        async def callback():
            return 1
        with pytest.raises(TypeError, match="no longer supported"):
            BagAsyncCbResolver(callback)


class TestBagCbResolverRejectsAsync:
    def test_async_callback_rejected(self):
        """BagCbResolver rejects a coroutine callback with TypeError."""

        async def async_cb():
            return 1

        with pytest.raises(TypeError, match="requires a sync"):
            BagCbResolver(async_cb)


# =============================================================================
# 6. FileResolver
# =============================================================================


class TestFileResolver:
    def test_loads_text_file(self, tmp_path):
        """FileResolver reads a .txt as a string."""
        file = tmp_path / "doc.txt"
        file.write_text("hello world", encoding="utf-8")
        bag = Bag()
        bag["doc"] = FileResolver(str(file))
        assert bag["doc"] == "hello world"

    def test_loads_json_file(self, tmp_path):
        """FileResolver on .json returns a parsed dict/list."""
        file = tmp_path / "data.json"
        file.write_text('{"a": 1, "b": 2}', encoding="utf-8")
        bag = Bag()
        bag["data"] = FileResolver(str(file))
        result = bag["data"]
        # without as_bag=True remains dict (read_only=True forces no-conversion)
        assert result == {"a": 1, "b": 2}

    def test_as_bag_true_converts_to_bag(self, tmp_path):
        """FileResolver with as_bag=True converts JSON to a navigable Bag."""
        file = tmp_path / "data.json"
        file.write_text('{"a": 1, "b": 2}', encoding="utf-8")
        bag = Bag()
        bag["data"] = FileResolver(str(file), as_bag=True)
        data = bag["data"]
        assert isinstance(data, Bag)
        assert data.get_item("a") == 1

    def test_missing_file_raises(self, tmp_path):
        """A missing file raises FileNotFoundError on first access."""
        bag = Bag()
        bag["doc"] = FileResolver(str(tmp_path / "missing.txt"))
        with pytest.raises(FileNotFoundError):
            _ = bag["doc"]

    def test_loads_csv_file_as_bag_of_records(self, tmp_path):
        """FileResolver on .csv returns a Bag of records with columns as attrs.

        Real-world scenario: CSV table mounted in a section of an app.
        """
        file = tmp_path / "contacts.csv"
        file.write_text(
            "name,age\nalice,30\nbob,25\n",
            encoding="utf-8",
        )
        bag = Bag()
        bag["contacts"] = FileResolver(str(file))
        contacts = bag["contacts"]
        assert isinstance(contacts, Bag)
        # two records
        assert len(contacts) == 2

    def test_loads_bag_json_file(self, tmp_path):
        """FileResolver on .bag.json uses TYTX to load the Bag.

        Scenario: native Bag persistence (type-preserving).
        """
        # prepare the file using to_tytx of the original Bag
        src = Bag({"a": 1, "b": "hello"})
        src.to_tytx(filename=str(tmp_path / "out"), transport="json")
        # the created file is out.bag.json
        bag = Bag()
        bag["data"] = FileResolver(str(tmp_path / "out.bag.json"))
        data = bag["data"]
        assert isinstance(data, Bag)
        assert data.get_item("a") == 1
        assert data.get_item("b") == "hello"

    def test_base_path_resolves_relative_path(self, tmp_path):
        """FileResolver(path, base_path=...) resolves relative paths relative to base_path.

        Real-world scenario: collection of assets relative to a project directory.
        """
        file = tmp_path / "doc.txt"
        file.write_text("content", encoding="utf-8")
        bag = Bag()
        bag["doc"] = FileResolver("doc.txt", base_path=str(tmp_path))
        assert bag["doc"] == "content"

    def test_unknown_extension_falls_back_to_text(self, tmp_path):
        """Files with unrecognized extensions are read as text."""
        file = tmp_path / "note.xyz"
        file.write_text("raw content", encoding="utf-8")
        bag = Bag()
        bag["doc"] = FileResolver(str(file))
        assert bag["doc"] == "raw content"

    def test_loads_bag_msgpack_file(self, tmp_path):
        """FileResolver on .bag.mp loads tytx in binary msgpack format."""
        src = Bag({"a": 1, "b": "hello"})
        src.to_tytx(filename=str(tmp_path / "out"), transport="msgpack")
        bag = Bag()
        bag["data"] = FileResolver(str(tmp_path / "out.bag.mp"))
        data = bag["data"]
        assert isinstance(data, Bag)
        assert data.get_item("a") == 1
        assert data.get_item("b") == "hello"

    def test_csv_no_header_mode_uses_positional_columns(self, tmp_path):
        """FileResolver CSV with csv_has_header=False uses c0,c1,... as attrs."""
        file = tmp_path / "data.csv"
        file.write_text("alice,30\nbob,25\n", encoding="utf-8")
        bag = Bag()
        bag["data"] = FileResolver(str(file), csv_has_header=False)
        data = bag["data"]
        assert isinstance(data, Bag)
        assert len(data) == 2
        first = data.get_node("r0")
        assert first is not None
        assert first.attr.get("c0") == "alice"
        assert first.attr.get("c1") == "30"

    def test_csv_empty_file_returns_empty_bag(self, tmp_path):
        """FileResolver CSV on empty file returns an empty Bag (no header = no rows)."""
        file = tmp_path / "empty.csv"
        file.write_text("", encoding="utf-8")
        bag = Bag()
        bag["data"] = FileResolver(str(file))
        data = bag["data"]
        assert isinstance(data, Bag)
        assert len(data) == 0


# =============================================================================
# 7. Priorita' node.attr vs resolver._kw
# =============================================================================


class TestParameterPriority:
    def test_resolver_kw_used_by_default(self):
        """If node.attr is not set, the resolver uses its defaults (_kw)."""
        bag = Bag()
        bag["x"] = BagCbResolver(lambda a: a * 2, a=5)
        assert bag["x"] == 10

    def test_node_attr_overrides_resolver_kw(self):
        """set_attr on the path overrides the resolver's default."""
        bag = Bag()
        bag["x"] = BagCbResolver(lambda a: a * 2, a=5, cache_time=0)
        assert bag["x"] == 10
        bag.set_attr("x", a=50)
        assert bag["x"] == 100

    def test_call_kwargs_are_a_one_off_read(self):
        """get_item(path, **kw) loads with kw for that call only: node.attr,
        the cached value and the following reads are unchanged."""
        calls = {"n": 0}

        def cb(units):
            calls["n"] += 1
            return units

        bag = Bag()
        bag["x"] = BagCbResolver(cb, units="fahrenheit", cache_time=-1)
        assert bag["x"] == "fahrenheit"
        assert bag.get_item("x", units="celsius") == "celsius"
        assert bag.get_attr("x", "units") is None
        assert bag.get_item("x", static=True) == "fahrenheit"
        assert bag["x"] == "fahrenheit"
        assert calls["n"] == 2

    def test_call_kwargs_one_off_read_is_not_cached(self):
        """Repeated one-off reads call load every time."""
        calls = {"n": 0}

        def cb(units):
            calls["n"] += 1
            return units

        bag = Bag()
        bag["x"] = BagCbResolver(cb, units="fahrenheit", cache_time=-1, read_only=True)
        assert bag.get_item("x", units="celsius") == "celsius"
        assert bag.get_item("x", units="celsius") == "celsius"
        assert bag["x"] == "fahrenheit"
        assert calls["n"] == 3

    def test_call_kwargs_accept_undeclared_keys(self):
        """An undeclared call kwarg reaches load, attached or standalone."""
        received = []

        def cb(**kw):
            received.append(kw)
            return len(received)

        bag = Bag()
        bag["x"] = BagCbResolver(cb, a=1, cache_time=-1)
        bag.get_item("x", page=2)
        standalone = BagCbResolver(cb, a=1)
        standalone(page=3)
        assert received == [{"a": 1, "page": 2}, {"a": 1, "page": 3}]
        assert bag.get_attr("x", "page") is None
        assert standalone() == 3
        assert received[-1] == {"a": 1}

    def test_call_kwargs_emit_no_event(self):
        """A one-off read is not a mutation: subscribers see nothing."""
        events = []
        bag = Bag()
        bag["x"] = BagCbResolver(lambda a: a, a=1, cache_time=-1)
        bag["x"]
        bag.subscribe("watch", any=lambda **kw: events.append(kw))
        bag.get_item("x", a=7)
        assert events == []

    def test_set_attr_on_resolver_param_invalidates_cache(self):
        """On a resolver with cache_time=-1 and NON-reactive, changing an attr
        that is a resolver parameter invalidates the cache: the next access
        recomputes. Difference with 'reactive=True' where the refresh is eager.
        """
        calls = {"n": 0}

        def cb(multiplier=1):
            calls["n"] += 1
            return calls["n"] * multiplier

        bag = Bag()
        bag["x"] = BagCbResolver(cb, cache_time=-1, multiplier=5)
        # first access: computes, cache hot
        assert bag["x"] == 5
        # second access: cache hit, does not recompute
        assert bag["x"] == 5
        assert calls["n"] == 1
        # modify the attr that is a parameter → cache invalidated
        node = bag.get_node("x")
        assert node is not None
        node.set_attr(multiplier=10)
        # next access recomputes with the new parameter
        assert bag["x"] == 20
        assert calls["n"] == 2

    @pytest.mark.parametrize("cache_time", [-1, 60])
    def test_undeclared_attr_is_not_a_resolver_input(self, cache_time):
        """An attribute the resolver does not declare (UI metadata such as
        '__checked') never reaches load and never invalidates the cache."""
        received = []

        def cb(**kw):
            received.append(kw)
            return len(received)

        bag = Bag()
        bag["x"] = BagCbResolver(cb, cache_time=cache_time, multiplier=5)
        assert bag["x"] == 1
        bag.set_attr("x", __checked=True, checked=True)
        assert bag["x"] == 1
        assert received == [{"multiplier": 5}]


# =============================================================================
# 8. static=True (no trigger del resolver)
# =============================================================================


class TestStaticAccess:
    def test_static_true_returns_cached_without_loading(self):
        """get_item(path, static=True) does not trigger the resolver."""
        calls = {"n": 0}

        def cb():
            calls["n"] += 1
            return "value"

        bag = Bag()
        bag["v"] = BagCbResolver(cb, cache_time=-1)
        # first lazy read triggers load
        bag["v"]
        assert calls["n"] == 1
        # static=True re-reads without calling cb
        cached = bag.get_item("v", static=True)
        assert cached == "value"
        assert calls["n"] == 1

    def test_static_before_any_load_returns_none(self):
        """static=True before any load returns the cached value (None)."""
        bag = Bag()
        bag["v"] = BagCbResolver(lambda: "hello", cache_time=-1)
        assert bag.get_item("v", static=True) is None


# =============================================================================
# 9. reset / expired
# =============================================================================


class TestResetAndExpired:
    def test_reset_forces_reload_next_access(self):
        """reset() (refresh=False) invalidates the cache: the next access reloads."""
        counter = {"n": 0}

        def cb():
            counter["n"] += 1
            return counter["n"]

        bag = Bag()
        bag["c"] = BagCbResolver(cb, cache_time=-1)
        assert bag["c"] == 1
        assert bag["c"] == 1  # cached
        resolver = bag.get_resolver("c")
        resolver.reset()
        assert bag["c"] == 2  # ricaricato

    def test_expired_false_when_cache_infinite_and_loaded(self):
        """With cache_time=-1 and already loaded, expired is False."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1, cache_time=-1)
        bag["c"]  # trigger
        assert bag.get_resolver("c").expired is False

    def test_expired_true_when_cache_time_zero(self):
        """With cache_time=0 expired is always True (no cache)."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1, cache_time=0)
        assert bag.get_resolver("c").expired is True


# =============================================================================
# 10. read_only
# =============================================================================


class TestReadOnly:
    def test_read_only_does_not_store_in_node(self):
        """read_only=True: the value is NOT saved as static_value."""
        counter = {"n": 0}

        def cb():
            counter["n"] += 1
            return counter["n"]

        bag = Bag()
        bag["c"] = BagCbResolver(cb, read_only=True)
        assert bag["c"] == 1
        # static=True reads the value in the node, which was not written
        assert bag.get_item("c", static=True) is None
        # since read_only does not store, each non-static read calls cb
        assert bag["c"] == 2


# =============================================================================
# 11. Errori di costruzione
# =============================================================================


class TestConstructionErrors:
    def test_boolean_cache_time_rejected(self):
        for value in (False, True):
            with pytest.raises(TypeError, match="cache_time must be numeric"):
                BagCbResolver(lambda: 1, cache_time=value)

    def test_read_only_with_interval_rejected(self):
        """read_only=True + interval raises ValueError."""
        with pytest.raises(TypeError):
            BagCbResolver(lambda: 1, read_only=True, interval=5)

    def test_read_only_with_reactive_rejected(self):
        """read_only=True + reactive=True raises ValueError."""
        with pytest.raises(TypeError):
            BagCbResolver(lambda: 1, read_only=True, reactive=True)


# =============================================================================
# 12. serialize / deserialize
# =============================================================================


class TestSerialize:
    def test_serialize_roundtrip_preserves_class_and_args(self):
        """BagResolver.deserialize(resolver.serialize()) reconstructs the resolver."""
        original = UuidResolver("uuid4")
        data = original.serialize()
        assert isinstance(data, dict)
        rebuilt = BagResolver.deserialize(data)
        assert isinstance(rebuilt, UuidResolver)
        assert rebuilt == original


# =============================================================================
# 13. get_resolver / set_resolver
# =============================================================================


class TestResolverAccessors:
    def test_get_resolver_returns_resolver(self):
        """get_resolver(path) returns the resolver instance of the node."""
        bag = Bag()
        r = UuidResolver()
        bag["id"] = r
        assert bag.get_resolver("id") is r

    def test_get_resolver_none_on_plain_node(self):
        """get_resolver returns None for a node without a resolver."""
        bag = Bag()
        bag["x"] = 42
        assert bag.get_resolver("x") is None

    def test_get_resolver_none_on_missing_path(self):
        """get_resolver returns None if the path does not exist."""
        assert Bag().get_resolver("missing") is None

    def test_set_resolver_creates_node_with_resolver(self):
        """set_resolver(path, resolver) creates a node with that resolver."""
        bag = Bag()
        r = UuidResolver()
        bag.set_resolver("id", r)
        assert bag.get_resolver("id") is r


# =============================================================================
# 14. UrlResolver (network - smoke)
# =============================================================================


class TestUrlResolver:
    def test_fetches_url_content(self, http_server):
        """UrlResolver performs the request and returns the content."""
        bag = Bag()
        bag["remote"] = UrlResolver(http_server.make_url("/json"))
        assert b"slideshow" in bag["remote"]

    def test_fetches_url_as_bag(self, http_server):
        """as_bag=True turns the JSON response into a navigable Bag."""
        bag = Bag()
        bag["remote"] = UrlResolver(http_server.make_url("/json"), as_bag=True)
        assert bag["remote.slideshow.title"] == "Sample Slide Show"


# =============================================================================
# 15. Resolver in place - properties (cache_time, interval, reactive,
#     read_only, is_async)
# =============================================================================


class TestResolverInPlaceProperties:
    def test_cache_time_property(self):
        """resolver.cache_time exposes the cache_time value."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1, cache_time=60)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.cache_time == 60

    def test_cache_time_negative_means_infinite(self):
        """cache_time=-1 means infinite cache."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1, cache_time=-1)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.cache_time == -1




    def test_reactive_setter_mutates(self):
        """reactive setter allows modifying the flag at runtime."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1, cache_time=-1)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        resolver.reactive = True
        assert resolver.reactive is True

    def test_read_only_derived_true_when_no_cache_no_trigger(self):
        """read_only not explicit: with cache_time=0 and no interval/reactive it is True.

        Documented: if not passed explicitly, it is derived from caching and refresh
        settings. Without cache and without trigger the resolver is read
        on each access -> read_only=True (no write to the node).
        """
        bag = Bag()
        bag["c"] = BagResolver()  # No constructor or class-level storage policy
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.read_only is True

    def test_read_only_derived_false_with_cache(self):
        """With cache_time=-1 (infinite), read_only not explicit is False."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1, cache_time=-1)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.read_only is False

    def test_read_only_explicit_false_honored(self):
        """read_only=False explicit wins over derived."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1, read_only=False)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.read_only is False

    def test_read_only_true_when_set(self):
        """read_only=True at construct is exposed by the property."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1, read_only=True)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.read_only is True

    def test_is_async_false_for_sync_callback(self):
        """is_async is False if the callback is sync."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: 1)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.is_async is False



# =============================================================================
# 16. cached_value getter/setter
# =============================================================================


class TestResolverCachedValue:
    def test_cached_value_before_load_is_none(self):
        """cached_value before any read is None."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: "hello", cache_time=-1)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.cached_value is None

    def test_cached_value_after_read(self):
        """After a read, cached_value reflects the value."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda: "hello", cache_time=-1)
        _ = bag["c"]
        resolver = bag.get_resolver("c")
        assert resolver is not None
        assert resolver.cached_value == "hello"


# =============================================================================
# 17. __eq__ tra resolver
# =============================================================================


class TestResolverEquality:
    def test_same_class_same_args_equal(self):
        """Two UuidResolvers with the same args are equal."""
        assert UuidResolver("uuid4") == UuidResolver("uuid4")

    def test_same_class_different_args_not_equal(self):
        """UuidResolver('uuid4') is not equal to UuidResolver('uuid1')."""
        assert UuidResolver("uuid4") != UuidResolver("uuid1")

    def test_different_classes_not_equal(self):
        """Resolvers of different classes are not equal."""
        assert UuidResolver() != EnvResolver("VAR")

    def test_resolver_not_equal_to_non_resolver(self):
        """__eq__ with a non-resolver object returns False."""
        r = UuidResolver()
        assert (r == "not a resolver") is False
        assert (r == 42) is False


# =============================================================================
# 18. kw pre-processed (on_loading)
# =============================================================================


class TestResolverExtras:
    def test_kwargs_exposes_only_extra_parameters(self):
        """Public extras exclude callback and internal configuration."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda a, b: a + b, a=1, b=2)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        kw = dict(resolver.kwargs)
        assert set(kw) == {"a", "b"}
        assert kw["a"] == 1
        assert kw["b"] == 2

    def test_on_loading_default_is_identity(self):
        """on_loading default is identity: kw == input."""
        bag = Bag()
        bag["c"] = BagCbResolver(lambda x: x, x=42)
        resolver = bag.get_resolver("c")
        assert resolver is not None
        # on_loading(dict) returns the dict unchanged
        kw_copy = dict(resolver.kwargs)
        assert resolver.on_loading(kw_copy) == kw_copy


# =============================================================================
# 19. Container proxy - resolver['x'], resolver.keys/items/values, get_node
# =============================================================================


class TestResolverContainerProxy:
    def test_resolver_iteration_delegates_to_resolved_bag(self):
        """Iteration exposes the resolved Bag nodes instead of indexing by integer."""

        resolver = BagCbResolver(
            lambda: Bag({"a": 1, "b": 2}), cache_time=-1
        )

        assert [node.label for node in resolver] == ["a", "b"]

    def test_resolver_getitem_after_load(self):
        """After load that produces a Bag, resolver['key'] navigates the resulting Bag."""

        def build():
            return {"a": 1, "b": 2}

        bag = Bag()
        # as_bag=True forces the conversion of the dict to a Bag
        bag["data"] = BagCbResolver(build, cache_time=-1, as_bag=True)
        _ = bag["data"]  # trigger load
        resolver = bag.get_resolver("data")
        assert resolver is not None
        assert resolver["a"] == 1
        assert resolver["b"] == 2

    def test_resolver_keys_values_items(self):
        """The resolver proxy exposes keys(), values(), items() of the cached Bag."""

        def build():
            return {"a": 1, "b": 2}

        bag = Bag()
        bag["data"] = BagCbResolver(build, cache_time=-1, as_bag=True)
        _ = bag["data"]
        resolver = bag.get_resolver("data")
        assert resolver is not None
        assert resolver.keys() == ["a", "b"]
        assert resolver.values() == [1, 2]
        assert resolver.items() == [("a", 1), ("b", 2)]

    def test_resolver_get_node(self):
        """resolver.get_node('key') returns the node of the cached Bag."""

        def build():
            return {"a": 42}

        bag = Bag()
        bag["data"] = BagCbResolver(build, cache_time=-1, as_bag=True)
        _ = bag["data"]
        resolver = bag.get_resolver("data")
        assert resolver is not None
        node = resolver.get_node("a")
        # the node is a valid BagNode, with label='a'
        assert node is not None
        assert node.label == "a"
        assert node.value == 42


# =============================================================================
# 20. DirectoryResolver - lazily mounting a directory as a Bag
# =============================================================================


class TestDirectoryResolverBasics:
    def test_resolved_update_accepts_directory_resolver(self, tmp_path):
        """Bag.update can consume a resolver directly and resolve its file nodes."""
        (tmp_path / "one.txt").write_text("one", encoding="utf-8")
        resolver = DirectoryResolver(str(tmp_path), ext="txt")
        target = Bag()

        target.update(resolver, resolved=True)

        assert target["one_txt"] == b"one"

    def test_empty_directory_produces_empty_bag(self, tmp_path):
        """An empty directory -> empty Bag."""
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        result = bag["docs"]
        assert isinstance(result, Bag)
        assert len(result) == 0

    def test_nonexistent_directory_produces_empty_bag(self, tmp_path):
        """Non-existent path -> empty Bag (OSError handled internally)."""
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path / "nonexistent"))
        result = bag["docs"]
        assert isinstance(result, Bag)
        assert len(result) == 0

    def test_directory_with_xml_file(self, tmp_path):
        """An .xml file in directory produces a node with label label_xml."""
        (tmp_path / "config.xml").write_text(
            "<root><x>1</x></root>", encoding="utf-8"
        )
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        result = bag["docs"]
        # default label: name + '_' + ext
        assert "config_xml" in result.keys()

    def test_directory_with_multiple_extensions(self, tmp_path):
        """ext='xml,txt' processes both extensions."""
        (tmp_path / "config.xml").write_text("<a>1</a>", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), ext="xml,txt")
        result = bag["docs"]
        assert "config_xml" in result.keys()
        assert "notes_txt" in result.keys()

    def test_subdirectory_becomes_nested_directory_resolver(self, tmp_path):
        """A subdirectory produces a node with a DirectoryResolver."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "inner.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        result = bag["docs"]
        # 'sub' is present as a node
        assert "sub" in result.keys()
        # accessing it triggers the resolver and returns the sub Bag
        sub_bag = result["sub"]
        assert isinstance(sub_bag, Bag)
        assert "inner_xml" in sub_bag.keys()


class TestDirectoryResolverAttributes:
    def test_node_has_standard_attributes(self, tmp_path):
        """Each node has file_name, file_ext, rel_path, abs_path, mtime, size."""
        f = tmp_path / "doc.xml"
        f.write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        result = bag["docs"]
        attrs = result.get_attr("doc_xml")
        assert attrs["file_name"] == "doc"
        assert attrs["file_ext"] == "xml"
        assert attrs["abs_path"] == str(f)
        assert attrs["size"] > 0

    def test_relocate_builds_rel_path(self, tmp_path):
        """relocate='virtual' prefixes the node's rel_path."""
        (tmp_path / "doc.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), "virtual")
        result = bag["docs"]
        assert result.get_attr("doc_xml", "rel_path") == "virtual/doc.xml"

    def test_relocate_propagates_to_subdirectories(self, tmp_path):
        """The relocate prefix is propagated to subdirectories."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "inner.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), "virtual")
        result = bag["docs"]
        sub_bag = result["sub"]
        # the node 'inner_xml' inside sub has rel_path 'virtual/sub/inner.xml'
        assert sub_bag.get_attr("inner_xml", "rel_path") == "virtual/sub/inner.xml"


class TestDirectoryResolverVisibility:
    def test_hidden_files_excluded_by_default(self, tmp_path):
        """Files with '.' at the start are excluded (invisible=False default)."""
        (tmp_path / ".secret").write_text("x", encoding="utf-8")
        (tmp_path / "visible.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        result = bag["docs"]
        keys = result.keys()
        assert "visible_xml" in keys
        assert ".secret" not in keys
        assert "secret_" not in " ".join(keys)

    def test_invisible_true_includes_hidden_files(self, tmp_path):
        """invisible=True also includes '.hidden' files."""
        (tmp_path / ".hidden").write_text("x", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), invisible=True, ext="")
        result = bag["docs"]
        # the file appears among the keys (the label has form '.hidden_')
        keys = result.keys()
        # something with 'hidden' in the name
        assert any("hidden" in k for k in keys)

    def test_reserved_names_skipped(self, tmp_path):
        """Files that start/end with '#' or end with '~' are skipped."""
        (tmp_path / "#journal").write_text("x", encoding="utf-8")
        (tmp_path / "trailing~").write_text("x", encoding="utf-8")
        (tmp_path / "normal.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        keys = bag["docs"].keys()
        assert keys == ["normal_xml"]


class TestDirectoryResolverFilters:
    def test_include_glob_pattern(self, tmp_path):
        """include='*.xml' filters only matching ones."""
        (tmp_path / "a.xml").write_text("<a/>", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        (tmp_path / "c.xml").write_text("<c/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(
            str(tmp_path), ext="xml,txt", include="*.xml"
        )
        keys = bag["docs"].keys()
        assert "a_xml" in keys
        assert "c_xml" in keys
        assert "b_txt" not in keys

    def test_exclude_glob_pattern(self, tmp_path):
        """exclude='*.bak' excludes matching files."""
        (tmp_path / "a.xml").write_text("<a/>", encoding="utf-8")
        (tmp_path / "old.bak").write_text("old", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(
            str(tmp_path), ext="xml,bak", exclude="*.bak"
        )
        keys = bag["docs"].keys()
        assert "a_xml" in keys
        assert "old_bak" not in keys

    def test_callback_filter(self, tmp_path):
        """callback returns False to discard the node."""
        (tmp_path / "big.xml").write_text("<a>" + "x" * 500 + "</a>", encoding="utf-8")
        (tmp_path / "small.xml").write_text("<a/>", encoding="utf-8")

        def only_big(nodeattr):
            return nodeattr["size"] > 100

        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), callback=only_big)
        keys = bag["docs"].keys()
        assert "big_xml" in keys
        assert "small_xml" not in keys

    def test_exclude_filter_applies_to_directories(self, tmp_path):
        """exclude=pattern also excludes subdirectories that match.

        Scenario: exclusion of __pycache__ or .git when mounting a tree.
        """
        (tmp_path / "docs").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "top.txt").write_text("x", encoding="utf-8")
        bag = Bag()
        bag["root"] = DirectoryResolver(str(tmp_path), exclude="__pycache__")
        keys = bag["root"].keys()
        assert "docs" in keys
        assert "__pycache__" not in keys


class TestDirectoryResolverCaption:
    def test_caption_true_auto_generates(self, tmp_path):
        """caption=True auto-generates caption with underscore -> spaces and capitalize."""
        (tmp_path / "my_doc.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), caption=True)
        result = bag["docs"]
        assert result.get_attr("my_doc_xml", "caption") == "My doc"

    def test_caption_callable_custom(self, tmp_path):
        """caption=callable: the callable receives the filename and returns the caption."""
        (tmp_path / "file.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(
            str(tmp_path), caption=lambda name: f"Caption[{name}]"
        )
        result = bag["docs"]
        assert result.get_attr("file_xml", "caption") == "Caption[file]"

    def test_caption_none_omits_attribute(self, tmp_path):
        """caption not set: the 'caption' attribute is not present."""
        (tmp_path / "doc.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        result = bag["docs"]
        node = result.get_node("doc_xml")
        assert node is not None
        assert not node.has_attr("caption")


class TestDirectoryResolverDropExt:
    def test_dropext_true_removes_extension_from_label(self, tmp_path):
        """dropext=True: label without '_ext' suffix."""
        (tmp_path / "doc.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), dropext=True)
        keys = bag["docs"].keys()
        # label without _xml
        assert "doc" in keys


class TestDirectoryResolverProcessors:
    def test_custom_processor(self, tmp_path):
        """processors={'ext': fn}: custom callable returns the value."""
        (tmp_path / "data.csv").write_text("a,b,c\n1,2,3", encoding="utf-8")

        def csv_processor(path):
            with open(path) as f:
                return f.read().upper()

        bag = Bag()
        bag["docs"] = DirectoryResolver(
            str(tmp_path), ext="csv", processors={"csv": csv_processor}
        )
        result = bag["docs"]
        # the node's value is the processor's return
        assert result.get_item("data_csv") == "A,B,C\n1,2,3"

    def test_processor_false_disables_handler(self, tmp_path):
        """processors={'xml': False}: the processor is disabled, uses default."""
        (tmp_path / "doc.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(
            str(tmp_path), ext="xml", processors={"xml": False}
        )
        result = bag["docs"]
        # the node exists, but the value comes from processor_default
        assert "doc_xml" in result.keys()


class TestDirectoryResolverExtMapping:
    def test_ext_mapping_colon_syntax(self, tmp_path):
        """ext='dat:xml' maps the .dat extension to the xml processor."""
        (tmp_path / "data.dat").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), ext="dat:xml")
        result = bag["docs"]
        assert "data_dat" in result.keys()


class TestDirectoryResolverContent:
    def test_xml_file_value_is_lazy_parsed_bag(self, tmp_path):
        """Accessing an .xml file node triggers parsing and returns a Bag."""
        (tmp_path / "doc.xml").write_text("<root><x>42</x></root>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        result = bag["docs"]
        parsed = result["doc_xml"]
        assert isinstance(parsed, Bag)
        assert parsed.get_item("root.x") == "42"

    def test_txt_file_value_is_lazy_bytes(self, tmp_path):
        """.txt file with ext='txt' produces a node with bytes value."""
        (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path), ext="txt")
        result = bag["docs"]
        content = result["note_txt"]
        assert content == b"hello"


class TestDirectoryResolverLabelSanitization:
    def test_dots_in_filename_replaced_by_underscore(self, tmp_path):
        """A filename with extra dots has dots replaced by '_' in the label."""
        (tmp_path / "my.v1.xml").write_text("<a/>", encoding="utf-8")
        bag = Bag()
        bag["docs"] = DirectoryResolver(str(tmp_path))
        keys = bag["docs"].keys()
        # the label replaces '.' with '_': "my_v1_xml"
        assert "my_v1_xml" in keys
