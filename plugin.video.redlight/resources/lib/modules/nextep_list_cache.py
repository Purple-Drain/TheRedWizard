# -*- coding: utf-8 -*-
"""The finished Next Episodes list, served before the indexer loads (#155).

At a cold boot the Next Episodes widget spent about 5.3 s importing its own code before it built
anything (4.4 s of that was `requests`, which every provider API module imports), then called
MDBList's sync/last_activities over the network, then rebuilt every watched show. The router asks
serve() first, and it answers in one of three ways:

* hit: the stored list was built from exactly the current state, after the last widget refresh
  (the widget_refresh_timer setting or a manual Refresh Widgets, so those still rebuild as they
  always did), within LIST_TTL, and none of its shows has aired a next episode since. Served as is.
* stale: the home widget asked, nothing fresh is stored, but a list from an earlier run is (younger
  than STALE_MAX). Shown at once, so the row is never empty at start, and replaced within seconds
  by the rebuild the service asks for (below). An in-addon listing is never answered stale.
* miss: the caller builds the normal way, and the build stores a fresh list.

The key is read from local state only: the active provider's watched and progress tables, the
dropped shows and watchlist as the provider's cache holds them, favourites, and every setting the
rows read. Working it out imports no provider module and makes no network call. Anything only the
provider knows (its cache row cleared by an activity sync, a provider with no local reader here, or
a table that could not be read) means no key, so no hit.

Rows are the JSON-safe dicts modules.episode_rows renders, so a served ListItem is made by the same
calls as a built one.

Keeping what is on screen honest. Every answer, hit, stale or built, records in a Home-window
property the key it was built for (record_served). The service's NextEpisodesRevalidate compares that
with the current key at CHECKPOINTS seconds after the first answer and, on a mismatch, asks for one
real build and refreshes the widgets. That covers a stale list, and a hit or build overtaken by
another widget's provider sync (In Progress syncs MDBList on its own cold build, which rewrites the
watched table after Next Episodes has already answered). MAX_REBUILDS bounds it, because refreshing
the widgets makes In Progress sync again. Past the last checkpoint, freshness is MDBListMonitor's
periodic sync and WidgetRefresher's timer, as before this module existed.

Session state lives in Home-window properties, which every Kodi process sees and Kodi clears on
restart:
* SERVED_PROP % list name: {"key", "external", "anime"} of the last answer for that list.
* REVALIDATE_PROP: '' (stale allowed), REBUILD (the service asks for a real build) or DONE (no more
  stale this session). serve() turns REBUILD into DONE the moment it sees it, before building, so a
  build that fails or finds no key cannot leave every later request forced into a build.
A read that fails counts as DONE: no stale list and no forced build.
"""
import sys
import json
import time
import hashlib
from caches.base_cache import connect_database
from caches.widget_cache import widget_cache
from modules import kodi_utils, settings
from modules.episode_rows import render_episode_row
from modules.utils import get_datetime

LIST_TTL = 12 * 3600
# How old a stored list may be and still be shown stale while a rebuild runs. It is also the row's
# own expiry in maincache, so forget(), delete_show() and Clear Main Cache still remove it.
STALE_MAX = 7 * 24 * 3600
# The service's checks, in seconds after the first answer of the session (and never before
# REVALIDATE_AFTER seconds after the service started). The last one is past an In Progress cold
# build (up to about 15 s) plus its MDBList sync. FIRST_ANSWER_WAIT covers a TV that is still off.
REVALIDATE_AFTER = 15
CHECKPOINTS = (15, 45, 90, 150, 240)
MAX_REBUILDS = 2
FIRST_ANSWER_WAIT = 30 * 60
# A rebuild request nobody answers (the home window was not showing) ends the checks after this.
# Nothing is lost: the list on record is behind, so its next request misses and builds anyway.
REBUILD_WAIT = 60

SERVED_PROP = 'redlight.nextep_served.%s'
REVALIDATE_PROP = 'redlight.nextep_revalidate'
REBUILD, DONE = 'rebuild', 'done'
# Set by kodi_utils.refresh_widgets(): a list built before it is due for a rebuild.
REFRESHED_PROP = 'redlight.widgets_refreshed_at'
# What a stale answer records as its key: never equal to a current key, so the service always
# rebuilds it (a list stale only by age has the same key as the current state).
STALE_KEY = 'stale'

