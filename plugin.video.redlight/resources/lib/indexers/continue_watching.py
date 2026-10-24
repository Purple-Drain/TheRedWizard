# -*- coding: utf-8 -*-
import sys
from indexers.movies import Movies
from indexers.episodes import build_single_episode, _widget_item_limit
from modules import kodi_utils, watched_status as ws
from datetime import datetime

def build_continue_watching(params):
	"""Paused movies, paused episodes, and the next episode of each show with nothing paused, most recently played first (#284).

	Widget path: plugin://plugin.video.redlight/?mode=build_continue_watching
	Next-ups sort by when the show was last watched but carry no ListItem.LastPlayed, since they were never played.
	Unaired next-ups and never-started shows (Next Episodes' include-unwatched options) are left out: nothing to continue.
	"""
	handle, is_external = int(sys.argv[1]), kodi_utils.external()
	never = datetime.min
	movie_rows = ws.get_in_progress_movies('movie', 1)
	movies = Movies({'action': 'in_progress_movies', 'list': [(c, i['media_id']) for c, i in enumerate(movie_rows)], 'custom_order': 'true'})
	movies.last_played_by_id = {str(i['media_id']): i['last_played'] for i in movie_rows}
	rows = [(ws.local_last_played(movie_rows[c]['last_played']) or never, item) for item, c in movies.worker()]
	paused = build_single_episode('episode.progress', {'collect': 'true'}) or []
	paused_shows = {str(i['row_packet']['tmdb_id']) for i in paused if i.get('row_packet')}
	next_up = build_single_episode('episode.next', {'collect': 'true', 'nextep_sort': 'last_played'}) or []
	next_up = [i for i in next_up if not (i.get('unaired') or i.get('unwatched')) and not (i.get('row_packet') and str(i['row_packet']['tmdb_id']) in paused_shows)]
	rows += [(ws.local_last_played(i.get('last_played')) or never, i['list_items']) for i in paused + next_up]
	rows.sort(key=lambda k: k[0], reverse=True)
	limit = _widget_item_limit(is_external)
	if limit: rows = rows[:limit]
	kodi_utils.add_items(handle, [i[1] for i in rows])
	kodi_utils.set_content(handle, 'episodes')
	kodi_utils.set_category(handle, 'Continue Watching')
	kodi_utils.end_directory(handle, cacheToDisc=False)
	kodi_utils.set_view_mode('view.episodes_single', 'episodes', is_external, fallback_view_types=('view.episodes',))
