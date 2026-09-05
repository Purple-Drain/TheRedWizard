# -*- coding: utf-8 -*-
"""Home widget caches (#120 In Progress, #121 Next Episodes).

Pure logic only: cache key derivation, filter-before-fetch in active_tvshows_information(),
hit/miss on get_in_progress_tvshows(), and the invalidation hooks. Everything talks to an
in-memory sqlite standing in for maincache_db, so no Kodi profile is touched.
"""
import sqlite3

import pytest

import caches.widget_cache as wc
import caches.episode_groups_cache as egc
import caches.meta_cache as mc
import modules.watched_status as ws
import modules.kodi_utils as kodi_utils
from modules import metadata, settings


@pytest.fixture
def db(monkeypatch):
	"""One shared in-memory maincache; every module-level connect_database() hands it back."""
	conn = sqlite3.connect(':memory:', check_same_thread=False)
	conn.isolation_level = None
	conn.execute('CREATE TABLE IF NOT EXISTS maincache (id text unique, data text, expires integer)')
	conn.execute('CREATE TABLE IF NOT EXISTS groups_data (tmdb_id text not null unique, data text)')
	conn.execute('CREATE TABLE IF NOT EXISTS metadata (db_type text not null, tmdb_id text not null, imdb_id text, tvdb_id text, meta text, expires integer, unique (db_type, tmdb_id))')
	conn.execute('CREATE TABLE IF NOT EXISTS season_metadata (tmdb_id text not null unique, meta text, expires integer)')
	conn.execute('CREATE TABLE IF NOT EXISTS function_cache (string_id text not null unique, data text, expires integer)')
	conn.execute('CREATE TABLE IF NOT EXISTS watched (db_type text not null, media_id text not null, season integer, episode integer, last_played text, title text, unique (db_type, media_id, season, episode))')

	class _NoClose:
		def __init__(self, c): self._c = c
		def __getattr__(self, name): return getattr(self._c, name)
		def close(self): pass
		def __enter__(self): return self
		def __exit__(self, *a): return False

	shared = _NoClose(conn)
	monkeypatch.setattr(wc, 'connect_database', lambda name: shared)
	monkeypatch.setattr(egc, 'connect_database', lambda name: shared)
	monkeypatch.setattr(mc, 'open_db', lambda name: shared)
	return conn


@pytest.fixture
def logged(monkeypatch):
	lines = []
	monkeypatch.setattr(ws, 'logger', lambda heading, message: lines.append(message))
	return lines


def _ids(conn):
	return sorted(r[0] for r in conn.execute('SELECT id FROM maincache').fetchall())


# --- widget_cache rows ------------------------------------------------------------------------

def test_show_facts_roundtrip_filter_and_expiry(db, monkeypatch):
	wc.widget_cache.set_show_facts({'1': {'aired_eps': 10, 'status': 'Ended'}, '2': {'aired_eps': 5, 'status': 'Returning Series'}})
	assert wc.widget_cache.get_show_facts(['1', 2]) == {'1': {'aired_eps': 10, 'status': 'Ended'}, '2': {'aired_eps': 5, 'status': 'Returning Series'}}
	assert wc.widget_cache.get_show_facts(['2']) == {'2': {'aired_eps': 5, 'status': 'Returning Series'}}
	assert wc.widget_cache.get_show_facts(['3']) == {}
	monkeypatch.setattr(wc, '_now', lambda: wc.AIRING_TTL + 10 ** 10)
	assert wc.widget_cache.get_show_facts() == {}


def test_ended_shows_keep_facts_longer_than_airing_ones(db):
	wc.widget_cache.set_show_facts({'1': {'aired_eps': 10, 'status': 'Ended'}, '2': {'aired_eps': 5, 'status': 'Returning Series'}})
	expires = dict(db.execute('SELECT id, expires FROM maincache').fetchall())
	assert expires[wc.FACTS_PREFIX + '1'] - expires[wc.FACTS_PREFIX + '2'] == wc.ENDED_TTL - wc.AIRING_TTL


def test_list_hit_only_on_same_key_and_before_expiry(db, monkeypatch):
	data = [{'media_id': '1', 'title': 'A', 'last_played': 'x'}]
	assert wc.widget_cache.get_list('in_progress_tvshows', 'k1') is None
	wc.widget_cache.set_list('in_progress_tvshows', 'k1', data)
	assert wc.widget_cache.get_list('in_progress_tvshows', 'k1') == data
	assert wc.widget_cache.get_list('in_progress_tvshows', 'k2') is None
	monkeypatch.setattr(wc, '_now', lambda: wc.LIST_TTL + 10 ** 10)
	assert wc.widget_cache.get_list('in_progress_tvshows', 'k1') is None


