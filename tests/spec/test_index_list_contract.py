from genro_bag import Bag
from genro_bag.resolver import BagResolver


def test_index_list_resolves_branches_and_supports_text():
    calls = []

    class Resolver(BagResolver):
        classKwargs = {'cacheTime': 60}

        def load(self):
            calls.append('load')
            return Bag({'name': 'Ada', 'empty': Bag()})

    bag = Bag()
    bag.set_item('customer', Resolver())
    bag.set_item('tail', None)
    expected = ['customer', 'customer.name', 'customer.empty', 'tail']
    assert calls == []
    assert bag.getIndexList() == expected
    assert calls == ['load']
    assert bag.getIndexList() == ['.'.join(parts) for parts, _node in bag.getIndex()]
    assert bag.getIndexList(asText=True) == '\n'.join(expected)
    assert Bag().getIndexList() == []
    assert Bag().getIndexList(asText=True) == ''


def test_index_list_follows_get_index_shared_subtree_policy():
    shared = Bag({'leaf': 1})
    bag = Bag()
    bag.set_item('a', shared)
    bag.set_item('b', shared)
    assert bag.getIndexList() == ['a', 'a.leaf', 'b']
