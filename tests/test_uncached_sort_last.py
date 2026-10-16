# -*- coding: utf-8 -*-
"""Cached results list before uncached ones for every provider (media-stack #45, #258).

With TorBox's Include Uncached Results on, an uncached TorBox REMUX used to sort as if it were
cached and sat above every cached Real-Debrid row. The new Results setting "Rank Uncached Results
Below Cached" (default on) puts every cached row first; off restores the old interleaving.
"""
import pytest

import modules.sources as sources
from modules import settings


def _sources():
    return object.__new__(sources.Sources)


def _settings(monkeypatch, **values):
    base = {'redlight.tb.include_uncached': 'true', 'redlight.tb.cache_check': 'true'}
    base.update(values)
    monkeypatch.setattr(settings, 'get_setting', lambda key, default=None: base.get(key, default))


def _rows():
    return [
        {'name': 'tb remux uncached', 'cache_provider': 'Uncached TorBox', 'quality': '1080p'},
        {'name': 'rd remux cached', 'cache_provider': 'Real-Debrid', 'quality': '1080p'},
        {'name': 'tb x265 uncached', 'cache_provider': 'Uncached TorBox', 'quality': '1080p'},
        {'name': 'rd x265 cached', 'cache_provider': 'Real-Debrid', 'quality': '1080p'},
        {'name': 'rd uncached', 'cache_provider': 'Uncached Real-Debrid', 'quality': '1080p'},
    ]


def test_setting_defaults_on(monkeypatch):
    _settings(monkeypatch)
    assert settings.uncached_sort_last() is True
    _settings(monkeypatch, **{'redlight.results.uncached_sort_last': 'false'})
    assert settings.uncached_sort_last() is False


def test_cached_rows_list_first_and_each_group_keeps_its_order(monkeypatch):
    _settings(monkeypatch)
    names = [i['name'] for i in _sources()._sort_uncached_results(_rows())]
    assert names == ['rd remux cached', 'rd x265 cached', 'tb remux uncached', 'tb x265 uncached', 'rd uncached']


def test_off_restores_the_included_provider_interleaving(monkeypatch):
    _settings(monkeypatch, **{'redlight.results.uncached_sort_last': 'false'})
    names = [i['name'] for i in _sources()._sort_uncached_results(_rows())]
    # Included TorBox uncached rows stay where the quality sort put them; the RD uncached row still drops.
    assert names == ['tb remux uncached', 'rd remux cached', 'tb x265 uncached', 'rd x265 cached', 'rd uncached']


def test_custom_sort_log_carries_the_cache_state(monkeypatch):
    _settings(monkeypatch, **{'redlight.filter.sort_to_top': '3', 'redlight.filter.preferred_filters': 'REMUX'})
    lines = []
    monkeypatch.setattr(sources.kodi_utils, 'logger', lambda tag, msg: lines.append((tag, msg)))
    monkeypatch.setattr(sources, 'get_setting', settings.get_setting, raising=False)
    s = _sources()
    s.media_type, s.tmdb_id, s.autoplay = 'episode', 8592, False
    s._log_custom_sort_summary(_rows(), True)
    assert lines and lines[0][0] == 'CustomSort'
    assert 'uncached=3' in lines[0][1] and 'uncached_last=True' in lines[0][1] and 'Uncached TorBox' in lines[0][1]
