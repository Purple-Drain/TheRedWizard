# -*- coding: utf-8 -*-
"""The finished Next Episodes list, served before the indexer loads (#155).

At a cold boot the Next Episodes widget spent about 5.3 s importing its own code before it built
anything (4.4 s of that was `requests`, which every provider API module imports), then called
MDBList's sync/last_activities over the network, then rebuilt every watched show. The router asks
serve() first; when nothing the stored list was built from has changed, the widget gets that list
back without any of the three.

* The key is read from local state only: the active provider's watched and progress tables, the
  dropped shows and watchlist as the provider's cache holds them, favourites, and every setting the
  rows read. Working it out imports no provider module and makes no network call.
* Anything only the provider knows (its cache row cleared by an activity sync, or a provider with no
  local reader here) means no key, and the widget builds the normal way, which refetches it.
* Rows are the JSON-safe dicts modules.episode_rows renders, so a served ListItem is made by the same
  calls as a built one.

Freshness. The key moves on any mark, unmark, progress write, drop, watchlist or favourites change,
or setting change. The date is only in the key when a date-dependent setting is on; otherwise the
stored list carries the earliest future air date among its shows' next episodes and is not served
from that day on, so an episode that airs today still appears. What the key cannot see (a show whose
next episode TMDb has not listed yet, a metadata refresh) is bounded by LIST_TTL, by the per-show
hooks that call widget_cache.delete_show(), by Refresh Widgets (forget()), and by MDBListMonitor,
which syncs a minute after start and refreshes the widgets when anything changed.
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


def list_name(is_external):
	# One row per entry point: a widget and an in-addon listing render different context menus, and
	# sharing a row would have each evict the other.
	return 'next_episodes_widget' if is_external else 'next_episodes'


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
	except Exception: return 'unknown-%s' % time.time()


def _favourites_digest():
	try:
		return _digest(connect_database('favorites_db').execute('SELECT tmdb_id, title FROM favourites WHERE db_type = ? ORDER BY tmdb_id',
			('tvshow',)).fetchall())
	except Exception: return 'unknown-%s' % time.time()


def local_state(watched_indicators, include_unwatched):
	"""What the list depends on outside settings, or None when a piece of it isn't held locally."""
	if watched_indicators not in LOCAL_PROVIDERS: return None
	from modules import watched_status as ws
	watched_db = ws.get_database(watched_indicators)
	state = [ws.watched_table_fingerprint(watched_db), _progress_digest(watched_db)]
	if watched_indicators == 3:
		dropped = _mdblist_row_digest(MDBLIST_DROPPED_ROW)
		if dropped is None: return None
		state.append(dropped)
		if include_unwatched in (1, 3):
			watchlist = _mdblist_row_digest(MDBLIST_WATCHLIST_ROW)
			if watchlist is None: return None
			state.append(watchlist)
	else: state.append(sorted(str(i) for i in (ws.get_hidden_progress_items(0) or [])))
	if include_unwatched in (2, 3): state.append(_favourites_digest())
	return state


def cache_key(is_external, params):
	try:
		state = local_state(settings.watched_indicators(), settings.nextep_include_unwatched())
		if state is None: return None
		values = [(name, getattr(settings, name)()) for name in SETTING_GETTERS]
		values.append(('single_ep_display_format', settings.single_ep_display_format(is_external)))
		values.append(('rpdb_info', settings.rpdb_info('tvshow')))
		dated = settings.nextep_include_airdate() or settings.nextep_airing_today() or settings.nextep_include_unaired()
		return _digest((values, state, str(get_datetime()) if dated else '', 'is_anime_list' in params, bool(is_external)))
	except Exception as e:
		kodi_utils.logger('Red Light', 'Next Episodes list cache key failed: %s' % e)
		return None


def store(key, is_external, params, items, future_dates, category):
	"""items: [(url, row)] in display order. future_dates: air dates after today seen during the build.
	Stored only if the key still matches, so a watched-table write that landed mid-build is never
	filed under the state before it."""
	try:
		if not key or cache_key(is_external, params) != key: return
		payload = {'items': [{'url': url, 'row': row} for url, row in items], 'category': category,
				'valid_until': str(min(future_dates)) if future_dates else ''}
		json.dumps(payload)
		widget_cache.set_list(list_name(is_external), key, payload, ttl=LIST_TTL)
	except Exception as e: kodi_utils.logger('Red Light', 'Next Episodes list cache store failed: %s' % e)


def forget():
	for is_external in (True, False): widget_cache.delete_list(list_name(is_external))


def _miss(reason, started):
	kodi_utils.logger('Red Light', 'Next Episodes list cache miss (%s), %.2fs' % (reason, time.time() - started))
	return False


def serve(params):
	"""Answer build_next_episode from the stored list. True when the directory was served; False means
	the caller builds it (and the build stores a fresh list)."""
	started = time.time()
	try:
		is_external = kodi_utils.external()
		key = cache_key(is_external, params)
		if key is None: return _miss('inputs not held locally', started)
		payload = widget_cache.get_list(list_name(is_external), key)
		if payload is None: return _miss('nothing stored for this state', started)
		if payload.get('valid_until') and str(get_datetime()) >= payload['valid_until']: return _miss('an episode has aired since', started)
		make_listitem, kodi_actor = kodi_utils.make_listitem, kodi_utils.kodi_actor()
		items = [(i['url'], render_episode_row(i['row'], make_listitem, kodi_actor), False) for i in payload['items']]
	except Exception as e: return _miss('error: %s' % e, started)
	handle = int(sys.argv[1])
	kodi_utils.add_items(handle, items)
	kodi_utils.set_content(handle, 'episodes')
	kodi_utils.set_category(handle, payload.get('category') or 'Next Episodes')
	kodi_utils.end_directory(handle, cacheToDisc=False)
	kodi_utils.set_view_mode('view.episodes_single', 'episodes', is_external, fallback_view_types=('view.episodes',))
	kodi_utils.logger('Red Light', 'Next Episodes list cache hit: %s listed, %.2fs' % (len(items), time.time() - started))
	return True
