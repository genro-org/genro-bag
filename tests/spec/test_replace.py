import warnings

import pytest

from genro_bag import Bag
from genro_bag.bagnode import BagNode
from genro_bag.resolver import BagResolver


class ConstantResolver(BagResolver):
    def load(self):
        return 42


def test_bag_replace_is_atomic_copies_and_preserves_identity():
    root = Bag({'child': {'old': 1}})
    root.set_backref()
    child = root['child']
    events = []
    root.subscribe('test', any=lambda **event: events.append(event))
    source = Bag({'fresh': {'leaf': 2}})
    assert child.replace(source) is child
    assert root['child'] is child
    assert child.keys() == ['fresh']
    assert len(events) == 1
    assert events[0]['oldvalue']['old'] == 1
    assert child['fresh'].parent is child
    source['fresh.leaf'] = 3
    assert child['fresh.leaf'] == 2
    assert child.replace(child) is child
    assert len(events) == 1
    with pytest.raises(TypeError):
        child.replace({})
    assert len(events) == 1
    child.replace(Bag())
    assert len(child) == 0


def test_node_replace_copies_resolver_attributes_and_keeps_location():
    root = Bag({'target': {'old': 1}, 'sibling': 0})
    root.set_backref()
    target = root.get_node('target')
    oldvalue = target.static_value
    target.set_attr({'obsolete': 1})
    source = BagNode(None, 'source', None, resolver=ConstantResolver())
    source.set_attr({'nullable': None, 'color': 'red'}, _remove_null_attributes=False)
    events = []
    target.subscribe('test', lambda **event: events.append(event))
    assert target.replace(source) is target
    assert root.get_node(0) is target
    assert target.label == 'target'
    assert target.attr == {'nullable': None, 'color': 'red'}
    assert oldvalue.parent_node is None
    assert target.resolver is not source.resolver
    assert target.resolver.parent_node is target
    assert source.resolver.parent_node is source
    assert target.static_value is None
    assert len(events) == 1
    assert events[0]['evt'] == 'upd_value_attr'
    assert target.get_value() == 42
    events.clear()
    target.replace(target)
    assert events == []
    previous_resolver = target.resolver
    target.replace(BagNode(None, 'other', 9))
    assert target.resolver is None
    assert previous_resolver.parent_node is None
    assert target.attr == {}
    with pytest.raises(TypeError):
        target.replace(Bag())


def test_replace_copies_nested_bags_and_resolvers_without_evaluating():
    source = Bag({'nested': {}})
    source['nested'].set_item('lazy', ConstantResolver())
    source.set_backref()
    copied = Bag().replace(source)
    original = source.get_node('nested.lazy')
    clone = copied.get_node('nested.lazy')
    assert clone.static_value is None
    assert clone.resolver is not original.resolver
    assert original.resolver.parent_node is original
    assert clone.resolver.parent_node is clone


def test_deprecated_fill_methods_and_warning_free_construction():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        bag = Bag({'x': 1})
        bag.replace(Bag({'y': 2}))
    assert not caught
    for name in ('fillFrom',):
        with pytest.warns(DeprecationWarning):
            assert getattr(bag, name)({'z': 3}) is bag
        assert bag.keys() == ['z']


def test_modern_fill_from_is_not_deprecated():
    bag = Bag()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        assert bag.fill_from({'value': 1}) is bag
    assert not caught
    assert bag['value'] == 1
