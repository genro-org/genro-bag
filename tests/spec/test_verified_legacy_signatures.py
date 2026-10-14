# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Legacy signatures exercised by current GenroPy Bag callers."""

from genro_bag import Bag
from genro_bag.resolver import BagResolver


class _BranchResolver(BagResolver):
    classKwargs = {"cacheTime": 0}

    def __init__(self, calls):
        self._calls = calls
        super().__init__()

    def load(self):
        self._calls.append("load")
        return Bag({'leaf': 1})


def test_for_each_static_controls_resolver_traversal():
    calls = []
    bag = Bag()
    bag.set_item("branch", _BranchResolver(calls))

    static_labels = []
    bag.for_each(lambda node: static_labels.append(node.label), static=True, deep=True)
    assert static_labels == ["branch"]
    assert calls == []

    deep_labels = []
    bag.for_each(lambda node: deep_labels.append(node.label), static=False, deep=True)
    assert deep_labels == ["branch", "leaf"]
    assert calls == ["load"]


def test_get_legacy_mode_keyword_preserves_modern_default():
    calls = []
    bag = Bag()
    bag.set_item("branch", _BranchResolver(calls))

    assert bag.get("branch") is None
    assert bag.get("branch", mode="static") is None
    assert calls == []
    assert isinstance(bag.get("branch", mode=None), Bag)
    assert calls == ["load"]


def test_digest_and_columns_accept_legacy_keyword_spellings():
    bag = Bag()
    bag.set_item("first", 1, _attributes={"rank": 10})
    bag.set_item("second", 2, _attributes={"rank": 20})

    assert bag.digest("#k,#v", asColumns=True) == [
        ["first", "second"],
        [1, 2],
    ]
    assert bag.columns("rank", attrMode=True) == [[10, 20]]


def test_update_verified_legacy_named_flags_remain_supported():
    target = Bag({'value': 1})
    source = Bag({'value': None, 'added': 2})

    target.update(source, resolved=True, ignoreNone=True)

    assert target["value"] == 1
    assert target["added"] == 2
