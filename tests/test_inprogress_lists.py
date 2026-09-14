# -*- coding: utf-8 -*-
"""The In Progress widgets' saved lists (#163).

The acceptance test is the first one: answering from a saved list must not load `requests` or any
provider API module. The rest pin the keys (what moves them, what makes them unknown), that every
setting the lists read is in a key, and that page 1 of a real build, Next Page item included, is served
back drawn the same without any metadata work.
"""
import os
import re
import sys
import json
import inspect
import sqlite3
import subprocess
from datetime import date

import pytest

import caches.widget_cache as wc
import modules.kodi_utils as kodi_utils
import modules.nextep_list_cache as nlc
import modules.inprogress_lists as ipl
import modules.saved_lists as sl
import modules.watched_status as ws
from modules import settings
import indexers.movies as movies
import indexers.tvshows as tvshows

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), 'plugin.video.redlight', 'resources', 'lib')
PROVIDER_MODULES = ('requests', 'urllib3', 'apis.trakt_api', 'apis.simkl_api', 'apis.mdblist_api', 'apis.punchplay_api')


def test_the_saved_list_path_loads_no_provider_module_and_no_requests():
	code = ('import sys; sys.path.insert(0, %r); import kodi_stub; kodi_stub.install(); sys.path.insert(0, %r)\n'
			'import modules.inprogress_lists, modules.saved_lists, modules.tvshow_rows, modules.movie_rows\n'
			'print(sorted(m for m in sys.modules if m.split(".")[0] in ("requests", "urllib3") or m in %r))' % (HERE, LIB, PROVIDER_MODULES))
	out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
	assert out.returncode == 0, out.stderr
	assert out.stdout.strip() == '[]'


# --- settings coverage ----------------------------------------------------------------------------

_WITH_ARGUMENTS = {'lists_sort_order', 'paginate', 'page_limit', 'rpdb_info', 'media_open_action'}
_CM_HELPERS = {'append_external_scraper_settings_cm', 'append_list_shortcut_context_menus', 'append_source_shortcut_context_menus'}
# Read, but no effect on a page-1 home widget: thread count, the in-addon Jump To item.
_NO_EFFECT = {'max_threads', 'jump_to_enabled'}


def _settings_read(*functions):
	read = set()
	for fn in functions: read |= set(re.findall(r'settings\.(\w+)\(', inspect.getsource(fn)))
	return read


def _check(read, getters):
	assert read - _WITH_ARGUMENTS - _CM_HELPERS - _NO_EFFECT <= set(getters), read - _WITH_ARGUMENTS - _CM_HELPERS - _NO_EFFECT - set(getters)
	for helper in _CM_HELPERS & read:
		inner = set(re.findall(r'\b(\w+_user_active|configured_external_scraper_slots)\(\)', inspect.getsource(getattr(settings, helper))))
		assert inner <= set(getters), helper


def test_tvshow_setting_getters_cover_the_list_and_its_rows():
	T = tvshows.TVShows
	_check(_settings_read(T.__init__, T.is_anime, T.fetch_list, T.worker, T.tvshow_row, T.paginate_list, ws.get_in_progress_tvshows,
		ws.active_tvshows_information, ws.watched_info_tvshow), ipl.TVSHOW_SETTING_GETTERS)


def test_movie_setting_getters_cover_the_list_and_its_rows():
	M = movies.Movies
	_check(_settings_read(M.__init__, M.fetch_list, M.worker, M.movie_row, M.paginate_list, ws.get_in_progress_movies, ws._sort_progress_list),
		ipl.MOVIE_SETTING_GETTERS)


# --- a shared in-memory profile -------------------------------------------------------------------

class _NoClose:
	def __init__(self, c): self._c = c
	def __getattr__(self, name): return getattr(self._c, name)
	def close(self): pass
	def __enter__(self): return self
	def __exit__(self, *a): return False


