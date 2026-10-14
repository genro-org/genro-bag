"""Native list ingestion never guesses node specifications."""
import pytest
from genro_bag import Bag
from genro_bag.resolver import BagCbResolver


def test_single_node_triple_is_rejected():
    with pytest.raises(TypeError):
        Bag(('project', None, {'name': 'demo'}))


def test_list_of_tuples_preserves_them_as_leaves():
    entries = [('first', 10), ('second', 20, {'caption': 'Second'})]
    bag = Bag(entries)
    assert bag.keys() == ['r_00', 'r_01']
    assert bag['r_00'] is entries[0]
    assert bag['r_01'] is entries[1]


def test_regular_lists_are_recursive_and_positional():
    bag = Bag([['label', 10], {'name': 'demo'}, (1, 2)])
    assert bag.keys() == ['r_00', 'r_01', 'r_02']
    assert bag['r_00.r_00'] == 'label'
    assert bag['r_00.r_01'] == 10
    assert bag['r_01.name'] == 'demo'
    assert bag['r_02'] == (1, 2)


def test_explicit_resolver_node_stays_lazy_on_subclass():
    class CustomBag(Bag):
        pass
    calls = []
    bag = CustomBag()
    bag.set_item('lazy', BagCbResolver(lambda: calls.append(1) or 42))
    assert type(bag) is CustomBag
    assert calls == []
    assert bag['lazy'] == 42
    assert calls == [1]


def test_tuple_root_validation_is_atomic():
    bag = Bag({'existing': 1})
    with pytest.raises(TypeError):
        bag.fill_from(('bad', 3, 99))
    assert bag.keys() == ['existing']
    assert bag['existing'] == 1
