import pytest

from genro_bag import Bag
from genro_bag.resolver import BagResolver


def test_direct_default_deep_skip_and_nested_stop():
    bag = Bag()
    bag['a.skip.child'] = 1
    bag['a.keep'] = 2
    bag['tail'] = 3
    direct = []
    assert bag.for_each(lambda node: direct.append(node.label)) is None
    assert direct == ['a', 'tail']
    seen = []

    def cb(node):
        seen.append(node.label)
        if node.label == 'skip':
            return False
        if node.label == 'keep':
            return node

    assert bag.for_each(cb, deep=True) is bag.get_node('a.keep')
    assert seen == ['a', 'skip', 'keep']
    assert not hasattr(bag, 'walk')


def test_static_dynamic_kwargs_and_callback_errors():
    calls = []

    class Resolver(BagResolver):
        def load(self):
            calls.append('load')
            return Bag({'leaf': 1})

    bag = Bag()
    bag.set_item('remote', Resolver())
    bag.for_each(lambda node: None, deep=True)
    assert calls == []
    bag.for_each(lambda node: False, deep=True, static=False)
    assert calls == []
    seen = []
    bag.for_each(lambda node, marker: seen.append((node.label, marker)),
                 deep=True, static=False, marker=42)
    assert seen == [('remote', 42), ('leaf', 42)]
    assert calls == ['load']
    with pytest.raises(TypeError):
        bag.for_each(None)
    with pytest.raises(ValueError, match='callback'):
        bag.for_each(lambda node: (_ for _ in ()).throw(ValueError('callback')))
