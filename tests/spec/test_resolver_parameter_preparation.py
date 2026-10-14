"""Preparation is owned by the engine, not by parameter reads."""
import pytest

from genro_bag import Bag, BagResolver
from genro_bag.resolver import BagCbResolver


class Prepared(BagResolver):
    class_args = ['value']
    class_kwargs = {'factor': 2, 'cache_time': 0, 'read_only': True, 'as_bag': False}

    def init(self):
        self.preparations = 0

    def on_loading(self, params):
        self.preparations += 1
        params['value'] *= params['factor']
        if 'extra' in params:
            params['extra'] += 1
        return params

    def load(self):
        assert self.value == self.value
        return self.value, dict(self.kwargs)


def test_once_per_load_no_hook_on_parameter_reads_and_no_kw_api():
    resolver = Prepared(3, extra=4)
    assert resolver.value == 3
    assert resolver.kwargs.copy() == {'extra': 4}
    with pytest.raises(AttributeError):
        _ = resolver.kw
    assert resolver.preparations == 0
    assert resolver() == (6, {'extra': 5})
    assert resolver.preparations == 1
    assert resolver.value == 3
    assert dict(resolver.kwargs) == {'extra': 4}
    assert resolver.serialize()['kwargs']['value'] == 3
    assert resolver.serialize()['kwargs']['extra'] == 4


def test_named_positional_argument_is_not_an_extra():
    resolver = Prepared(value=3, factor=4, extra=5)
    assert resolver() == (12, {'extra': 6})


def test_cached_reads_and_reset():
    resolver = Prepared(3, cache_time=-1)
    assert resolver() == resolver()
    assert resolver.preparations == 1
    resolver.reset()
    resolver()
    assert resolver.preparations == 2


def test_explicit_read_after_reset_prepares_parameters():
    resolver = Prepared(3, read_only=False)
    node = Bag().set_item('result', resolver)
    resolver.reset()
    resolver()
    assert node.static_value == (6, {})
    assert resolver.preparations == 1
    assert resolver.value == 3


def test_preparation_restored_after_load_exception():
    class Failing(Prepared):
        def load(self):
            assert self.value == 6
            raise RuntimeError('load failed')

    resolver = Failing(3, extra=4)
    with pytest.raises(RuntimeError, match='load failed'):
        resolver()
    assert resolver.value == 3
    assert dict(resolver.kwargs) == {'extra': 4}
    assert resolver.expired


def test_retry_prepares_once_per_attempt_from_raw_parameters():
    class Retrying(Prepared):
        def load(self):
            assert self.value == 6
            if self.preparations < 3:
                raise ConnectionError('retry')
            return self.value

    resolver = Retrying(3, retry_policy={'max_attempts': 3, 'delay': 0,
                                       'jitter': False, 'on': (ConnectionError,)})
    assert resolver() == 6
    assert resolver.preparations == 3
    assert resolver.value == 3


def test_nested_call_restores_outer_prepared_parameters():
    class Nested(Prepared):
        def load(self):
            before = self.value
            if before == 6:
                assert self(value=4) == 8
                assert self.value == before
            return self.value

    resolver = Nested(3)
    assert resolver() == 6
    assert resolver.preparations == 2
    # Explicit call-time changes remain persistent; preparation does not.
    assert resolver.value == 4


def test_nested_hook_failure_restores_outer_context():
    class Nested(Prepared):
        def on_loading(self, params):
            if params['value'] == 4:
                raise RuntimeError('prepare failed')
            return super().on_loading(params)

        def load(self):
            with pytest.raises(RuntimeError, match='prepare failed'):
                self(value=4)
            return self.value

    resolver = Nested(3)
    assert resolver() == 6
    assert resolver.value == 4


@pytest.mark.parametrize('result,error', [(None, TypeError), ({}, ValueError)])
def test_invalid_hook_result_does_not_leak_context(result, error):
    class Invalid(Prepared):
        def on_loading(self, params):
            return result

    resolver = Invalid(3)
    with pytest.raises(error):
        resolver()
    assert resolver.value == 3


def test_callback_receives_only_prepared_extras_once():
    class Callback(BagCbResolver):
        def init(self):
            self.preparations = 0

        def on_loading(self, params):
            self.preparations += 1
            params['value'] += 1
            return params

    resolver = Callback(lambda **kwargs: kwargs, value=3)
    assert resolver() == {'value': 4}
    assert resolver.preparations == 1
    assert dict(resolver.kwargs) == {'value': 3}


def test_view_obtained_before_load_tracks_context_without_reprocessing():
    class WithView(Prepared):
        def init(self):
            super().init()
            self.saved_extras = self.kwargs

        def load(self):
            assert self.saved_extras['extra'] == 5
            self.saved_extras['extra'] = 9
            return dict(self.kwargs)

    resolver = WithView(3, extra=4)
    assert resolver() == {'extra': 9}
    assert resolver.saved_extras['extra'] == 4
    assert resolver.preparations == 1
