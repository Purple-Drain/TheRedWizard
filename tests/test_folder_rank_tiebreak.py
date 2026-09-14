# -*- coding: utf-8 -*-
"""The same file in two folder slots lists the lower slot first (#141).

zurg shows each debrid account as its own folder (__realdebrid__, __torbox__). With one in folder 1
and the other in folder 2, a release both accounts hold comes back twice, identical in quality,
provider and size, so the sort left the two in whichever order the folder threads finished. The
folder slot is now the sort's last key, so it breaks only those exact ties.
"""
import pytest

import scrapers.folders as folders
from modules import settings


def _item(folder_rank=None, size=4.2, quality_rank=1, provider_rank=6, provider='folders'):
    item = {'quality_rank': quality_rank, 'provider_rank': provider_rank, 'size_rank': size, 'scrape_provider': provider}
    if folder_rank is not None: item['folder_rank'] = folder_rank
    return item


@pytest.fixture
def sort_key(monkeypatch):
    def _key(order=1, size_direction='0'):
        values = {'redlight.results.sort_order': str(order), 'redlight.results.size_sort_direction': size_direction}
        monkeypatch.setattr(settings, 'get_setting', lambda key, default=None: values.get(key, default))
        return settings.results_sort_order()
    return _key


@pytest.mark.parametrize('slot, expected', [('folder1', 1), ('folder2', 2), ('folder5', 5), ('folders', 0)])
def test_source_takes_its_rank_from_the_slot(slot, expected):
    assert folders.source(slot, 'zurg', '/mnt/zurg').folder_rank == expected


@pytest.mark.parametrize('order', range(6))
def test_exact_tie_lists_the_lower_folder_first(sort_key, order):
    results = sorted([_item(2), _item(1)], key=sort_key(order))
    assert [i['folder_rank'] for i in results] == [1, 2]


def test_quality_and_size_still_outrank_the_folder(sort_key):
    key = sort_key(order=0)  # Quality, Provider, Size; size direction '0' is largest first
    assert [i['folder_rank'] for i in sorted([_item(1, quality_rank=2), _item(2, quality_rank=1)], key=key)] == [2, 1]
    assert [i['folder_rank'] for i in sorted([_item(1, size=5.0), _item(2, size=10.0)], key=key)] == [2, 1]


def test_results_without_a_folder_rank_still_sort(sort_key):
    rd = _item(provider='rd_cloud', provider_rank=8)
    folder = _item(2)
    assert sorted([rd, folder], key=sort_key(order=0)) == [folder, rd]
