"""XML label disambiguation must not discard siblings or rewrite XML tags."""
import pytest

from genro_bag import Bag


@pytest.mark.parametrize('read', [Bag.from_xml])
def test_existing_suffix_is_preserved(read):
    bag = read('<root><item>A</item><item_1>B</item_1><item>C</item></root>')['root']
    assert [(n.label, n.value, n.xml_tag) for n in bag] == [
        ('item', 'A', 'item'), ('item_1', 'B', 'item_1'), ('item_2', 'C', 'item')]
    restored = Bag.from_xml('<root>' + bag.to_xml() + '</root>')['root']
    assert [(n.value, n.xml_tag) for n in restored] == [
        ('A', 'item'), ('B', 'item_1'), ('C', 'item')]


def test_plain_duplicates_keep_original_tag():
    bag = Bag.from_xml('<root><item>A</item><item>B</item><item>C</item></root>')['root']
    assert [(n.label, n.xml_tag) for n in bag] == [
        ('item', 'item'), ('item_1', 'item'), ('item_2', 'item')]


@pytest.mark.parametrize('attribute,options', [('_tag', {}), ('name', {'tag_attribute': 'name'})])
def test_remapped_labels_keep_source_tags(attribute, options):
    xml = (f'<root><a {attribute}="entry">A</a>'
           f'<b {attribute}="entry_1">B</b><c {attribute}="entry">C</c></root>')
    bag = Bag.from_xml(xml, **options)['root']
    assert [(n.label, n.value, n.xml_tag) for n in bag] == [
        ('entry', 'A', 'a'), ('entry_1', 'B', 'b'), ('entry_2', 'C', 'c')]


def test_nested_bags_have_independent_label_namespaces():
    bag = Bag.from_xml('<root><group><x>A</x><x_1>B</x_1><x>C</x></group>'
                       '<group><x>D</x><x>E</x></group></root>')['root']
    assert bag['group.x_2'] == 'C'
    assert bag['group_1.x_1'] == 'E'


def test_deepcopy_preserves_explicit_tags_at_every_level():
    bag = Bag()
    bag.set_item('outer.inner', 'value', node_tag='semantic')
    bag.get_node('outer').xml_tag = 'group'
    bag.get_node('outer.inner').xml_tag = 'item'
    copied = bag.deepcopy()
    assert copied.get_node('outer').xml_tag == 'group'
    assert copied.get_node('outer.inner').xml_tag == 'item'
    assert copied.get_node('outer.inner').node_tag == 'semantic'
    assert copied.get_node('outer.inner') is not bag.get_node('outer.inner')
