# -*- coding: utf-8 -*-
"""TV show and movie rows as data (#163): the indexers compute a JSON-safe row, and the render
functions draw it. A row that went through JSON must draw exactly what the build drew, so a saved
list looks the same as a built one.

The split itself was checked against main's indexers call for call (15,360 setting and title
combinations, no difference) before this was committed; these tests keep the round trip honest.
"""
import json
import types
from datetime import date
from urllib.parse import urlencode

import pytest

import indexers.movies as movies
import indexers.tvshows as tvshows
from modules import settings
from modules.movie_rows import render_movie_row
from modules.tvshow_rows import render_tvshow_row


class _Recorder:
	def __init__(self): self.calls = []
	def __getattr__(self, name):
		def call(*args, **kwargs):
			self.calls.append((name, json.loads(json.dumps([args, kwargs], default=repr))))
			if name == 'getVideoInfoTag': return self
		return call


def _actor(**kwargs): return ['actor', sorted(kwargs.items())]


_WS = types.SimpleNamespace(
	progress_aired_eps=lambda meta: meta.get('total_aired_eps') or 0,
	get_watched_status_tvshow=lambda info, aired: ((1 if info and info >= aired else 0), info or 0, aired - (info or 0)),
	get_progress_status_tvshow=lambda watched, aired: int(watched * 100 / aired) if aired else 0,
	get_progress_status_movie=lambda bookmarks, tmdb_id: bookmarks.get(tmdb_id),
	get_watched_status_movie=lambda info, tmdb_id: 1 if tmdb_id in info else 0,
	get_resume_seconds=lambda progress, duration: float(int(float(progress) / 100 * duration)))

_CAST = [{'name': 'A', 'role': 'R', 'thumbnail': 't'}]


@pytest.fixture
def users(monkeypatch):
	for name in ('trakt_user_active', 'simkl_user_active', 'punchplay_user_active', 'tmdblist_user_active'):
		monkeypatch.setattr(settings, name, lambda: False)
	monkeypatch.setattr(settings, 'mdblist_user_active', lambda: True)
	monkeypatch.setattr(settings, 'configured_external_scraper_slots', lambda: [])


def _setup(obj, is_external):
	obj.id_type, obj.tmdb_api_key, obj.mpaa_region, obj.current_date, obj.current_time = 'tmdb_id', 'k', 'US', date(2026, 9, 14), 1757800000
	obj.is_anime_list, obj.rpdb_api_key, obj.rpdb_format = None, None, ''
	obj.poster_empty, obj.fanart_empty, obj.is_external = 'pe', 'fe', is_external
	obj.build_url = lambda d: 'plugin://plugin.video.redlight/?' + urlencode(sorted((k, str(v)) for k, v in d.items()))
	obj.window_command = 'ActivateWindow(Videos,%s,return)' if is_external else 'Container.Update(%s)'
	obj.cm_sort_order, obj.custom_cm_menu, obj.widget_hide_watched, obj.ai_model_active = {'extras': 0, 'options': 1, 'mdblist_watchlist': 2}, True, False, False
	obj.make_listitem, obj.kodi_actor = _Recorder, _actor
	obj.items, obj.rows, obj.keep_rows = [], [], True
	obj.append = obj.items.append


def _tvshow(monkeypatch, is_external, aired, watched):
	monkeypatch.setattr(tvshows, 'watched_status', _WS)
	monkeypatch.setattr(tvshows, 'tvshow_meta', lambda *a, **k: {'tmdb_id': 1, 'tvdb_id': 11, 'imdb_id': 'tt1', 'title': 'Show', 'year': '2020',
		'premiered': '2020-01-01', 'trailer': 'tr', 'poster': 'p', 'fanart': 'f', 'clearlogo': 'c', 'landscape': 'l', 'total_seasons': 2,
		'total_aired_eps': aired, 'original_title': 'O', 'plot': 'plot', 'genre': ['Drama'], 'tagline': 'tag', 'studio': ['S'], 'writer': ['W'],
		'director': ['D'], 'votes': 5, 'mpaa': 'TV-14', 'duration': 3000, 'country': ['US'], 'status': 'Returning Series', 'rating': 7.5, 'cast': _CAST})
	obj = object.__new__(tvshows.TVShows)
	_setup(obj, is_external)
	obj.all_episodes, obj.open_extras, obj.skip_inprogress, obj.in_progress_show_ids = 0, False, False, set()
	obj.watched_info = {'1': watched} if watched else {}
	obj.build_tvshow_content(0, 1)
	return obj