def test_next_episode_hit_only_on_same_key(db):
	assert wc.widget_cache.get_next_episode(7, 'k') is None
	wc.widget_cache.set_next_episode(7, 'k', 2, 5, 'Ended')
	assert wc.widget_cache.get_next_episode('7', 'k') == (2, 5)
	assert wc.widget_cache.get_next_episode(7, 'other') is None


def test_next_episode_none_result_is_cached_as_negative_not_a_miss(db):
	# A show with no next episode (#121 follow-up): stored under the same key as a positive
	# result would be, distinguishable from a miss so callers don't recompute every rebuild.
	assert wc.widget_cache.get_next_episode(7, 'k') is None
	wc.widget_cache.set_next_episode(7, 'k', None, None, 'Ended')
	assert wc.widget_cache.get_next_episode(7, 'k') is wc.NEGATIVE
	assert wc.widget_cache.get_next_episode(7, 'other') is None  # different key: still a miss
	assert _ids(db) == [wc.NEXTEP_PREFIX + '7']


def test_negative_next_episode_uses_a_short_ttl_regardless_of_show_status(db):
	wc.widget_cache.set_next_episode(1, 'k', None, None, 'Ended')       # would be ENDED_TTL if positive
	wc.widget_cache.set_next_episode(2, 'k', None, None, 'Returning Series')
	expires = dict(db.execute('SELECT id, expires FROM maincache').fetchall())
	now = wc._now()
	for tmdb_id in (1, 2):
		ttl = expires[wc.NEXTEP_PREFIX + str(tmdb_id)] - now
		assert 0 < ttl <= wc.NEGATIVE_NEXTEP_TTL
	assert wc.NEGATIVE_NEXTEP_TTL < wc.AIRING_TTL < wc.ENDED_TTL


def test_positive_result_replaces_a_cached_negative_under_the_same_key(db):
	wc.widget_cache.set_next_episode(7, 'k', None, None)
	assert wc.widget_cache.get_next_episode(7, 'k') is wc.NEGATIVE
	wc.widget_cache.set_next_episode(7, 'k', 2, 5, 'Ended')
	assert wc.widget_cache.get_next_episode(7, 'k') == (2, 5)


def test_negative_next_episode_expires_back_to_a_miss(db, monkeypatch):
	wc.widget_cache.set_next_episode(7, 'k', None, None)
	monkeypatch.setattr(wc, '_now', lambda: wc.NEGATIVE_NEXTEP_TTL + 10 ** 10)
	assert wc.widget_cache.get_next_episode(7, 'k') is None


def test_delete_show_drops_its_rows_and_every_list_but_nothing_else(db):
	db.execute("INSERT INTO maincache VALUES ('unrelated', '1', 9999999999)")
	wc.widget_cache.set_show_facts({'1': {'aired_eps': 1, 'status': ''}, '2': {'aired_eps': 1, 'status': ''}})
	wc.widget_cache.set_next_episode(1, 'k', 1, 2)
	wc.widget_cache.set_next_episode(2, 'k', 1, 2)
	wc.widget_cache.set_list('in_progress_tvshows', 'k', [])
	wc.widget_cache.delete_show(1)
	assert _ids(db) == sorted([wc.FACTS_PREFIX + '2', wc.NEXTEP_PREFIX + '2', 'unrelated'])
	assert wc.widget_cache.clear()
	assert _ids(db) == ['unrelated']


# --- key derivation ---------------------------------------------------------------------------

def test_next_episode_key_ignores_row_order_but_sees_any_row_change():
	rows = [(1, 1), (1, 2), (2, 1)]
	base = ws.next_episode_cache_key(rows, 0, 2, 1)
	assert ws.next_episode_cache_key(list(reversed(rows)), 0, 2, 1) == base
	assert ws.next_episode_cache_key([('1', '1'), ('1', '2'), ('2', '1')], 0, 2, 1) == base
	assert ws.next_episode_cache_key(rows[:-1] + [(1, 3)], 0, 2, 1) != base  # a middle row changed, seed did not
	assert ws.next_episode_cache_key(rows, 1, 2, 1) != base  # different method
	assert ws.next_episode_cache_key(rows, 0, 2, 2) != base  # different seed


