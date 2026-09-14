# -*- coding: utf-8 -*-
"""The finished Next Episodes list, served before the indexer loads (#155).

At a cold boot the Next Episodes widget spent about 5.3 s importing its own code before it built
anything (4.4 s of that was `requests`, which every provider API module imports), then called
MDBList's sync/last_activities over the network, then rebuilt every watched show. The router asks
serve() first. The hit, stale and miss rules, the recorded keys and the service's checks live in
modules.saved_lists (shared with In Progress, #163); this module is Next Episodes' spec: its key, its
rows (modules.episode_rows) and its own validity check.

The key is read from local state only: the active provider's watched and progress tables, the
dropped shows and watchlist as the provider's cache holds them, favourites, and every setting the
rows read. Working it out imports no provider module and makes no network call. Anything only the
provider knows (its cache row cleared by an activity sync, a provider with no local reader here, or
a table that could not be read) means no key, so no hit.

A stored list also stops being served on the earliest air date among its next episodes, so a newly
aired episode appears (valid_until).
"""
import hashlib
from caches.base_cache import connect_database
from modules import kodi_utils, settings, saved_lists
from modules.episode_rows import render_episode_row
from modules.utils import get_datetime
from modules.saved_lists import (LIST_TTL, STALE_MAX, REVALIDATE_AFTER, CHECKPOINTS, MAX_REBUILDS, FIRST_ANSWER_WAIT, REBUILD_WAIT,
	SERVED_PROP, REBUILD, DONE, REFRESHED_PROP, STALE_KEY, LOCAL_PROVIDERS)

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


def _still_valid(payload):
	if payload.get('valid_until') and str(get_datetime()) >= payload['valid_until']: return 'an episode has aired since'
	return ''


# Looked up at call time, so a patched cache_key or get_datetime (tests) is what the engine uses.
SPEC = saved_lists.register(saved_lists.Spec('next_episodes', 'next_episodes', 'Next Episodes', 'episodes',
	key=lambda is_external, anime: cache_key(is_external, anime),
	render=lambda row, make_listitem, kodi_actor: render_episode_row(row, make_listitem, kodi_actor),
	category='Next Episodes', view='view.episodes_single', fallback_views=('view.episodes',), variants=(False, True),
	still_valid=lambda payload: _still_valid(payload)))
ALL_LISTS = SPEC.lists()


def list_name(is_external, anime=False):
	return SPEC.list_name(is_external, anime)


# The home widget's rebuild request, the one the tests and most log lines are about.
REVALIDATE_PROP = saved_lists.REVALIDATE_PROP % list_name(True, False)


def revalidate_state(is_external=True, anime=False):
	return saved_lists.revalidate_state(list_name(is_external, anime))


def record_served(is_external, anime, key):
	saved_lists.record_served(SPEC, is_external, anime, key)


def served_lists():
	return saved_lists.served_lists(SPEC)


def store(key, is_external, anime, items, future_dates, category):
	"""items: [(url, row)] in display order. future_dates: air dates after today seen during the build."""
	saved_lists.store(SPEC, key, is_external, anime, [(url, row, False) for url, row in items], category,
		extra={'valid_until': str(min(future_dates)) if future_dates else ''})


def forget():
	saved_lists.forget(SPEC)


def serve(params):
	return saved_lists.serve(SPEC, params, 'is_anime_list' in params)