class _Recorder:
	def __init__(self): self.calls = []
	def __getattr__(self, name):
		def call(*args, **kwargs):
			self.calls.append((name, json.loads(json.dumps([args, kwargs], default=repr))))
			if name == 'getVideoInfoTag': return self
		return call


def _actor(**kwargs): return ['actor', sorted(kwargs.items())]


class _Pool:
	"""TaskPool without threads or the metadata database: rows in their enumerate order."""
	def tasks(self, target, items, max_size=20, db_name=None):
		for item in items: target(item)
		return []
	def tasks_enumerate(self, target, items, max_size=20, db_name=None):
		for position, item in enumerate(items, 1): target(position, item)
		return []


@pytest.fixture
def db(monkeypatch):
	conn = sqlite3.connect(':memory:', check_same_thread=False)
	conn.isolation_level = None
	conn.execute('CREATE TABLE maincache (id text unique, data text, expires integer)')
	conn.execute('CREATE TABLE watched (db_type text not null, media_id text not null, season integer, episode integer, last_played text, title text, unique (db_type, media_id, season, episode))')
	conn.execute('CREATE TABLE progress (db_type text not null, media_id text not null, season integer, episode integer, resume_point text, curr_time text, last_played text, resume_id integer, title text, unique (db_type, media_id, season, episode))')
	conn.execute('CREATE TABLE mdblist_data (id text unique, data text)')
	shared = _NoClose(conn)
	monkeypatch.setattr(wc, 'connect_database', lambda name: shared)
	monkeypatch.setattr(nlc, 'connect_database', lambda name: shared)
	monkeypatch.setattr(ws, 'get_database', lambda watched_indicators=None: shared)
	conn.execute("INSERT INTO mdblist_data VALUES ('mdblist_hidden_items_dropped', '[5]')")
	return conn


@pytest.fixture
def prefs(monkeypatch):
	values = dict((name, False) for name in set(ipl.TVSHOW_SETTING_GETTERS) | set(ipl.MOVIE_SETTING_GETTERS))
	values.update({'watched_indicators': 3, 'exclude_specials_from_progress': False, 'tv_progress_location': 0, 'include_anime_tvshow': True,
		'tmdb_api_key': 'k', 'mpaa_region': 'US', 'default_all_episodes': 0, 'cm_sort_order': {'extras': 0, 'options': 1},
		'cm_default_order': {'extras': 0, 'options': 1}, 'playback_key': 'media', 'configured_external_scraper_slots': []})
	for name in values: monkeypatch.setattr(settings, name, lambda name=name: values[name])
	monkeypatch.setattr(settings, 'lists_sort_order', lambda setting: 1)
	monkeypatch.setattr(settings, 'paginate', lambda is_home: is_home)
	monkeypatch.setattr(settings, 'page_limit', lambda is_home: 2)
	monkeypatch.setattr(settings, 'rpdb_info', lambda media_type: {'rpdb_api_key': None, 'rpdb_format': None})
	monkeypatch.setattr(settings, 'media_open_action', lambda media_type: 0)
	monkeypatch.setattr(settings, 'max_threads', lambda: 2)
	monkeypatch.setattr(settings, 'jump_to_enabled', lambda: False)
	monkeypatch.setattr(ipl, 'get_datetime', lambda: date(2026, 9, 14))
	return values


# --- the keys -------------------------------------------------------------------------------------

def test_the_tvshow_key_moves_with_every_local_input(db, prefs):
	base = ipl.tvshow_key(True)
	assert base and ipl.tvshow_key(True) == base
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 1, '2026-09-01T10:00:00.000Z', 'A')")
	watched = ipl.tvshow_key(True)
	assert watched != base
	db.execute("INSERT INTO progress VALUES ('episode', '1', 1, 2, '40.0', '600', '2026-09-01', 0, 'A')")
	progress = ipl.tvshow_key(True)
	assert progress != watched
	db.execute("UPDATE mdblist_data SET data = '[5, 6]' WHERE id = 'mdblist_hidden_items_dropped'")
	dropped = ipl.tvshow_key(True)
	assert dropped != progress
	prefs['widget_hide_watched'] = True
	assert ipl.tvshow_key(True) != dropped
	# Movie progress is not the TV list's business.
	prefs['widget_hide_watched'] = False
	db.execute("INSERT INTO progress VALUES ('movie', '9', '', '', '30.0', '900', '2026-09-01', 0, 'M')")
	assert ipl.tvshow_key(True) == dropped