# Watched status providers whose Next Episodes inputs can all be read locally: Red Light's own
# table (0) and MDBList (3). Trakt, Simkl and PunchPlay keep building the normal way.
LOCAL_PROVIDERS = (0, 3)

# The mdblist_data rows mdblist_get_dropped_items() and _mdbl_watchlist_raw() cache into. Spelled out
# rather than imported: apis.mdblist_api imports requests, which is the cost this module avoids.
# tests/test_nextep_list_cache.py checks both against the API module.
MDBLIST_DROPPED_ROW = 'mdblist_hidden_items_dropped'
MDBLIST_WATCHLIST_ROW = 'mdblist_watchlist_live'

# Every no-argument setting the episode.next rows, their filtering or their order read, directly or
# through settings.append_*_context_menus(). tests/test_nextep_list_cache.py checks this against
# build_single_episode(), so a new setting there cannot silently leave the cache key stale.
SETTING_GETTERS = (
	'watched_indicators', 'nextep_include_unwatched', 'nextep_include_unaired', 'nextep_method',
	'nextep_limit_history', 'nextep_limit', 'nextep_sort_key', 'nextep_sort_direction', 'nextep_include_airdate',
	'nextep_airing_today', 'single_ep_unwatched_episodes', 'single_ep_unwatched_in_title', 'widget_hide_watched',
	'avoid_episode_spoilers', 'date_offset', 'tmdb_api_key', 'mpaa_region', 'include_anime_tvshow',
	'exclude_specials_from_progress', 'tv_progress_location', 'cm_sort_order', 'cm_default_order', 'ignore_articles',
	'playback_key', 'trakt_user_active', 'simkl_user_active', 'punchplay_user_active', 'mdblist_user_active',
	'tmdblist_user_active', 'configured_external_scraper_slots')


def list_name(is_external, anime=False):
	# One row per entry point and per list: a widget and an in-addon listing render different context
	# menus, and Next Episodes and Anime Next Episodes are different lists. Sharing a row would have
	# each evict the other on every build.
	return '%s%s' % ('next_episodes_widget' if is_external else 'next_episodes', '_anime' if anime else '')


ALL_LISTS = tuple((is_external, anime) for is_external in (True, False) for anime in (False, True))


def revalidate_state():
	try: return kodi_utils.get_property(REVALIDATE_PROP) or ''
	except Exception: return DONE


def record_served(is_external, anime, key):
	try: kodi_utils.set_property(SERVED_PROP % list_name(is_external, anime), json.dumps({'key': key, 'external': bool(is_external), 'anime': bool(anime)}))
	except Exception: pass


def served_lists():
	"""{list name: {"key", "external", "anime"}} for every list answered this session."""
	served = {}
	for is_external, anime in ALL_LISTS:
		name = list_name(is_external, anime)
		try:
			raw = kodi_utils.get_property(SERVED_PROP % name)
			if raw: served[name] = json.loads(raw)
		except Exception: pass
	return served


def _digest(value):
	return hashlib.sha1(repr(value).encode('utf-8')).hexdigest()


def _mdblist_row_digest(row_id):
	try:
		row = connect_database('mdblist_db').execute('SELECT data FROM mdblist_data WHERE id = ?', (row_id,)).fetchone()
		return _digest(row[0]) if row else None
	except Exception: return None


def _progress_digest(watched_db):
	try:
		return _digest(watched_db.execute('SELECT media_id, season, episode, resume_point, curr_time FROM progress WHERE db_type = ? '
			'ORDER BY media_id, season, episode', ('episode',)).fetchall())
	except Exception as e:
		kodi_utils.logger('Red Light', 'Next Episodes list cache: progress table unreadable: %s' % e)
		return None


