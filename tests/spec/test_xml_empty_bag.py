"""Auto-detected legacy XML preserves explicitly typed empty containers."""

import pytest

from genro_bag import Bag


@pytest.mark.parametrize('type_code', ['BAG', 'bag'])
@pytest.mark.parametrize('read', [Bag.from_xml])
@pytest.mark.parametrize('body', ['', '   '])
def test_typed_empty_bag_survives_xml_read(type_code, read, body):
    xml = (
        '<GenRoBag><parent>'
        f'<empty _T="{type_code}" caption="Empty">{body}</empty>'
        f'<closed _T="{type_code}"/>'
        '<text/><count _T="L">42</count>'
        '</parent></GenRoBag>'
    )
    result = read(xml)
    for path in ('parent.empty', 'parent.closed'):
        value = result[path]
        assert isinstance(value, Bag)
        assert len(value) == 0
    assert result.get_node('parent.empty').attr['caption'] == 'Empty'
    assert result['parent.text'] == ''
    assert result['parent.count'] == 42


def test_plain_xml_empty_element_remains_text():
    assert Bag.from_xml('<root><empty/></root>')['root.empty'] == ''
