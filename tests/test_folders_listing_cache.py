# -*- coding: utf-8 -*-
"""The folders scraper's listing cache must not keep an empty listing (#150).

xbmcvfs.listdir returns ([], []) for a failed SMB listing as well as for an empty folder. On the
Shield on 12.09.26 one 'Broken pipe' on shows/Daria (1997) was cached as empty for four hours, so
the next episode's prescrape skipped the share without trying it and played from rd_cloud, though
both files were on the share.
"""
import pytest

import scrapers.folders as folders

FOLDER = '/mnt/zurg/shows/Daria (1997)'
KEY = 'FOLDERSCRAPER_folders_%s' % FOLDER
FILES = [('Daria (1997) - S01e11 - Road Worrier.mkv', 'file'), ('Extras', 'folder')]


class _FakeCache(object):
    def __init__(self, stored=None):
        self.stored = dict(stored or {})
        self.sets = []

    def get(self, string):
        return self.stored.get(string)

    def set(self, string, data, expiration=720):
        self.sets.append((string, data, expiration))
        self.stored[string] = data


@pytest.fixture
def instance():
    return folders.source('folders', 'My Library', '/mnt/zurg/shows')


def _lister(monkeypatch, instance, result):
    calls = []
    monkeypatch.setattr(instance, '_make_dirs', lambda folder: calls.append(folder) or list(result))
    return calls


def test_failed_listing_is_not_cached(instance, monkeypatch):
    cache = _FakeCache()
    monkeypatch.setattr(folders, 'main_cache', cache)
    calls = _lister(monkeypatch, instance, [])
    assert instance._cached_listing(FOLDER) == []
    assert instance._cached_listing(FOLDER) == []
    assert calls == [FOLDER, FOLDER]
    assert cache.sets == []


def test_listing_is_cached_for_four_hours_under_the_folderscraper_key(instance, monkeypatch):
    cache = _FakeCache()
    monkeypatch.setattr(folders, 'main_cache', cache)
    calls = _lister(monkeypatch, instance, FILES)
    assert instance._cached_listing(FOLDER) == FILES
    assert instance._cached_listing(FOLDER) == FILES
    assert calls == [FOLDER]
    # The key delete_all_folderscrapers() matches with LIKE 'FOLDERSCRAPER_%'.
    assert cache.sets == [(KEY, FILES, 4)]


def test_empty_entry_left_by_an_older_version_is_listed_again(instance, monkeypatch):
    cache = _FakeCache({KEY: []})
    monkeypatch.setattr(folders, 'main_cache', cache)
    calls = _lister(monkeypatch, instance, FILES)
    assert instance._cached_listing(FOLDER) == FILES
    assert calls == [FOLDER]
    assert cache.sets == [(KEY, FILES, 4)]


def test_scrape_directory_lists_through_the_cache_helper(instance, monkeypatch):
    cache = _FakeCache()
    monkeypatch.setattr(folders, 'main_cache', cache)
    calls = _lister(monkeypatch, instance, [])
    instance.scrape_deadline = 0
    instance.title_query, instance.folder_query = 'daria', ('season01',)
    instance._scrape_directory(FOLDER, first_run=True)
    assert calls == [FOLDER]
    assert cache.sets == []
