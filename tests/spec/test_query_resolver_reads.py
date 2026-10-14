from genro_bag import Bag
from genro_bag.resolver import BagResolver


def resolver(callback):
    class Resolver(BagResolver):
        classKwargs = {'cacheTime': 0}

        def load(self):
            return callback()

    return Resolver()


def test_dynamic_query_reuses_branch_and_null_leaf_values_without_caching_between_queries():
    calls = []
    child = Bag()

    def leaf():
        calls.append('leaf')
        return None

    def branch():
        calls.append('branch')
        return child

    child.set_item('leaf', resolver(leaf))
    bag = Bag()
    bag.set_item('branch', resolver(branch))
    for _ in range(2):
        assert bag.query('#p,#v,#v', deep=True, branch=False, static=False) == [
            ('branch.leaf', None, None)]
    assert calls == ['branch', 'leaf', 'branch', 'leaf']


def test_changing_resolver_yields_one_consistent_row():
    calls = []

    def changing():
        calls.append('load')
        return len(calls)

    bag = Bag()
    bag.set_item('n', resolver(changing))
    assert bag.query('#v,#v', static=False) == [(1, 1)]
    assert calls == ['load']


def test_metadata_static_and_limited_queries_do_not_resolve_unneeded_values():
    calls = []

    def load():
        calls.append('load')
        return Bag({'leaf': 1})

    bag = Bag()
    bag.set_item('branch', resolver(load), _attributes={'caption': 'Branch'})
    assert bag.query('#k,#a.caption', static=False) == [('branch', 'Branch')]
    assert bag.query('#p', deep=True, static=False, limit=1) == ['branch']
    assert bag.query('#v', deep=True) == [None]
    assert calls == []
    iterator = bag.query('#p,#v', deep=True, branch=False, static=False, iter=True)
    assert calls == []
    assert next(iterator) == ('branch.leaf', 1)
    assert calls == ['load']
