from genro_bag import Bag
from genro_bag.resolver import BagResolver


def test_traverse_yields_original_nodes_in_depth_first_order():
    bag = Bag()
    bag.set_item('customer.name', 'Ada')
    bag.set_item('customer.address.city', 'Rome')
    bag.set_item('invoice', 42)
    paths = ['customer', 'customer.name', 'customer.address',
             'customer.address.city', 'invoice']
    expected = [bag.get_node(path) for path in paths]
    nodes = list(bag.traverse())
    assert len(nodes) == len(expected)
    assert all(node is original for node, original in zip(nodes, expected, strict=True))
    nodes[1].value = 'Grace'
    assert bag['customer.name'] == 'Grace'
    assert list(Bag().traverse()) == []


def test_traverse_is_lazy_and_does_not_resolve_values():
    calls = []

    class Resolver(BagResolver):
        def load(self):
            calls.append('load')
            return Bag({'leaf': 1})

    bag = Bag()
    bag.set_item('lazy', Resolver())
    iterator = bag.traverse()
    assert iter(iterator) is iterator
    assert next(iterator) is bag.get_node('lazy')
    assert list(iterator) == []
    assert calls == []


def test_traverse_visits_shared_subtrees_at_each_occurrence():
    shared = Bag({'leaf': 1})
    bag = Bag()
    bag.set_item('a', shared)
    bag.set_item('b', shared)
    assert [node.label for node in bag.traverse()] == ['a', 'leaf', 'b', 'leaf']
