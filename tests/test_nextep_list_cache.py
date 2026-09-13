# -*- coding: utf-8 -*-
"""Next Episodes finished-list cache and lazy provider imports (#155).

The acceptance test is the first one: the widget's hit path must not load `requests`, which was
4.4 s of a cold boot. The rest pin the key (what moves it, what makes it unknown), the stored list's
own expiry, and that a served list renders exactly what the build rendered.
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
import modules.watched_status as ws
from modules import settings
from modules.episode_rows import render_episode_row
import indexers.episodes as episodes

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), 'plugin.video.redlight', 'resources', 'lib')
PROVIDER_MODULES = ('requests', 'urllib3', 'apis.trakt_api', 'apis.simkl_api', 'apis.mdblist_api', 'apis.punchplay_api')


def _loaded_after_import(*module_names):
	code = ('import sys; sys.path.insert(0, %r); import kodi_stub; kodi_stub.install(); sys.path.insert(0, %r)\n'
			'import importlib\n'
			'for name in %r: importlib.import_module(name)\n'
			'print(sorted(m for m in sys.modules if m.split(".")[0] in ("requests", "urllib3") or m in %r))'
			% (HERE, LIB, module_names, PROVIDER_MODULES))
	out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
	assert out.returncode == 0, out.stderr
	return out.stdout.strip()


# --- the 4.4 s ------------------------------------------------------------------------------------

def test_the_hit_path_loads_no_provider_module_and_no_requests():
	assert _loaded_after_import('modules.nextep_list_cache', 'modules.watched_status', 'modules.episode_rows') == '[]'


def test_the_episodes_indexer_itself_no_longer_loads_requests():
	# A miss for an MDBList user still imports mdblist_api for its activity sync, but only then.
	assert _loaded_after_import('indexers.episodes') == '[]'


def test_provider_names_stay_patchable_and_import_on_first_call(monkeypatch):
	calls = []
	fake = type(sys)('apis.trakt_api')
	fake.trakt_progress = lambda *a, **k: calls.append((a, k)) or 'done'
	monkeypatch.setitem(sys.modules, 'apis.trakt_api', fake)
	assert ws.trakt_progress('clear_progress', 'episode', 1, 0, 1, 2, 'r') == 'done'
	assert calls == [(('clear_progress', 'episode', 1, 0, 1, 2, 'r'), {})]
	assert ws.trakt_progress.__name__ == 'trakt_progress'


# --- a shared in-memory profile --------------------------------------------------------------------

class _NoClose:
	def __init__(self, c): self._c = c
	def __getattr__(self, name): return getattr(self._c, name)
	def close(self): pass
	def __enter__(self): return self
	def __exit__(self, *a): return False


@pytest.fixture
def db(monkeypatch):
	conn = sqlite3.connect(':memory:', check_same_thread=False)
	conn.isolation_level = None
	conn.execute('CREATE TABLE maincache (id text unique, data text, expires integer)')
	conn.execute('CREATE TABLE watched (db_type text not null, media_id text not null, season integer, episode integer, last_played text, title text, unique (db_type, media_id, season, episode))')
	conn.execute('CREATE TABLE progress (db_type text not null, media_id text not null, season integer, episode integer, resume_point text, curr_time text, last_played text, resume_id integer, title text, unique (db_type, media_id, season, episode))')
	conn.execute('CREATE TABLE mdblist_data (id text unique, data text)')
	conn.execute('CREATE TABLE favourites (db_type text not null, tmdb_id text not null, title text not null, unique (db_type, tmdb_id))')
	conn.execute('CREATE TABLE watched_status (db_type text not null unique, status text)')
	shared = _NoClose(conn)
	monkeypatch.setattr(wc, 'connect_database', lambda name: shared)
	monkeypatch.setattr(nlc, 'connect_database', lambda name: shared)
	monkeypatch.setattr(ws, 'get_database', lambda watched_indicators=None: shared)
	conn.execute("INSERT INTO mdblist_data VALUES ('mdblist_hidden_items_dropped', '[5]')")
	conn.execute("INSERT INTO mdblist_data VALUES ('mdblist_watchlist_live', \"{'shows': [{'id': 9}]}\")")
	return conn


@pytest.fixture
def prefs(monkeypatch):
	"""Settings as a dict: every getter the key reads, overridable per test."""
	values = dict((name, 0) for name in nlc.SETTING_GETTERS)
	values.update({'watched_indicators': 3, 'nextep_include_unwatched': 0, 'nextep_include_unaired': False, 'nextep_include_airdate': False,
		'nextep_airing_today': False, 'nextep_method': 1, 'nextep_sort_key': 'last_played', 'nextep_sort_direction': True, 'nextep_limit_history': False,
		'cm_sort_order': {'extras': 0, 'options': 1}, 'cm_default_order': {'extras': 0, 'options': 1}, 'playback_key': 'media', 'tmdb_api_key': 'k',
		'mpaa_region': 'US', 'configured_external_scraper_slots': [], 'mdblist_user_active': False, 'trakt_user_active': False,
		'simkl_user_active': False, 'punchplay_user_active': False, 'tmdblist_user_active': False, 'single_ep_unwatched_episodes': False,
		'single_ep_unwatched_in_title': False, 'avoid_episode_spoilers': False, 'widget_hide_watched': False, 'ignore_articles': False,
		'include_anime_tvshow': False})
	for name in nlc.SETTING_GETTERS: monkeypatch.setattr(settings, name, lambda name=name: values[name])
	monkeypatch.setattr(settings, 'single_ep_display_format', lambda is_external: 1)
	monkeypatch.setattr(settings, 'rpdb_info', lambda media_type: {'rpdb_api_key': None, 'rpdb_format': None})
	monkeypatch.setattr(settings, 'max_threads', lambda: 2)
	monkeypatch.setattr(nlc, 'get_datetime', lambda: date(2026, 9, 12))
	return values


# --- the key ---------------------------------------------------------------------------------------

def test_key_moves_with_every_local_input(db, prefs):
	base = nlc.cache_key(True)
	assert base and nlc.cache_key(True) == base
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 1, '2026-09-01T10:00:00.000Z', 'A')")
	watched = nlc.cache_key(True)
	assert watched != base
	db.execute("INSERT INTO progress VALUES ('episode', '1', 1, 2, '40.0', '600', '2026-09-01', 0, 'A')")
	progress = nlc.cache_key(True)
	assert progress != watched
	db.execute("UPDATE mdblist_data SET data = '[5, 6]' WHERE id = 'mdblist_hidden_items_dropped'")
	dropped = nlc.cache_key(True)
	assert dropped != progress
	prefs['nextep_method'] = 0
	assert nlc.cache_key(True) != dropped
	prefs['nextep_method'] = 1
	assert nlc.cache_key(True) == dropped
	assert nlc.cache_key(False) != dropped
	assert nlc.cache_key(True, True) != dropped


def test_watchlist_and_favourites_count_only_when_included(db, prefs):
	base = nlc.cache_key(True)
	db.execute("UPDATE mdblist_data SET data = 'changed' WHERE id = 'mdblist_watchlist_live'")
	db.execute("INSERT INTO favourites VALUES ('tvshow', '7', 'F')")
	assert nlc.cache_key(True) == base  # include_unwatched off: neither is read
	prefs['nextep_include_unwatched'] = 3
	both = nlc.cache_key(True)
	db.execute("INSERT INTO favourites VALUES ('tvshow', '8', 'G')")
	assert nlc.cache_key(True) != both
	db.execute("DELETE FROM mdblist_data WHERE id = 'mdblist_watchlist_live'")
	assert nlc.cache_key(True) is None  # cleared by an activity sync: only the provider knows it now


def test_no_key_without_the_dropped_row_or_for_a_remote_only_provider(db, prefs):
	db.execute("DELETE FROM mdblist_data WHERE id = 'mdblist_hidden_items_dropped'")
	assert nlc.cache_key(True) is None
	for provider in (1, 2, 4):
		prefs['watched_indicators'] = provider
		assert nlc.cache_key(True) is None


def test_the_date_is_only_in_the_key_when_a_date_setting_is_on(db, prefs, monkeypatch):
	base = nlc.cache_key(True)
	monkeypatch.setattr(nlc, 'get_datetime', lambda: date(2026, 9, 13))
	assert nlc.cache_key(True) == base
	prefs['nextep_include_unaired'] = True
	unaired = nlc.cache_key(True)
	monkeypatch.setattr(nlc, 'get_datetime', lambda: date(2026, 9, 14))
	assert nlc.cache_key(True) != unaired


def test_setting_getters_cover_everything_the_next_episodes_build_reads():
	source = inspect.getsource(episodes.build_single_episode)
	read = set(re.findall(r'settings\.(\w+)\(', source))
	calendar_or_threading = {'calendar_display_format', 'calendar_date_label_options', 'calendar_sort_order', 'flatten_episodes', 'max_threads'}
	with_arguments = {'single_ep_display_format', 'rpdb_info'}
	context_menu_helpers = {'append_source_shortcut_context_menus', 'append_external_scraper_settings_cm', 'append_list_shortcut_context_menus'}
	assert read - calendar_or_threading - with_arguments - context_menu_helpers <= set(nlc.SETTING_GETTERS)
	for helper in context_menu_helpers:
		inner = set(re.findall(r'\b(\w+_user_active|configured_external_scraper_slots)\(\)', inspect.getsource(getattr(settings, helper))))
		assert inner <= set(nlc.SETTING_GETTERS), helper


def test_mdblist_row_names_match_the_api_module():
	from apis import mdblist_api
	assert mdblist_api._MDBL_DROPPED_CACHE_KEY == nlc.MDBLIST_DROPPED_ROW
	assert "'%s'" % nlc.MDBLIST_WATCHLIST_ROW in inspect.getsource(mdblist_api._mdbl_watchlist_raw)


# --- rows and rendering ----------------------------------------------------------------------------

def _plain(value):
	if isinstance(value, (list, tuple)): return [_plain(i) for i in value]
	if isinstance(value, dict): return dict((k, _plain(v)) for k, v in value.items())
	return value


class _Recorder:
	def __init__(self): self.calls = []
	def __getattr__(self, name):
		def call(*args, **kwargs):
			self.calls.append((name, _plain(args), _plain(kwargs)))
			if name == 'getVideoInfoTag': return self
		return call


def _actor(**kwargs): return ('actor', sorted(kwargs.items()))


def _row():
	return {'label': 'Show: 1x02. Two', 'cm': [('[B]Extras[/B]', 'RunPlugin(x)'), ('[B]Options[/B]', 'RunPlugin(y)')],
		'properties': {'unwatchedepisodes': '3', 'WatchedProgress': 40.0, 'episode_type': ''},
		'info': {'original_title': 'Show', 'tvshow_title': 'Show', 'title': 'Show: 1x02. Two', 'genres': ['Drama'], 'playcount': 0,
			'season': 1, 'episode': 2, 'plot': 'p', 'first_aired': '2026-09-01', 'duration': 3000, 'imdb': 'tt1',
			'unique_ids': {'imdb': 'tt1', 'tmdb': '1', 'tvdb': '2'}, 'countries': ['US'], 'trailer': '', 'tvshow_status': 'Returning Series',
			'studios': ['HBO'], 'writers': None, 'directors': ['D'], 'year': 2026, 'rating': 8.1, 'votes': 10, 'mpaa': 'TV-MA'},
		'cast': [{'name': 'A', 'role': 'R', 'thumbnail': 't'}], 'art': {'poster': 'p.jpg'}, 'resume_seconds': 600}


def test_a_row_renders_the_same_after_a_json_round_trip():
	built, served = _Recorder(), _Recorder()
	render_episode_row(_row(), lambda: built, _actor)
	render_episode_row(json.loads(json.dumps(_row())), lambda: served, _actor)
	assert built.calls == served.calls
	names = [c[0] for c in built.calls]
	assert names[0] == 'getVideoInfoTag' and 'setResumePoint' in names and names[-4:] == ['setLabel', 'addContextMenuItems', 'setArt', 'setProperties']


def test_no_resume_point_unless_the_row_carries_one():
	row, rec = _row(), _Recorder()
	del row['resume_seconds']
	render_episode_row(row, lambda: rec, _actor)
	assert 'setResumePoint' not in [c[0] for c in rec.calls]


# --- build, store, serve -------------------------------------------------------------------------

@pytest.fixture
def widget(db, prefs, monkeypatch):
	"""Two watched shows through the real build_single_episode('episode.next'), metadata stubbed."""
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 1, '2026-09-10T20:00:00.000Z', 'Alpha')")
	db.execute("INSERT INTO watched VALUES ('episode', '2', 1, 1, '2026-09-11T20:00:00.000Z', 'Beta')")
	metas = dict((str(i), {'tmdb_id': i, 'tvdb_id': 10 + i, 'imdb_id': 'tt%s' % i, 'title': t, 'year': '2026', 'status': 'Returning Series',
		'season_data': [{'season_number': 1, 'poster_path': None}], 'genre': ['Drama'], 'mpaa': 'TV-14', 'plot': 'plot', 'studio': ['S'],
		'poster': 'poster%s' % i, 'fanart': 'fanart', 'duration': 3000, 'country': ['US'], 'cast': [{'name': 'A', 'role': 'R', 'thumbnail': 't'}]})
		for i, t in ((1, 'Alpha'), (2, 'Beta')))
	# Alpha's next episode aired; Beta's airs on the 15th, so it is left out and dates the stored list.
	premiered = {'1': '2026-09-05', '2': '2026-09-15'}
	built_meta = []
	monkeypatch.setattr(episodes, 'tvshow_meta', lambda id_type, media_ids, *a, **k: (built_meta.append(1), metas[str(media_ids['tmdb'] if isinstance(media_ids, dict) else media_ids)])[1])
	monkeypatch.setattr(episodes, 'episodes_meta', lambda season, meta: [{'season': 1, 'episode': 2, 'title': 'Two', 'premiered': premiered[str(meta['tmdb_id'])],
		'episode_type': '', 'episode_id': 100 + meta['tmdb_id'], 'thumb': 'th', 'plot': 'ep plot', 'duration': 2800, 'rating': 7.5, 'votes': 3,
		'writer': ['W'], 'director': ['D'], 'guest_stars': []}])
	monkeypatch.setattr(episodes, 'resolve_assigned_episode_group', lambda tmdb_id: None)
	monkeypatch.setattr(episodes, 'get_datetime', lambda: date(2026, 9, 12))
	monkeypatch.setattr(ws, 'get_hidden_progress_items', lambda indicators: [])
	monkeypatch.setattr(ws, 'group_ordered_episode_pairs', lambda meta: None)
	monkeypatch.setattr(ws, 'group_corrected_next_seed', lambda meta, info, s, e, group_pairs=None: (s, e))
	monkeypatch.setattr(ws, 'get_next', lambda s, e, info, season_data, method, meta, group_pairs=None: (s, e + 1))
	monkeypatch.setattr(settings, 'date_offset', lambda: 0)
	directory = {}
	monkeypatch.setattr(kodi_utils, 'external', lambda: True)
	monkeypatch.setattr(kodi_utils, 'get_icon', lambda name: 'icon')
	monkeypatch.setattr(kodi_utils, 'addon_fanart', lambda: 'fanart')
	monkeypatch.setattr(kodi_utils, 'make_listitem', lambda offscreen=True: _Recorder())
	monkeypatch.setattr(kodi_utils, 'kodi_actor', lambda: _actor)
	monkeypatch.setattr(kodi_utils, 'add_items', lambda handle, items: directory.__setitem__('items', [(u, li.calls, f) for u, li, f in items]))
	monkeypatch.setattr(kodi_utils, 'set_content', lambda handle, content: directory.__setitem__('content', content))
	monkeypatch.setattr(kodi_utils, 'set_category', lambda handle, category: directory.__setitem__('category', category))
	monkeypatch.setattr(kodi_utils, 'set_sort_method', lambda *a, **k: None)
	monkeypatch.setattr(kodi_utils, 'end_directory', lambda handle, cacheToDisc=True: directory.__setitem__('ended', cacheToDisc))
	monkeypatch.setattr(kodi_utils, 'set_view_mode', lambda *a, **k: directory.__setitem__('view', a))
	monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: directory.setdefault('log', []).append(message))
	monkeypatch.setattr(sys, 'argv', ['plugin://plugin.video.redlight/', '1', ''])
	props = {}
	monkeypatch.setattr(kodi_utils, 'get_property', lambda name: props.get(name, ''))
	monkeypatch.setattr(kodi_utils, 'set_property', lambda name, value: props.__setitem__(name, value))
	directory['props'] = props
	return directory, built_meta


def test_a_built_list_is_stored_and_served_identically_without_metadata(widget):
	directory, built_meta = widget
	assert not nlc.serve({})  # nothing stored yet
	episodes.build_single_episode('episode.next', {})
	built = dict(directory)
	assert [u for u, calls, f in built['items']] and len(built['items']) == 1  # Beta's episode is unaired
	assert built_meta
	del built_meta[:]
	directory.clear()
	assert nlc.serve({})
	assert built_meta == []  # no tvshow_meta on a hit
	assert directory['items'] == built['items']
	assert (directory['content'], directory['category'], directory['ended'], directory['view']) == \
		(built['content'], built['category'], built['ended'], built['view'])
	assert 'list cache hit: 1 listed, built 0 s ago' in directory['log'][-1]
	# Nothing time-of-build rides in a stored row: Personal Lists stamps its own date_added.
	assert 'current_time' not in repr(directory['items'])


def test_the_stored_list_stops_being_served_the_day_an_episode_airs(widget, monkeypatch):
	directory, built_meta = widget
	directory['props'][nlc.REVALIDATE_PROP] = nlc.DONE
	episodes.build_single_episode('episode.next', {})
	monkeypatch.setattr(nlc, 'get_datetime', lambda: date(2026, 9, 14))
	assert nlc.serve({})
	monkeypatch.setattr(nlc, 'get_datetime', lambda: date(2026, 9, 15))
	assert not nlc.serve({})
	assert 'an episode has aired since' in directory['log'][-1]


def test_a_mark_after_the_build_is_a_miss_and_forget_drops_both_rows(widget, db):
	directory, built_meta = widget
	directory['props'][nlc.REVALIDATE_PROP] = nlc.DONE
	episodes.build_single_episode('episode.next', {})
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-09-12T21:00:00.000Z', 'Alpha')")
	assert not nlc.serve({})
	episodes.build_single_episode('episode.next', {})
	assert nlc.serve({})
	nlc.forget()
	assert not nlc.serve({})


def test_a_failure_after_committing_still_ends_the_directory(widget, monkeypatch):
	# Past add_items there is no falling back to a build; Kodi's fetch must still get its end.
	directory, built_meta = widget
	episodes.build_single_episode('episode.next', {})
	directory.clear()
	def fail(handle, items): raise RuntimeError('boom')
	monkeypatch.setattr(kodi_utils, 'add_items', fail)
	assert nlc.serve({})
	assert directory['ended'] is False  # end_directory(handle, cacheToDisc=False) ran
	assert any('failed after committing' in line for line in directory['log'])
	assert not any('list cache hit' in line for line in directory['log'])


def test_no_handle_is_a_miss_not_an_error(widget, monkeypatch):
	directory, built_meta = widget
	episodes.build_single_episode('episode.next', {})
	monkeypatch.setattr(sys, 'argv', ['plugin://plugin.video.redlight/'])
	assert not nlc.serve({})
	assert 'no directory handle' in directory['log'][-1]


def test_store_refuses_a_key_that_moved_during_the_build(widget, db):
	key = nlc.cache_key(True)
	db.execute("INSERT INTO watched VALUES ('episode', '3', 1, 1, '2026-09-12T21:00:00.000Z', 'Gamma')")
	nlc.store(key, True, False, [('u', _row())], [], 'Next Episodes')
	assert db.execute("SELECT COUNT(*) FROM maincache").fetchone()[0] == 0


# --- stale first, then the service's checks -------------------------------------------------------

def _set_age(db, name, seconds):
	data, = db.execute("SELECT data FROM maincache WHERE id = ?", (wc.LIST_PREFIX + name,)).fetchone()
	stored = json.loads(data)
	stored['data']['built_at'] = int(__import__('time').time()) - seconds
	db.execute("UPDATE maincache SET data = ? WHERE id = ?", (json.dumps(stored), wc.LIST_PREFIX + name))


def _served(props, name='next_episodes_widget'):
	return json.loads(props[nlc.SERVED_PROP % name])


def test_a_changed_state_is_shown_stale_once_then_rebuilt(widget, db):
	directory, built_meta = widget
	props = directory['props']
	episodes.build_single_episode('episode.next', {})
	built_items = directory['items']
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-09-12T21:00:00.000Z', 'Alpha')")
	del built_meta[:]
	directory.clear(); directory['props'] = props
	props.clear()  # the next start: Kodi clears Home properties
	assert nlc.serve({})  # the earlier run's list, at once
	assert built_meta == [] and directory['items'] == built_items
	assert 'list cache stale (state changed since it was stored): 1 listed' in directory['log'][-1]
	assert _served(props)['key'] == nlc.STALE_KEY  # so the service's check always rebuilds it
	props[nlc.REVALIDATE_PROP] = nlc.REBUILD  # what NextEpisodesRevalidate sets
	assert not nlc.serve({})
	assert props[nlc.REVALIDATE_PROP] == nlc.DONE  # cleared before the build, not after it
	assert 'rebuild asked for by the service' in directory['log'][-1]
	assert not nlc.serve({})  # no second stale list this session
	assert 'state changed since it was stored' in directory['log'][-1]


def test_a_build_records_the_key_the_service_recomputes(widget):
	directory, built_meta = widget
	episodes.build_single_episode('episode.next', {})
	assert nlc.served_lists() == {'next_episodes_widget': {'key': nlc.cache_key(True, False), 'external': True, 'anime': False}}


def test_a_widget_refresh_makes_a_stored_list_due(widget):
	directory, built_meta = widget
	props = directory['props']
	episodes.build_single_episode('episode.next', {})
	assert nlc.serve({})
	props[nlc.REFRESHED_PROP] = str(int(__import__('time').time()) + 1)  # kodi_utils.refresh_widgets()
	assert not nlc.serve({})  # not even stale: the service's window is still open here
	assert nlc.REVALIDATE_PROP not in props and 'widgets refreshed since' in directory['log'][-1]
	episodes.build_single_episode('episode.next', {})
	props[nlc.REFRESHED_PROP] = '0'
	assert nlc.serve({})


def test_the_anime_list_has_its_own_row_and_forget_drops_all_four(db, prefs):
	names = set(nlc.list_name(e, a) for e, a in nlc.ALL_LISTS)
	assert len(names) == 4
	for is_external, anime in nlc.ALL_LISTS:
		nlc.store(nlc.cache_key(is_external, anime), is_external, anime, [('u', _row())], [], 'Next Episodes')
	assert db.execute("SELECT COUNT(*) FROM maincache").fetchone()[0] == 4
	nlc.forget()
	assert db.execute("SELECT COUNT(*) FROM maincache").fetchone()[0] == 0


def test_an_unreadable_watched_table_means_no_key(db, prefs, monkeypatch):
	logged = []
	monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: logged.append(message))
	monkeypatch.setattr(ws, 'watched_table_fingerprint', lambda dbcon: 'unknown-1757700000')
	assert nlc.cache_key(True) is None
	assert 'watched table unreadable' in logged[-1]


def test_stale_is_only_for_the_start_of_a_session(widget, db):
	# G1 on #162: once this session built or hit the list, a mark rebuilds instead of showing the old list.
	directory, built_meta = widget
	episodes.build_single_episode('episode.next', {})
	assert nlc.serve({})
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-09-12T21:00:00.000Z', 'Alpha')")
	assert not nlc.serve({})
	assert nlc.REVALIDATE_PROP not in directory['props'] and 'state changed since it was stored' in directory['log'][-1]


def test_a_local_provider_keeps_its_saved_list_through_a_build_without_a_key(widget, db):
	# H1 on #162: an activity sync cleared MDBList's dropped row and the refetch failed (network not up).
	directory, built_meta = widget
	episodes.build_single_episode('episode.next', {})
	db.execute("DELETE FROM mdblist_data WHERE id = 'mdblist_hidden_items_dropped'")
	nlc.store(nlc.cache_key(True), True, False, [('u', _row())], [], 'Next Episodes')
	assert db.execute("SELECT COUNT(*) FROM maincache WHERE id LIKE 'WIDGET_LIST_%'").fetchone()[0] == 1
	directory['props'].clear()  # the next start
	assert nlc.serve({}) and 'list cache stale (inputs not held locally)' in directory['log'][-1]


def test_a_provider_without_a_local_key_gets_no_stale_list_and_its_build_drops_the_row(widget, db, prefs):
	# G2 on #162: a leftover list from MDBList must not come back at every start after a switch to Trakt.
	directory, built_meta = widget
	episodes.build_single_episode('episode.next', {})
	directory['props'].clear()  # the next start
	prefs['watched_indicators'] = 1
	assert not nlc.serve({})
	assert 'inputs not held locally' in directory['log'][-1]
	nlc.store(None, True, False, [('u', _row())], [], 'Next Episodes')
	assert db.execute("SELECT COUNT(*) FROM maincache WHERE id LIKE 'WIDGET_LIST_%'").fetchone()[0] == 0


def test_refresh_widgets_stamps_the_property_the_cache_reads():
	assert "'%s'" % nlc.REFRESHED_PROP in inspect.getsource(kodi_utils.refresh_widgets)


def test_an_in_addon_listing_is_never_answered_stale(widget, db, monkeypatch):
	directory, built_meta = widget
	monkeypatch.setattr(kodi_utils, 'external', lambda: False)
	episodes.build_single_episode('episode.next', {})
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-09-12T21:00:00.000Z', 'Alpha')")
	assert not nlc.serve({})
	assert 'state changed since it was stored' in directory['log'][-1]


def test_a_hit_needs_list_ttl_and_a_stale_list_needs_stale_max(widget, db):
	directory, built_meta = widget
	episodes.build_single_episode('episode.next', {})
	directory['props'].clear()  # the next start
	_set_age(db, 'next_episodes_widget', nlc.LIST_TTL + 60)
	assert nlc.serve({})
	assert 'list cache stale (older than 12.0 h)' in directory['log'][-1] and 'built 12.0 h ago' in directory['log'][-1]
	_set_age(db, 'next_episodes_widget', nlc.STALE_MAX + 60)
	assert not nlc.serve({})
	assert 'older than 12.0 h' in directory['log'][-1]


def test_a_failed_property_read_means_no_stale_list(widget, db, monkeypatch):
	directory, built_meta = widget
	episodes.build_single_episode('episode.next', {})
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-09-12T21:00:00.000Z', 'Alpha')")
	def broken(name): raise RuntimeError('no window')
	monkeypatch.setattr(kodi_utils, 'get_property', broken)
	assert nlc.revalidate_state() == nlc.DONE
	assert not nlc.serve({})


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


@pytest.fixture
def revalidator(monkeypatch):
	"""NextEpisodesRevalidate on a fake clock. state['key'] is the current key; serving is simulated
	by writing SERVED_PROP, and a refresh by on_refresh (default: nothing answers it)."""
	import time as time_module
	import service
	clock, props, refreshed = _Clock(), {}, []
	state = {'key': 'k1', 'playing': False, 'asked': [], 'on_refresh': lambda: None}
	monkeypatch.setattr(time_module, 'time', clock.time)
	monkeypatch.setattr(kodi_utils, 'get_property', lambda name: props.get(name, ''))
	monkeypatch.setattr(kodi_utils, 'set_property', lambda name, value: props.__setitem__(name, value))
	monkeypatch.setattr(kodi_utils, 'kodi_refresh', lambda: (refreshed.append(clock.now), state['on_refresh']()))
	monkeypatch.setattr(kodi_utils, 'service_shutting_down', lambda monitor=None: False)
	monkeypatch.setattr(kodi_utils, 'kodi_player', lambda: type('P', (), {'isPlayingVideo': lambda self: state['playing']})())
	monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: state.setdefault('log', []).append(message))
	def current_key(is_external, anime=False):
		state['asked'].append((is_external, anime))
		if state.get('keys'): state['key'] = state['keys'].pop(0)  # one key per check, then the last one stays
		return state['key']
	monkeypatch.setattr(nlc, 'cache_key', current_key)
	return service.NextEpisodesRevalidate(), clock, props, refreshed, state


def _serve_as(props, key, external=True, anime=False):
	props[nlc.SERVED_PROP % nlc.list_name(external, anime)] = json.dumps({'key': key, 'external': external, 'anime': anime})


def test_the_service_asks_for_one_rebuild_after_a_stale_start(revalidator):
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, nlc.STALE_KEY)
	def answer(): props[nlc.REVALIDATE_PROP] = nlc.DONE; _serve_as(props, state['key'])
	state['on_refresh'] = answer
	job.run(_Monitor(clock))
	assert props[nlc.REVALIDATE_PROP] == nlc.DONE and len(refreshed) == 1
	assert refreshed[0] - 1000.0 >= nlc.REVALIDATE_AFTER


def test_a_rebuild_nobody_answers_ends_the_checks(revalidator):
	# G3 on #162: the home window was not showing, so the refresh never reached the widget.
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, nlc.STALE_KEY)
	monitor = _Monitor(clock)
	job.run(monitor)
	assert len(refreshed) == 1 and props[nlc.REVALIDATE_PROP] == nlc.DONE
	assert monitor.calls < monitor.limit and nlc.REBUILD_WAIT <= clock.now - refreshed[0] <= nlc.REBUILD_WAIT + 5
	assert 'not answered' in state['log'][-1]


def test_a_rebuild_asked_at_the_last_checkpoint_is_waited_for(revalidator):
	# H2 on #162: the window used to close, and force DONE, before the widget could answer.
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, 'k1')
	state['keys'] = ['k1'] * (len(nlc.CHECKPOINTS) - 1) + ['k2']
	answered = []
	def answer(): answered.append(clock.now)
	state['on_refresh'] = answer
	# The widget answers 10 s after the refresh: serve() flips REBUILD to DONE and records k2.
	original_wait = _Monitor.waitForAbort
	class _Answering(_Monitor):
		def waitForAbort(self, seconds):
			stop = original_wait(self, seconds)
			if answered and props.get(nlc.REVALIDATE_PROP) == nlc.REBUILD and self.clock.now - answered[0] >= 10:
				props[nlc.REVALIDATE_PROP] = nlc.DONE
				_serve_as(props, 'k2')
			return stop
	job.run(_Answering(clock))
	assert len(refreshed) == 1 and props[nlc.REVALIDATE_PROP] == nlc.DONE
	assert not any('not answered' in line for line in state.get('log', []))


def test_an_in_addon_listing_is_not_revalidated(revalidator):
	# G4 on #162: a widget refresh cannot re-render it, so it must not spend the rebuilds.
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, 'k0', external=False)
	job.run(_Monitor(clock))
	assert refreshed == [] and state['asked'] == []


def test_a_list_that_matches_needs_no_refresh_and_the_checks_end(revalidator):
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, 'k1')
	monitor = _Monitor(clock)
	job.run(monitor)
	assert refreshed == [] and props[nlc.REVALIDATE_PROP] == nlc.DONE
	assert monitor.calls < monitor.limit  # returned after the last checkpoint


def test_another_widgets_sync_after_the_answer_is_caught(revalidator):
	# F1 on #162, in one run: the 15 s check matches, then In Progress's MDBList sync rewrites the
	# watched table, and the 45 s check asks for the rebuild.
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, 'k1')
	state['keys'] = ['k1', 'k2']
	def answer(): props[nlc.REVALIDATE_PROP] = nlc.DONE; _serve_as(props, state['key'])
	state['on_refresh'] = answer
	job.run(_Monitor(clock))
	assert len(refreshed) == 1 and _served(props)['key'] == 'k2'
	assert refreshed[0] - 1000.0 >= 45


def test_a_key_that_keeps_moving_stops_at_max_rebuilds(revalidator):
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, 'k0')
	def answer():
		props[nlc.REVALIDATE_PROP] = nlc.DONE
		_serve_as(props, state['key'])
		state['key'] += '+'  # the refresh made In Progress sync again
	state['on_refresh'] = answer
	job.run(_Monitor(clock))
	assert len(refreshed) == nlc.MAX_REBUILDS
	assert 'still behind after 2 rebuilds' in state['log'][-1]


def test_the_service_checks_each_list_with_its_own_flags(revalidator):
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, 'k1', external=True, anime=True)
	job.run(_Monitor(clock))
	assert set(state['asked']) == {(True, True)}


def test_the_service_waits_for_playback_to_end_before_the_rebuild(revalidator):
	job, clock, props, refreshed, state = revalidator
	_serve_as(props, nlc.STALE_KEY)
	state['playing'] = True
	job.run(_Monitor(clock, limit=20))
	assert refreshed == [] and nlc.REVALIDATE_PROP not in props


def test_nothing_answered_ends_the_checks_without_a_refresh(revalidator):
	job, clock, props, refreshed, state = revalidator
	job.run(_Monitor(clock))
	assert props[nlc.REVALIDATE_PROP] == nlc.DONE and refreshed == []
	assert clock.now - 1000.0 > nlc.FIRST_ANSWER_WAIT