def test_the_movie_key_moves_with_movie_progress_and_watched_movies_only(db, prefs):
	base = ipl.movie_key(True)
	assert base
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 1, '2026-09-01T10:00:00.000Z', 'A')")
	assert ipl.movie_key(True) == base
	db.execute("INSERT INTO progress VALUES ('movie', '9', '', '', '30.0', '900', '2026-09-01', 0, 'M')")
	progress = ipl.movie_key(True)
	assert progress != base
	db.execute("INSERT INTO watched VALUES ('movie', '8', '', '', '2026-09-02T10:00:00.000Z', 'N')")
	assert ipl.movie_key(True) != progress


def test_no_key_in_addon_for_a_remote_provider_or_without_the_dropped_row(db, prefs):
	assert ipl.tvshow_key(False) is None and ipl.movie_key(False) is None
	for provider in (1, 2, 4):
		prefs['watched_indicators'] = provider
		assert ipl.tvshow_key(True) is None and ipl.movie_key(True) is None
	prefs['watched_indicators'] = 3
	db.execute("DELETE FROM mdblist_data WHERE id = 'mdblist_hidden_items_dropped'")
	assert ipl.tvshow_key(True) is None and ipl.movie_key(True)


def test_the_date_is_in_both_keys(db, prefs, monkeypatch):
	tv, movie = ipl.tvshow_key(True), ipl.movie_key(True)
	monkeypatch.setattr(ipl, 'get_datetime', lambda: date(2026, 9, 15))
	assert ipl.tvshow_key(True) != tv and ipl.movie_key(True) != movie


# --- build, store, serve --------------------------------------------------------------------------