def test_watched_table_fingerprint_moves_on_mark_and_unmark(db):
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 1, '2026-01-01 10:00:00', 'A')")
	db.execute("INSERT INTO watched VALUES ('episode', '1', 1, 2, '2026-01-01 11:00:00', 'A')")
	before = ws.watched_table_fingerprint(db)
	db.execute("INSERT INTO watched VALUES ('episode', '2', 1, 1, '2026-01-01 11:00:00', 'B')")
	marked = ws.watched_table_fingerprint(db)
	assert marked != before
	db.execute("DELETE FROM watched WHERE media_id = '1' AND episode = 2")
	assert ws.watched_table_fingerprint(db) not in (before, marked)


def test_in_progress_key_tracks_hidden_items_settings_and_fingerprint(monkeypatch):
	monkeypatch.setattr(settings, 'watched_indicators', lambda: 3)
	monkeypatch.setattr(settings, 'exclude_specials_from_progress', lambda: True)
	monkeypatch.setattr(settings, 'tv_progress_location', lambda: 0)
	monkeypatch.setattr(ws, 'get_datetime', lambda: '2026-09-05')
	monkeypatch.setattr(ws, 'watched_table_fingerprint', lambda watched_db=None: 'fp1')
	base = ws.in_progress_cache_key([5, 3])
	assert ws.in_progress_cache_key([3, 5]) == base
	assert ws.in_progress_cache_key(['3', '5']) == base
	assert ws.in_progress_cache_key([3]) != base
	monkeypatch.setattr(ws, 'watched_table_fingerprint', lambda watched_db=None: 'fp2')
	assert ws.in_progress_cache_key([5, 3]) != base
	monkeypatch.setattr(ws, 'watched_table_fingerprint', lambda watched_db=None: 'fp1')
	monkeypatch.setattr(settings, 'tv_progress_location', lambda: 1)
	assert ws.in_progress_cache_key([5, 3]) != base


# --- classification and filter-before-fetch -----------------------------------------------------

def test_classify_tvshow_matches_the_original_rules():
	row = lambda played: {'total_played': played}
	# progress: unfinished shows always; finished-but-airing only when include_other
	assert ws.classify_tvshow('progress', row(3), 10, 'Returning Series', False)
	assert not ws.classify_tvshow('progress', row(10), 10, 'Returning Series', False)
	assert ws.classify_tvshow('progress', row(10), 10, 'Returning Series', True)
	assert not ws.classify_tvshow('progress', row(10), 10, 'Ended', True)
	# watched: finished shows; airing ones only when include_other
	assert ws.classify_tvshow('watched', row(10), 10, 'Ended', False)
	assert not ws.classify_tvshow('watched', row(10), 10, 'Returning Series', False)
	assert ws.classify_tvshow('watched', row(10), 10, 'Returning Series', True)
	assert not ws.classify_tvshow('watched', row(3), 10, 'Ended', True)


@pytest.fixture
def three_shows(db, monkeypatch):
	watched = {
		'1': {'media_id': '1', 'title': 'Done', 'last_played': '3', 'total_played': 10},
		'2': {'media_id': '2', 'title': 'Halfway', 'last_played': '2', 'total_played': 5},
		'3': {'media_id': '3', 'title': 'Fresh', 'last_played': '1', 'total_played': 1},
	}
	metas = {'1': {'tmdb_id': 1, 'status': 'Ended'}, '2': {'tmdb_id': 2, 'status': 'Ended'}, '3': {'tmdb_id': 3, 'status': 'Returning Series'}}
	aired = {'1': 10, '2': 10, '3': 8}
	fetched = []
	monkeypatch.setattr(ws, 'watched_info_tvshow', lambda watched_db=None: dict((k, dict(v)) for k, v in watched.items()))
	monkeypatch.setattr(ws, 'get_hidden_progress_items', lambda indicators: [])
	monkeypatch.setattr(metadata, 'tvshow_meta', lambda id_type, media_id, *a, **k: (fetched.append(str(media_id)), metas[str(media_id)])[1])
	monkeypatch.setattr(ws, 'progress_aired_eps', lambda meta: aired[str(meta['tmdb_id'])])
	monkeypatch.setattr(settings, 'watched_indicators', lambda: 0)
	monkeypatch.setattr(settings, 'tmdb_api_key', lambda: 'k')
	monkeypatch.setattr(settings, 'mpaa_region', lambda: 'US')
	monkeypatch.setattr(settings, 'tv_progress_location', lambda: 0)
	monkeypatch.setattr(settings, 'max_threads', lambda: 4)
	return fetched


def test_cold_run_fetches_every_show_and_writes_facts(three_shows):
	stats = {}
	results = ws.active_tvshows_information('progress', stats=stats)
	assert sorted(i['media_id'] for i in results) == ['2', '3']
	assert sorted(three_shows) == ['1', '2', '3']
	assert stats == {'scanned': 3, 'meta_fetched': 3}
	assert wc.widget_cache.get_show_facts() == {
		'1': {'aired_eps': 10, 'status': 'Ended'}, '2': {'aired_eps': 10, 'status': 'Ended'}, '3': {'aired_eps': 8, 'status': 'Returning Series'}}


