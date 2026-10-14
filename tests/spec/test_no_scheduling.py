"""Bag owns no scheduler: attaching, invalidating and expiring never load data."""

import asyncio
from datetime import datetime, timedelta

import pytest

from genro_bag import Bag
from genro_bag.resolver import BagResolver
from genro_bag.resolvers import BagCbResolver


@pytest.mark.parametrize('option,value', [('interval', 1), ('interval', None),
                                         ('reactive', True), ('reactive', False)])
def test_removed_resolver_options_fail_explicitly(option, value):
    with pytest.raises(TypeError, match='Removed resolver options'):
        BagCbResolver(lambda: 1, **{option: value})


def test_subclass_cannot_restore_scheduler_defaults():
    class OldResolver(BagResolver):
        class_kwargs = {'interval': 5}

    with pytest.raises(TypeError, match='interval'):
        OldResolver()


@pytest.mark.parametrize('method', ['subscribe', 'unsubscribe'])
@pytest.mark.parametrize('options', [{'timer': lambda **kw: None}, {'interval': 1}])
def test_removed_subscriptions_fail_without_changing_observers(method, options):
    bag = Bag({'value': 0})
    events = []
    bag.subscribe('watch', update=lambda **kw: events.append(kw))
    with pytest.raises(TypeError):
        getattr(bag, method)('watch', any=True, **options)
    bag['value'] = 1
    assert len(events) == 1


def test_reset_rejects_refresh_before_invalidating_cache():
    resolver = BagCbResolver(lambda: 1, cache_time=-1)
    assert resolver() == 1
    assert not resolver.expired
    with pytest.raises(TypeError):
        resolver.reset(refresh=True)
    assert not resolver.expired


@pytest.mark.asyncio
async def test_attach_change_expire_reset_and_detach_never_schedule():
    calls = []

    def load(factor):
        calls.append(factor)
        return factor * 2

    resolver = BagCbResolver(load, factor=2, cache_time=60)
    bag = Bag()
    before = asyncio.all_tasks()
    bag['value'] = resolver
    await asyncio.sleep(0)
    assert calls == []
    events = []
    bag.subscribe('watch', update=lambda **kw: events.append(kw['evt']))

    async def read():
        value = bag['value']
        return await value if asyncio.iscoroutine(value) else value

    assert await read() == 4
    assert events == []  # Pulling data is not a mutation event.
    bag.set_attr('value', factor=3)
    await asyncio.sleep(0)
    assert calls == [2]
    assert resolver.expired
    assert events == ['upd_attrs']
    assert await read() == 6
    resolver._cache_last_update = datetime.now() - timedelta(seconds=61)
    await asyncio.sleep(0)
    assert calls == [2, 3]
    assert resolver.expired
    assert await read() == 6
    resolver.reset()
    await asyncio.sleep(0)
    assert calls == [2, 3, 3]
    assert await read() == 6
    bag.pop_node('value')
    await asyncio.sleep(0)
    assert calls == [2, 3, 3, 3]
    assert asyncio.all_tasks() == before
    assert events == ['upd_attrs']


def test_application_can_explicitly_refresh_and_publish():
    resolver = BagCbResolver(lambda: 42, cache_time=-1)
    bag = Bag({'result': None})
    events = []
    bag.subscribe('watch', update=lambda **kw: events.append(kw['evt']))
    resolver.reset()
    bag['result'] = resolver()
    assert bag['result'] == 42
    assert events == ['upd_value']
