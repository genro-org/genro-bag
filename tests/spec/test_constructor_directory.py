"""Directory sources retain a lazy directory node and file processors."""

import asyncio

import pytest

from genro_bag import Bag
from genro_bag.resolvers.directory_resolver import DirectoryResolver


@pytest.mark.parametrize('as_path', [False, True])
def test_from_file_directory_mounts_lazy_basename(tmp_path, as_path):
    folder = tmp_path / 'config'
    folder.mkdir()
    (folder / 'environment.xml').write_text('<GenRoBag><setting>ready</setting></GenRoBag>')
    (folder / 'nested').mkdir()
    (folder / 'nested' / 'more.xml').write_text('<GenRoBag><value>nested</value></GenRoBag>')
    source = folder if as_path else str(folder) + '/'
    bag = Bag.from_file(source)
    assert bag.keys() == ['config']
    node = bag.get_node('config')
    assert isinstance(node.resolver, DirectoryResolver)
    assert node.get_value(static=True) is None
    assert bag['config.environment_xml.setting'] == 'ready'
    assert bag['config.nested.more_xml.value'] == 'nested'


def test_directory_replace_and_event_loop_are_synchronous(tmp_path):
    folder = tmp_path / 'config'
    folder.mkdir()
    (folder / 'environment.xml').write_text('<GenRoBag><setting>ready</setting></GenRoBag>')
    async def lookup():
        bag = Bag({'old': True})
        assert bag.replace(Bag.from_file(folder)) is bag
        assert bag.keys() == ['config']
        return bag['config.environment_xml.setting']
    assert asyncio.run(lookup()) == 'ready'


def test_legacy_directory_option_is_not_inserted_as_data(tmp_path):
    folder = tmp_path / 'config'
    folder.mkdir()
    bag = Bag.from_file(str(folder))
    assert bag.keys() == ['config']
