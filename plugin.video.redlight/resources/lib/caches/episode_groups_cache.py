# -*- coding: utf-8 -*-
from caches.base_cache import connect_database
# from modules.kodi_utils import logger

class EpisodeGroupsCache:
	def get(self, tmdb_id):
		try: data = eval(connect_database('episode_groups_db').execute('SELECT data FROM groups_data WHERE tmdb_id = ?', (str(tmdb_id),)).fetchone()[0])
		except: data = {}
		return data

	def set(self, tmdb_id, data):
		connect_database('episode_groups_db').execute('INSERT OR REPLACE INTO groups_data VALUES (?, ?)', (str(tmdb_id), repr(data)))
		_forget_widget_rows(tmdb_id)

	def delete(self, tmdb_id):
		dbcon = connect_database('episode_groups_db')
		dbcon.execute('DELETE FROM groups_data where tmdb_id=?', (str(tmdb_id),))
		dbcon.execute('VACUUM')
		_forget_widget_rows(tmdb_id)

	def clear_cache(self):
		dbcon = connect_database('episode_groups_db')
		dbcon.execute('DELETE FROM groups_data')
		dbcon.execute('VACUUM')
		_forget_widget_rows()

def _forget_widget_rows(tmdb_id=None):
	# A group assignment changes a show's aired totals and next-episode order, which the widget
	# cache derived from the previous assignment (#120, #121).
	try:
		from caches.widget_cache import widget_cache
		if tmdb_id is None: widget_cache.clear()
		else: widget_cache.delete_show(tmdb_id)
	except: pass

episode_groups_cache = EpisodeGroupsCache()