def _favourites_digest():
	try:
		return _digest(connect_database('favorites_db').execute('SELECT tmdb_id, title FROM favourites WHERE db_type = ? ORDER BY tmdb_id',
			('tvshow',)).fetchall())
	except Exception as e:
		kodi_utils.logger('Red Light', 'Next Episodes list cache: favourites unreadable: %s' % e)
		return None


def local_state(watched_indicators, include_unwatched):
	"""What the list depends on outside settings, or None when a piece of it isn't held locally or
	could not be read (a key that could never match again would make the cache look empty, not broken)."""
	if watched_indicators not in LOCAL_PROVIDERS: return None
	from modules import watched_status as ws
	watched_db = ws.get_database(watched_indicators)
	watched = ws.watched_table_fingerprint(watched_db)
	# watched_table_fingerprint() answers 'unknown-<time>' when the table can't be read; In Progress
	# relies on that, so it is only translated here.
	if str(watched).startswith('unknown-'):
		kodi_utils.logger('Red Light', 'Next Episodes list cache: watched table unreadable')
		return None
	progress = _progress_digest(watched_db)
	if progress is None: return None
	state = [watched, progress]
	if watched_indicators == 3:
		dropped = _mdblist_row_digest(MDBLIST_DROPPED_ROW)
		if dropped is None: return None
		state.append(dropped)
		if include_unwatched in (1, 3):
			watchlist = _mdblist_row_digest(MDBLIST_WATCHLIST_ROW)
			if watchlist is None: return None
			state.append(watchlist)
	else: state.append(sorted(str(i) for i in (ws.get_hidden_progress_items(0) or [])))
	if include_unwatched in (2, 3):
		favourites = _favourites_digest()
		if favourites is None: return None
		state.append(favourites)
	return state


def cache_key(is_external, anime=False):
	"""The key for the list named list_name(is_external, anime). Both flags are explicit: the service
	computes keys for lists it did not render, where kodi_utils.external() would not know. The anime
	flag picks the row (list_name) and, like is_external, is also part of the key."""
	try:
		state = local_state(settings.watched_indicators(), settings.nextep_include_unwatched())
		if state is None: return None
		values = [(name, getattr(settings, name)()) for name in SETTING_GETTERS]
		values.append(('single_ep_display_format', settings.single_ep_display_format(is_external)))
		values.append(('rpdb_info', settings.rpdb_info('tvshow')))
		dated = settings.nextep_include_airdate() or settings.nextep_airing_today() or settings.nextep_include_unaired()
		return _digest((values, state, str(get_datetime()) if dated else '', bool(anime), bool(is_external)))
	except Exception as e:
		kodi_utils.logger('Red Light', 'Next Episodes list cache key failed: %s' % e)
		return None


def store(key, is_external, anime, items, future_dates, category):
	"""items: [(url, row)] in display order. future_dates: air dates after today seen during the build.
	Always records what was shown. Stored only if the key still matches, so a watched-table write that
	landed mid-build is never filed under the state before it; the service then sees the recorded key
	is out of date and asks for a rebuild."""
	record_served(is_external, anime, key)
	try:
		if not key:
			# Built without a key (provider switched, a table unreadable): an older list must not
			# outlive it and come back as the saved list at a later start.
			widget_cache.delete_list(list_name(is_external, anime))
			return
		if cache_key(is_external, anime) != key: return
		payload = {'items': [{'url': url, 'row': row} for url, row in items], 'category': category,
				'valid_until': str(min(future_dates)) if future_dates else '', 'built_at': int(time.time())}
		json.dumps(payload)
		widget_cache.set_list(list_name(is_external, anime), key, payload, ttl=STALE_MAX)
	except Exception as e: kodi_utils.logger('Red Light', 'Next Episodes list cache store failed: %s' % e)


def forget():
	for is_external, anime in ALL_LISTS: widget_cache.delete_list(list_name(is_external, anime))


def _miss(reason, started):
	kodi_utils.logger('Red Light', 'Next Episodes list cache miss (%s), %.2fs' % (reason, time.time() - started))
	return False


def _age_text(seconds):
	return '%.1f h' % (seconds / 3600.0) if seconds >= 3600 else '%d s' % seconds


