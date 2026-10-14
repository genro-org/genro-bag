"""Integration tests against the sibling JS checkout when available."""
import base64
import json
import shutil
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from genro_bag import Bag
from tests.spec.test_ingestion import IngestionResolver

JS_ROOT = Path(__file__).resolve().parents[3] / 'genro-bag-js'
pytestmark = pytest.mark.skipif(
    shutil.which('node') is None or not (JS_ROOT / 'node_modules/@genrojs/tytx').exists(),
    reason='cross-language tests require Node and the sibling genro-bag-js checkout',
)


def run_js(script, data):
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script], cwd=JS_ROOT,
        input=json.dumps(data), text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize('recursive', [True, False])
@pytest.mark.parametrize('source', [
    {'name': 'Ada', 'active': True, 'none': None},
    ['Ada', 37, True, None],
    {'person': {'name': 'Ada'}, 'items': [{'x': 1}], 'a.b': [1, 2]},
    [[1, 2], {'name': 'Ada', 'tags': ['a', 'b']}, 42],
])
def test_native_conversion_parity(source, recursive):
    def shape(bag):
        return [[n.label, shape(n.static_value) if isinstance(n.static_value, Bag)
                 else {'leaf': n.static_value}] for n in bag]

    result = run_js('''
        import {Bag} from './src/index.js';
        let text=''; for await (const chunk of process.stdin) text+=chunk;
        const {source, recursive}=JSON.parse(text);
        const shape=bag => [...bag].map(n=>[n.label,n.getValue(true) instanceof Bag
            ? shape(n.getValue(true)) : {leaf:n.getValue(true)}]);
        process.stdout.write(JSON.stringify(shape(new Bag(source,{recursive}))));
    ''', {'source': source, 'recursive': recursive})
    assert result == shape(Bag(source, recursive=recursive))


@pytest.mark.parametrize('transport', ['json', 'msgpack'])
@pytest.mark.parametrize('compact', [False, True])
def test_python_js_python_marked_bag_and_signed_resolver(transport, compact):
    bag = Bag({'a.b': {'?literal': [1, 2]}})
    bag.set_item('date', datetime(2026, 10, 3, 9, 30, tzinfo=UTC))
    bag.set_item('decimal', Decimal('12.30'), _attributes={'caption': 'amount'}, node_tag='number')
    bag.set_item('binary', b'hello')
    bag.set_item('kept', [{'label': 'ordinary', 'value': 1}])
    bag.set_item('resolver', IngestionResolver())
    wire = bag.to_tytx(transport, compact=compact, sign_key='server-only')
    result = run_js('''
        import {Bag} from './src/index.js';
        let text=''; for await (const chunk of process.stdin) text+=chunk;
        const {wire,transport,compact}=JSON.parse(text);
        const input=transport==='msgpack'?new Uint8Array(Buffer.from(wire,'base64')):wire;
        const bag=Bag.fromTytx(input,transport);
        const output=bag.toTytx(transport,compact);
        process.stdout.write(JSON.stringify(transport==='msgpack'?Buffer.from(output).toString('base64'):output));
    ''', {'wire': base64.b64encode(wire).decode() if isinstance(wire, bytes) else wire,
          'transport': transport, 'compact': compact})
    restored = Bag.from_tytx(base64.b64decode(result) if transport == 'msgpack' else result,
                             transport, sign_key='server-only')
    assert restored.keys() == bag.keys()
    assert restored._nodes['a.b'].value._nodes['?literal'].value.values() == [1, 2]
    assert restored['date'] == bag['date']
    assert restored['decimal'] == bag['decimal']
    assert restored.get_node('decimal').node_tag == 'number'
    assert restored.get_attr('decimal', 'caption') == 'amount'
    assert restored['binary'] == b'hello'
    assert restored['kept'] == [{'label': 'ordinary', 'value': 1}]
    assert isinstance(restored.get_resolver('resolver'), IngestionResolver)
