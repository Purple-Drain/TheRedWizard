# -*- coding: utf-8 -*-
"""The In Progress widgets' finished lists, served before the indexers load (#163).

At a start the In Progress TV widget took 8.5 s even with its data cached (13.09 capture): the process
imported the TV show indexer and the provider APIs, synced MDBList over the network, then drew every
row with a metadata lookup each. The router asks serve_tvshows() / serve_movies() first; the hit, stale
and miss rules, the recorded keys and the service's checks are modules.saved_lists'.

Only page 1 of the home widget is saved. The in-addon listing sets its own browse state on the way in
(set_browse_exit_params), and later pages carry their own offsets. Widgets usually paginate, so page 1
ends in a Next Page item; it is saved with the rows (saved_lists.nav_row) and drawn as add_dir() draws it.

The keys are read from local state only, and taken after the build's provider sync, so a list stored
from a sync that changed things is filed under the state it shows:
* TV: the watched table fingerprint, the episode progress rows, the hidden (dropped) shows as the
  provider's cache holds them, every setting the list and its rows read, and the date.
* Movies: the movie progress rows, the watched movies, every setting, and the date.
Per-show facts (aired-episode counts) are outside the keys; they drift only as far as their own
expiry allows (6 h airing, 168 h ended), as they already did in the In Progress data cache.
"""
from modules import kodi_utils, settings, saved_lists
from modules.movie_rows import render_movie_row
from modules.tvshow_rows import render_tvshow_row
from modules.nextep_list_cache import MDBLIST_DROPPED_ROW, _digest, _mdblist_row_digest
from modules.utils import get_datetime

# Every no-argument setting the In Progress lists, their rows, their paging or their order read,
# directly or through settings.append_*_context_menus(). tests/test_inprogress_lists.py checks these
# against the indexers and watched_status, so a new setting cannot silently leave a key stale.
TVSHOW_SETTING_GETTERS = (
	'watched_indicators', 'exclude_specials_from_progress', 'tv_progress_location', 'include_anime_tvshow', 'ignore_articles',
	'widget_hide_next_page', 'widget_hide_watched', 'tmdb_api_key', 'mpaa_region', 'default_all_episodes',
	'media_open_action_skip_inprogress_tvshow', 'cm_sort_order', 'cm_default_order', 'ai_model_active', 'trakt_user_active',
	'simkl_user_active', 'punchplay_user_active', 'mdblist_user_active', 'tmdblist_user_active', 'configured_external_scraper_slots')
MOVIE_SETTING_GETTERS = (
	'watched_indicators', 'ignore_articles', 'widget_hide_next_page', 'widget_hide_watched', 'tmdb_api_key', 'mpaa_region',
	'playback_key', 'media_open_action_skip_inprogress_movie', 'cm_sort_order', 'cm_default_order', 'ai_model_active',
	'trakt_user_active', 'simkl_user_active', 'punchplay_user_active', 'mdblist_user_active', 'tmdblist_user_active',
	'configured_external_scraper_slots')


def _rows_digest(watched_db, sql, args):
	try: return _digest(watched_db.execute(sql, args).fetchall())
	except Exception as e:
		kodi_utils.logger('Red Light', 'In Progress list cache: table unreadable: %s' % e)
		return None


def _settings(getters, media_type):
	values = [(name, getattr(settings, name)()) for name in getters]
	values.extend([('lists_sort_order', settings.lists_sort_order('progress')), ('paginate', settings.paginate(True)),
		('page_limit', settings.page_limit(True)), ('rpdb_info', settings.rpdb_info(media_type)),
		('media_open_action', settings.media_open_action(media_type))])
	return values


def _watched_db():
	"""The active provider's database, or None when its lists aren't held locally."""
	indicators = settings.watched_indicators()
	if indicators not in saved_lists.LOCAL_PROVIDERS: return None, None
	from modules import watched_status as ws
	return ws, ws.get_database(indicators)