@pytest.fixture
def widget(db, prefs, monkeypatch):
	"""A home widget: kodi_utils captured, three shows and three movies in progress, two per page."""
	directory, props, meta_calls = {'items': []}, {}, []
	monkeypatch.setattr(kodi_utils, 'external', lambda: True)
	monkeypatch.setattr(kodi_utils, 'get_property', lambda name: props.get(name, ''))
	monkeypatch.setattr(kodi_utils, 'set_property', lambda name, value: props.__setitem__(name, value))
	monkeypatch.setattr(kodi_utils, 'get_icon', lambda name: 'icons/%s.png' % name)
	monkeypatch.setattr(kodi_utils, 'addon_fanart', lambda: 'fanart.png')
	monkeypatch.setattr(kodi_utils, 'get_addon_fanart', lambda: 'fanart.png')
	monkeypatch.setattr(kodi_utils, 'make_listitem', lambda offscreen=True: _Recorder())
	monkeypatch.setattr(kodi_utils, 'kodi_actor', lambda: _actor)
	monkeypatch.setattr(kodi_utils, 'add_items', lambda handle, items: directory['items'].extend((u, li.calls, f) for u, li, f in items))
	monkeypatch.setattr(kodi_utils, 'add_item', lambda handle, url, li, isFolder: directory['items'].append((url, li.calls, isFolder)))
	monkeypatch.setattr(kodi_utils, 'set_content', lambda handle, content: directory.__setitem__('content', content))
	monkeypatch.setattr(kodi_utils, 'set_category', lambda handle, category: directory.__setitem__('category', category))
	monkeypatch.setattr(kodi_utils, 'set_sort_method', lambda *a, **k: None)
	monkeypatch.setattr(kodi_utils, 'end_directory', lambda handle, cacheToDisc=True: directory.__setitem__('ended', cacheToDisc))
	monkeypatch.setattr(kodi_utils, 'set_view_mode', lambda *a, **k: directory.__setitem__('view', a))
	monkeypatch.setattr(kodi_utils, 'set_browse_exit_params', lambda *a, **k: None)
	monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: directory.setdefault('log', []).append(message))
	monkeypatch.setattr(sys, 'argv', ['plugin://plugin.video.redlight/', '1', ''])
	for mod in (tvshows, movies):
		monkeypatch.setattr(mod, 'get_datetime', lambda: date(2026, 9, 14))
		monkeypatch.setattr(mod, 'TaskPool', _Pool)
	monkeypatch.setattr(ws, 'progress_aired_eps', lambda meta: meta['total_aired_eps'])
	for i in (1, 2, 3):
		db.execute("INSERT INTO watched VALUES ('episode', ?, 1, 1, ?, ?)", (str(i), '2026-09-1%sT20:00:00.000Z' % i, 'Show %s' % i))
		db.execute("INSERT INTO progress VALUES ('movie', ?, '', '', '40.0', '600', ?, 0, ?)", (str(i), '2026-09-1%s' % i, 'Movie %s' % i))
	monkeypatch.setattr(ws, 'get_in_progress_tvshows', lambda dummy, page_no: [{'media_id': i} for i in (1, 2, 3)])
	monkeypatch.setattr(ws, 'get_in_progress_movies', lambda dummy, page_no: [{'media_id': str(i)} for i in (1, 2, 3)])
	common = {'imdb_id': 'tt', 'year': '2020', 'premiered': '2020-01-01', 'trailer': '', 'poster': 'p', 'fanart': 'f', 'original_title': 'O',
		'plot': 'plot', 'genre': ['Drama'], 'tagline': '', 'studio': ['S'], 'writer': [], 'director': [], 'votes': 1, 'mpaa': 'PG', 'duration': 3000,
		'country': ['US'], 'rating': 7.0, 'cast': [{'name': 'A', 'role': 'R', 'thumbnail': 't'}]}
	def tvshow_meta(id_type, media_id, *a, **k):
		meta_calls.append(media_id)
		return dict(common, tmdb_id=media_id, tvdb_id=10, title='Show %s' % media_id, total_seasons=1, total_aired_eps=10, status='Returning Series')
	def movie_meta(id_type, media_id, *a, **k):
		meta_calls.append(media_id)
		return dict(common, tmdb_id=media_id, title='Movie %s' % media_id, extra_info={})
	monkeypatch.setattr(tvshows, 'tvshow_meta', tvshow_meta)
	monkeypatch.setattr(movies, 'movie_meta', movie_meta)
	directory['props'], directory['meta_calls'] = props, meta_calls
	return directory


def _fresh(directory):
	props, meta_calls = directory['props'], directory['meta_calls']
	directory.clear()
	directory.update({'items': [], 'props': props, 'meta_calls': meta_calls})
	del meta_calls[:]


@pytest.mark.parametrize('action, build, serve, content', [
	('in_progress_tvshows', lambda p: tvshows.TVShows(p).fetch_list(), ipl.serve_tvshows, 'tvshows'),
	('in_progress_movies', lambda p: movies.Movies(p).fetch_list(), ipl.serve_movies, 'movies')])
def test_page_one_is_saved_and_served_the_same_without_metadata(widget, action, build, serve, content):
	params = {'action': action, 'category_name': 'In Progress'}
	build(params)
	built = dict(widget)
	assert len(built['items']) == 3  # two rows and the Next Page item
	url, calls, is_folder = built['items'][-1]
	assert 'new_page=2' in url and is_folder is True and ('setLabel', [['Next Page (2) >>'], {}]) in calls
	assert widget['meta_calls']
	_fresh(widget)
	assert serve(params)
	assert widget['meta_calls'] == []
	assert widget['items'] == built['items']
	assert (widget['content'], widget['category'], widget['ended']) == (content, 'In Progress', False)
	assert 'view' not in widget  # a home widget: the indexers set no view there
	assert 'list cache hit: 3 listed' in widget['log'][-1]