def _may_answer_stale(is_external, anime):
	"""Stale is for the start of a session only: while this list has had no answer yet, or only stale
	ones (a second container asking for the same path at boot). Once it was built or hit this session,
	a changed state builds. Never for a provider with no local key: its builds never replace the row."""
	try:
		if settings.watched_indicators() not in LOCAL_PROVIDERS: return False
		raw = kodi_utils.get_property(SERVED_PROP % list_name(is_external, anime))
		return not raw or json.loads(raw).get('key') == STALE_KEY
	except Exception: return False


def _widgets_refreshed_at():
	try: return int(float(kodi_utils.get_property(REFRESHED_PROP) or 0))
	except Exception: return 0


def _choose(is_external, anime, revalidate):
	"""(kind, stored key, payload, age, reason): kind is 'hit', 'stale' or None (a miss, for reason)."""
	key = cache_key(is_external, anime)
	stored = widget_cache.get_list_any(list_name(is_external, anime))
	if not stored or not stored[1]: return None, None, None, None, 'inputs not held locally' if key is None else 'nothing stored'
	stored_key, payload = stored
	built_at = int(payload.get('built_at') or 0)
	age = int(time.time()) - built_at
	# A widget refresh asks for current data, so a list built before it is never served, not even stale.
	refreshed = built_at < _widgets_refreshed_at()
	if refreshed: reason = 'widgets refreshed since'
	elif key is None: reason = 'inputs not held locally'
	elif stored_key != key: reason = 'state changed since it was stored'
	elif age >= LIST_TTL: reason = 'older than %s' % _age_text(LIST_TTL)
	elif payload.get('valid_until') and str(get_datetime()) >= payload['valid_until']: reason = 'an episode has aired since'
	else: return 'hit', stored_key, payload, age, ''
	if is_external and revalidate == '' and age < STALE_MAX and not refreshed and _may_answer_stale(is_external, anime):
		return 'stale', stored_key, payload, age, reason
	return None, None, None, age, reason


def serve(params):
	"""Answer build_next_episode from a stored list. True when the directory was served; False means
	the caller builds it (and the build stores a fresh list)."""
	started = time.time()
	try: handle = int(sys.argv[1])
	except Exception: return _miss('no directory handle', started)
	try:
		is_external, anime = kodi_utils.external(), 'is_anime_list' in params
		revalidate = revalidate_state()
		if revalidate == REBUILD:
			kodi_utils.set_property(REVALIDATE_PROP, DONE)
			return _miss('rebuild asked for by the service', started)
		kind, stored_key, payload, age, reason = _choose(is_external, anime, revalidate)
		if kind is None: return _miss(reason, started)
		make_listitem, kodi_actor = kodi_utils.make_listitem, kodi_utils.kodi_actor()
		items = [(i['url'], render_episode_row(i['row'], make_listitem, kodi_actor), False) for i in payload['items']]
	except Exception as e: return _miss('error: %s' % e, started)
	# Committed from here: a build can no longer answer this handle, so the directory must end whatever
	# happens, or Kodi's fetch of the widget fails outright instead of showing a list.
	failed = None
	try:
		kodi_utils.add_items(handle, items)
		kodi_utils.set_content(handle, 'episodes')
		kodi_utils.set_category(handle, payload.get('category') or 'Next Episodes')
	except Exception as e: failed = e
	finally: kodi_utils.end_directory(handle, cacheToDisc=False)
	kodi_utils.set_view_mode('view.episodes_single', 'episodes', is_external, fallback_view_types=('view.episodes',))
	record_served(is_external, anime, STALE_KEY if kind == 'stale' else stored_key)
	if failed is not None:
		kodi_utils.logger('Red Light', 'Next Episodes list cache serve failed after committing: %s' % failed)
	elif kind == 'stale':
		kodi_utils.logger('Red Light', 'Next Episodes list cache stale (%s): %s listed, built %s ago, rebuild pending, %.2fs'
			% (reason, len(items), _age_text(age), time.time() - started))
	else:
		kodi_utils.logger('Red Light', 'Next Episodes list cache hit: %s listed, built %s ago, %.2fs'
			% (len(items), _age_text(age), time.time() - started))
	return True