def tvshow_key(is_external, variant=False):
	try:
		if not is_external: return None
		ws, watched_db = _watched_db()
		if watched_db is None: return None
		watched = ws.watched_table_fingerprint(watched_db)
		# 'unknown-<time>' when the table can't be read; In Progress's data cache relies on that answer.
		if str(watched).startswith('unknown-'):
			kodi_utils.logger('Red Light', 'In Progress list cache: watched table unreadable')
			return None
		progress = _rows_digest(watched_db, 'SELECT media_id, season, episode, resume_point, curr_time FROM progress WHERE db_type = ? '
			'ORDER BY media_id, season, episode', ('episode',))
		if progress is None: return None
		if settings.watched_indicators() == 3:
			hidden = _mdblist_row_digest(MDBLIST_DROPPED_ROW)
			if hidden is None: return None
		else: hidden = sorted(str(i) for i in (ws.get_hidden_progress_items(0) or []))
		return _digest(('tvshows', _settings(TVSHOW_SETTING_GETTERS, 'tvshow'), watched, progress, hidden, str(get_datetime())))
	except Exception as e:
		kodi_utils.logger('Red Light', 'In Progress TV list cache key failed: %s' % e)
		return None


def movie_key(is_external, variant=False):
	try:
		if not is_external: return None
		ws, watched_db = _watched_db()
		if watched_db is None: return None
		progress = _rows_digest(watched_db, 'SELECT media_id, title, last_played, resume_point, curr_time FROM progress WHERE db_type = ? '
			'ORDER BY media_id', ('movie',))
		watched = _rows_digest(watched_db, 'SELECT media_id, last_played FROM watched WHERE db_type = ? ORDER BY media_id', ('movie',))
		if progress is None or watched is None: return None
		return _digest(('movies', _settings(MOVIE_SETTING_GETTERS, 'movie'), progress, watched, str(get_datetime())))
	except Exception as e:
		kodi_utils.logger('Red Light', 'In Progress Movies list cache key failed: %s' % e)
		return None


# Looked up at call time, so a patched key (tests) is what the engine uses.
TVSHOWS = saved_lists.register(saved_lists.Spec('in_progress_tvshows', 'in_progress_tvshows_list', 'In Progress TV', 'tvshows',
	key=lambda is_external, variant: tvshow_key(is_external, variant),
	render=lambda row, make_listitem, kodi_actor: render_tvshow_row(row, make_listitem, kodi_actor),
	category='In Progress', view='view.tvshows', view_when_external=False))
MOVIES = saved_lists.register(saved_lists.Spec('in_progress_movies', 'in_progress_movies_list', 'In Progress Movies', 'movies',
	key=lambda is_external, variant: movie_key(is_external, variant),
	render=lambda row, make_listitem, kodi_actor: render_movie_row(row, make_listitem, kodi_actor),
	category='In Progress', view='view.movies', view_when_external=False))


def wanted(params):
	"""Page 1 of the home widget only (see the module docstring)."""
	try: return kodi_utils.external() and str(params.get('new_page', '1')) == '1' and str(params.get('paginate_start', '0')) in ('', '0')
	except Exception: return False


def serve_tvshows(params):
	return wanted(params) and saved_lists.serve(TVSHOWS, params)


def serve_movies(params):
	return wanted(params) and saved_lists.serve(MOVIES, params)


def store(spec, key, rows, next_page, category):
	"""rows: [(url, row, is_folder)] in display order; next_page: the Next Page item's url params (the
	dict the indexer passed to add_dir), or None when the list shows no Next Page item."""
	items = list(rows)
	if next_page:
		items.append((kodi_utils.build_url(next_page), saved_lists.nav_row('Next Page (%s) >>' % next_page['new_page'],
			kodi_utils.get_icon('nextpage'), kodi_utils.get_icon('nextpage_landscape')), True))
	saved_lists.store(spec, key, True, False, items, category)