def test_a_mark_after_the_build_is_a_miss(widget, db):
	params = {'action': 'in_progress_tvshows', 'category_name': 'In Progress'}
	tvshows.TVShows(params).fetch_list()
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-09-14T21:00:00.000Z', 'Show 1')")
	widget['props'][sl.REVALIDATE_PROP % ipl.TVSHOWS.list_name(True)] = sl.DONE
	assert not ipl.serve_tvshows(params)
	assert 'state changed since it was stored' in widget['log'][-1]


def test_only_page_one_of_the_home_widget_is_served(widget, monkeypatch):
	params = {'action': 'in_progress_movies', 'category_name': 'In Progress'}
	movies.Movies(params).fetch_list()
	assert not ipl.serve_movies(dict(params, new_page='2', paginate_start='2'))
	monkeypatch.setattr(kodi_utils, 'external', lambda: False)
	log_before = len(widget.get('log', []))
	assert not ipl.serve_movies(params)
	assert len(widget.get('log', [])) == log_before  # not even asked: no miss line for an in-addon listing


def test_an_in_addon_build_stores_nothing(widget, db, monkeypatch):
	monkeypatch.setattr(kodi_utils, 'external', lambda: False)
	tvshows.TVShows({'action': 'in_progress_tvshows', 'category_name': 'In Progress'}).fetch_list()
	assert db.execute("SELECT COUNT(*) FROM maincache WHERE id LIKE 'WIDGET_LIST_in_progress%'").fetchone()[0] == 0


# --- the next start: saved list first, then the service's rebuild ---------------------------------

def test_a_changed_state_at_the_next_start_shows_the_saved_list_then_rebuilds(widget, db):
	params = {'action': 'in_progress_tvshows', 'category_name': 'In Progress'}
	tvshows.TVShows(params).fetch_list()
	built, props = list(widget['items']), widget['props']
	_fresh(widget)
	props.clear()  # the next start: Kodi clears Home properties
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-09-14T21:00:00.000Z', 'Show 1')")
	assert ipl.serve_tvshows(params)
	assert widget['meta_calls'] == [] and widget['items'] == built
	assert 'In Progress TV list cache stale (state changed since it was stored): 3 listed' in widget['log'][-1]
	name = ipl.TVSHOWS.list_name(True)
	assert json.loads(props[sl.SERVED_PROP % name])['key'] == sl.STALE_KEY  # so the service always rebuilds it
	props[sl.REVALIDATE_PROP % name] = sl.REBUILD  # what SavedListsRevalidate sets
	assert not ipl.serve_tvshows(params)
	assert props[sl.REVALIDATE_PROP % name] == sl.DONE
	assert 'rebuild asked for by the service' in widget['log'][-1]


class _Clock:
	def __init__(self): self.now = 1000.0
	def time(self): return self.now


class _Monitor:
	def __init__(self, clock, limit=400): self.clock, self.calls, self.limit = clock, 0, limit
	def abortRequested(self): return self.calls >= self.limit
	def waitForAbort(self, seconds):
		self.calls += 1
		self.clock.now += seconds
		return self.calls >= self.limit