def test_warm_run_decides_from_facts_and_fetches_only_the_unknown_show(three_shows):
	wc.widget_cache.set_show_facts({'1': {'aired_eps': 10, 'status': 'Ended'}, '2': {'aired_eps': 10, 'status': 'Ended'}})
	stats = {}
	results = ws.active_tvshows_information('progress', stats=stats)
	assert sorted(i['media_id'] for i in results) == ['2', '3']
	assert three_shows == ['3']
	assert stats == {'scanned': 3, 'meta_fetched': 1}
	assert '3' in wc.widget_cache.get_show_facts()


def test_watched_list_uses_the_same_facts(three_shows):
	assert [i['media_id'] for i in ws.active_tvshows_information('watched')] == ['1']
	assert [i['media_id'] for i in ws.active_tvshows_information('watched')] == ['1']
	assert three_shows == ['1', '2', '3']  # second pass fetched nothing


def test_in_progress_widget_hits_cache_until_the_key_moves(three_shows, logged, monkeypatch):
	monkeypatch.setattr(ws, 'in_progress_cache_key', lambda hidden, watched_db=None: 'key-a')
	monkeypatch.setattr(settings, 'lists_sort_order', lambda kind: 1)
	first = ws.get_in_progress_tvshows(None, 1)
	assert [i['media_id'] for i in first] == ['2', '3']
	assert 'cache miss' in logged[-1] and '3 shows scanned, 2 with progress' in logged[-1] and '3 meta fetched' in logged[-1]
	del three_shows[:]
	second = ws.get_in_progress_tvshows(None, 1)
	assert second == first
	assert three_shows == []
	assert 'cache hit' in logged[-1] and '2 with progress' in logged[-1]
	monkeypatch.setattr(ws, 'in_progress_cache_key', lambda hidden, watched_db=None: 'key-b')
	ws.get_in_progress_tvshows(None, 1)
	assert 'cache miss' in logged[-1] and '0 meta fetched' in logged[-1]  # facts survived, only the list was rebuilt


def test_show_facts_helper_reads_bulk_map_then_computes_and_writes_through(db, monkeypatch):
	monkeypatch.setattr(ws, 'progress_aired_eps', lambda meta: 42)
	meta = {'tmdb_id': 9, 'status': 'Ended'}
	assert ws.show_facts(meta, {'9': {'aired_eps': 7, 'status': 'Ended'}}) == (7, 'Ended')
	assert ws.show_facts(meta, {}) == (42, 'Ended')
	assert wc.widget_cache.get_show_facts(['9']) == {'9': {'aired_eps': 42, 'status': 'Ended'}}


# --- invalidation hooks -------------------------------------------------------------------------

def _seed(show):
	wc.widget_cache.set_show_facts({str(show): {'aired_eps': 1, 'status': 'Ended'}})
	wc.widget_cache.set_next_episode(show, 'k', 1, 1, 'Ended')
	wc.widget_cache.set_list('in_progress_tvshows', 'k', [])


def test_group_assignment_change_forgets_that_show(db):
	_seed(1); _seed(2)
	egc.episode_groups_cache.set(1, {'id': 'g'})
	assert _ids(db) == sorted([wc.FACTS_PREFIX + '2', wc.NEXTEP_PREFIX + '2'])
	egc.episode_groups_cache.delete(2)
	assert _ids(db) == []
	_seed(3)
	egc.episode_groups_cache.clear_cache()
	assert _ids(db) == []


def test_meta_refresh_and_meta_clear_forget_widget_rows(db):
	_seed(1); _seed(2)
	mc.meta_cache.delete('tvshow', 'tmdb_id', '1')
	assert _ids(db) == sorted([wc.FACTS_PREFIX + '2', wc.NEXTEP_PREFIX + '2'])
	mc.meta_cache.delete('movie', 'tmdb_id', '2')
	assert len(_ids(db)) == 2  # movies do not touch the widget rows
	mc.meta_cache.delete_all()
	assert _ids(db) == []


def test_negative_sentinel_is_reachable_through_the_instance():
    """Regression for pd.41: episodes.py compares against widget_cache.NEGATIVE on the instance;
    on 05.09.26 that raised AttributeError for every show and Next Episodes listed nothing."""
    from caches import widget_cache as module
    assert module.widget_cache.NEGATIVE is module.NEGATIVE
