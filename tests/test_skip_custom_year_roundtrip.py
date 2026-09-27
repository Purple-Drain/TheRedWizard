# -*- coding: utf-8 -*-
"""The explicit skip's params survive a plugin URL without a 'None' year (#1, 27.09.26).

Shield 23:37:46: after the OSD next-episode button every scraper raised "invalid literal for int()
with base 10: 'None'". Sources stores custom_year=None in meta on every play, next_episode_info
copied it into url_params, and run_plugin's build_url turned None into the string 'None', which
get_search_year then preferred over the real year.
"""
from urllib.parse import parse_qsl, urlparse

import modules.episode_tools as et
from modules import kodi_utils


def _next_params(monkeypatch, meta):
    monkeypatch.setattr(et, 'watched_info_episode', lambda tmdb_id: {})
    monkeypatch.setattr(et, 'get_next', lambda s, e, *a, **k: (s, e + 1))
    monkeypatch.setattr(et, 'get_watched_status_episode', lambda *a: 0)
    monkeypatch.setattr(et, 'episodes_meta', lambda season, meta: [
        {'episode': 11, 'title': "St. Valentine's Day", 'premiered': '2009-02-05', 'plot': ''}])
    monkeypatch.setattr(et, 'date_offset', lambda: 0)
    monkeypatch.setattr(et, 'playback_key', lambda: 'media')
    monkeypatch.setattr(et.kodi_utils, 'logger', lambda *a: None)
    return et.EpisodeTools(meta, {'play_type': 'autoplay_nextep'}).next_episode_info()


def _round_trip(params):
    url = kodi_utils.build_url(params)
    return dict(parse_qsl(urlparse(url).query))


def test_none_custom_year_is_not_sent(monkeypatch):
    meta = {'title': '30 Rock', 'rootname': '30 Rock', 'tmdb_id': 4608, 'season': 3, 'episode': 10,
            'year': 2006, 'custom_title': None, 'custom_year': None}
    params = _next_params(monkeypatch, meta)
    assert isinstance(params, dict), params
    assert 'custom_year' not in params
    assert _round_trip(params).get('custom_year') is None


def test_real_custom_year_is_kept(monkeypatch):
    meta = {'title': '30 Rock', 'rootname': '30 Rock', 'tmdb_id': 4608, 'season': 3, 'episode': 10,
            'year': 2006, 'custom_year': 2007}
    params = _next_params(monkeypatch, meta)
    assert _round_trip(params)['custom_year'] == '2007'