def test_the_service_rebuilds_only_the_real_list_that_is_behind(widget, db, monkeypatch):
	import time as time_module
	import service
	params, props = {'action': 'in_progress_tvshows', 'category_name': 'In Progress'}, widget['props']
	tvshows.TVShows(params).fetch_list()  # records In Progress TV's current key
	monkeypatch.setattr(nlc, 'cache_key', lambda is_external, anime=False: 'n1')
	props[sl.SERVED_PROP % nlc.list_name(True)] = json.dumps({'key': 'n1', 'external': True, 'anime': False})
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-09-14T21:00:00.000Z', 'Show 1')")
	clock, refreshed = _Clock(), []
	monkeypatch.setattr(time_module, 'time', clock.time)
	monkeypatch.setattr(kodi_utils, 'kodi_refresh', lambda: refreshed.append(clock.now))
	monkeypatch.setattr(kodi_utils, 'service_shutting_down', lambda monitor=None: False)
	monkeypatch.setattr(kodi_utils, 'kodi_player', lambda: type('P', (), {'isPlayingVideo': lambda self: False})())
	service.SavedListsRevalidate().run(_Monitor(clock, limit=8))  # stops before REBUILD_WAIT
	assert len(refreshed) == 1
	assert props[sl.REVALIDATE_PROP % ipl.TVSHOWS.list_name(True)] == sl.REBUILD
	assert nlc.REVALIDATE_PROP not in props
	assert sl.REVALIDATE_PROP % ipl.MOVIES.list_name(True) not in props
	assert 'in_progress_tvshows_list_widget shown from an older state' in widget['log'][-1]


# --- review findings on #164 ----------------------------------------------------------------------

def _stored_names(db):
	return sorted(r[0] for r in db.execute("SELECT id FROM maincache WHERE id LIKE 'WIDGET_LIST_in_progress%'"))


def test_anime_in_progress_is_its_own_list(widget, db):
	plain = {'action': 'in_progress_tvshows', 'category_name': 'In Progress'}
	anime = dict(plain, is_anime_list='true')
	tvshows.TVShows(plain).fetch_list()
	_fresh(widget)
	assert not ipl.serve_tvshows(anime)  # never answered from In Progress's list
	_fresh(widget)
	tvshows.TVShows(anime).fetch_list()
	built_anime = list(widget['items'])
	assert 'is_anime_list=true' in built_anime[-1][0]
	_fresh(widget)
	assert ipl.serve_tvshows(anime) and widget['items'] == built_anime
	_fresh(widget)
	assert ipl.serve_tvshows(plain) and 'is_anime_list' not in widget['items'][-1][0]
	assert len(_stored_names(db)) == 2


def test_a_listing_with_anime_turned_off_is_not_saved_or_served(widget, db):
	params = {'action': 'in_progress_tvshows', 'category_name': 'In Progress', 'is_anime_list': 'false'}
	tvshows.TVShows(params).fetch_list()
	assert _stored_names(db) == []
	assert not ipl.serve_tvshows(params)


def test_a_custom_order_listing_is_not_saved(widget, db):
	tvshows.TVShows({'action': 'in_progress_tvshows', 'category_name': 'In Progress', 'custom_order': 'true'}).fetch_list()
	movies.Movies({'action': 'in_progress_movies', 'category_name': 'In Progress', 'custom_order': 'true'}).fetch_list()
	assert _stored_names(db) == []


def test_a_change_from_the_build_s_own_sync_is_rebuilt_not_filed_under_it(widget, db, prefs, monkeypatch):
	import types
	prefs['mdblist_user_active'] = True
	def sync(): db.execute("INSERT INTO watched VALUES ('episode', '2', 1, 2, '2026-09-14T21:00:00.000Z', 'Show 2')")
	monkeypatch.setitem(sys.modules, 'apis.mdblist_api', types.SimpleNamespace(mdblist_sync_activities=sync))
	tvshows.TVShows({'action': 'in_progress_tvshows', 'category_name': 'In Progress'}).fetch_list()
	assert _stored_names(db) == []
	served = json.loads(widget['props'][sl.SERVED_PROP % ipl.TVSHOWS.list_name(True)])
	assert served['key'] != ipl.tvshow_key(True)  # so the service asks for a rebuild


def test_in_progress_tv_is_fresh_for_the_airing_facts_expiry():
	assert ipl.TVSHOWS.fresh_for == 6 * 3600 and ipl.TVSHOWS.variants == (False, True)
	assert ipl.MOVIES.fresh_for == sl.LIST_TTL and ipl.MOVIES.variants == (False,)