def _movie(monkeypatch, is_external, progress):
	monkeypatch.setattr(movies, 'watched_status', _WS)
	monkeypatch.setattr(movies, 'movie_meta', lambda *a, **k: {'tmdb_id': 1, 'imdb_id': 'tt1', 'title': 'Movie', 'year': '2019', 'premiered': '2019-05-01',
		'trailer': 'tr', 'poster': 'p', 'fanart': 'f', 'clearlogo': 'c', 'landscape': 'l', 'original_title': 'O', 'plot': 'plot', 'genre': ['Drama'],
		'tagline': 'tag', 'studio': ['S'], 'writer': ['W'], 'director': ['D'], 'votes': 5, 'mpaa': 'PG', 'duration': 6000, 'country': ['US'],
		'rating': 6.5, 'cast': _CAST, 'extra_info': {'collection_id': 9, 'collection_name': 'Set'}})
	obj = object.__new__(movies.Movies)
	_setup(obj, is_external)
	obj.play_mode, obj.playback_key, obj.open_extras, obj.open_movieset, obj.skip_inprogress, obj.movieset_list_active = 'playback.media', 'media', False, False, False, False
	obj.watched_info, obj.bookmarks = {}, {'1': progress} if progress else {}
	obj.build_movie_content(0, 1)
	return obj


def _drawn_again(obj, render):
	(url, row, is_folder), position = obj.rows[0]
	return url, render(json.loads(json.dumps(row)), _Recorder, _actor).calls, is_folder


@pytest.mark.parametrize('is_external, aired, watched', [(True, 10, 4), (False, 10, 4), (True, 10, 0), (True, 5, 5)])
def test_a_tvshow_row_draws_the_same_after_a_json_round_trip(monkeypatch, users, is_external, aired, watched):
	obj = _tvshow(monkeypatch, is_external, aired, watched)
	(url, listitem, is_folder), position = obj.items[0]
	assert _drawn_again(obj, render_tvshow_row) == (url, listitem.calls, is_folder)
	assert is_folder is True and 'mode=build_season_list' in url
	props = [c for c in listitem.calls if c[0] == 'setProperties'][0][1][0][0]
	assert props['unwatchedepisodes'] == str(aired - watched) and 'redlight.extras_params' in props


def test_a_tvshow_left_out_leaves_no_row(monkeypatch, users):
	obj = object.__new__(tvshows.TVShows)
	monkeypatch.setattr(tvshows, 'tvshow_meta', lambda *a, **k: {'blank_entry': True})
	_setup(obj, True)
	obj.build_tvshow_content(0, 1)
	assert obj.items == [] and obj.rows == []


@pytest.mark.parametrize('is_external, progress', [(True, '42'), (False, '42'), (True, None)])
def test_a_movie_row_draws_the_same_after_a_json_round_trip(monkeypatch, users, is_external, progress):
	obj = _movie(monkeypatch, is_external, progress)
	(url, listitem, is_folder), position = obj.items[0]
	assert _drawn_again(obj, render_movie_row) == (url, listitem.calls, is_folder)
	names = [c[0] for c in listitem.calls]
	assert ('setResumePoint' in names) == bool(progress)
	assert names[-4:] == ['setLabel', 'addContextMenuItems', 'setArt', 'setProperties']


def test_rows_are_only_kept_when_asked(monkeypatch, users):
	obj = _movie(monkeypatch, True, None)
	assert len(obj.rows) == 1
	obj.items, obj.rows, obj.keep_rows = [], [], False
	obj.append = obj.items.append
	obj.build_movie_content(0, 1)
	assert len(obj.items) == 1 and obj.rows == []
