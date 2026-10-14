from genro_bag import Bag
from genro_bag.resolver import BagResolver


def test_leaves_include_only_non_bag_values_with_relative_paths():
    bag = Bag()
    bag['customer.address.city'] = 'Rome'
    bag['customer.empty'] = Bag()
    bag['null'] = None
    bag['zero'] = 0
    bag['false'] = False
    bag['text'] = ''
    bag['list'] = [1, 2]
    expected = [('customer.address.city', 'Rome'), ('null', None),
                ('zero', 0), ('false', False), ('text', ''), ('list', [1, 2])]
    assert bag.get_leaves() == expected
    assert bag.getLeaves() == expected
    assert bag['customer'].get_leaves() == [('address.city', 'Rome')]
    assert Bag().get_leaves() == []


def test_leaves_resolve_uncached_nodes_once_per_occurrence():
    calls = []

    class Resolver(BagResolver):
        classKwargs = {'cacheTime': 0}

        def load(self):
            calls.append(self.label)
            return self.result

    def resolver(label, result):
        r = Resolver()
        r.label = label
        r.result = result
        return r

    child = Bag()
    child.set_item('value', resolver('value', None))
    bag = Bag()
    bag.set_item('branch', resolver('branch', child))
    assert bag.get_leaves() == [('branch.value', None)]
    assert calls == ['branch', 'value']


def test_shared_subtree_is_reported_at_both_paths():
    child = Bag({'leaf': 1})
    bag = Bag()
    bag.set_item('a', child)
    bag.set_item('b', child)
    assert bag.get_leaves() == [('a.leaf', 1), ('b.leaf', 1)]
