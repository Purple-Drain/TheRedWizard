# -*- coding: utf-8 -*-
from threading import Thread
from caches.base_cache import connect_database
from modules import kodi_utils

class PunchPlayCache:
	def get(self, string):
		try:
			dbcon = connect_database('punchplay_db')
			cache_data = dbcon.execute('SELECT data FROM punchplay_data WHERE id = ?', (string,)).fetchone()
			if cache_data: return eval(cache_data[0])
		except: pass
		return None

	def set(self, string, data):
		try:
			dbcon = connect_database('punchplay_db')
			dbcon.execute('INSERT OR REPLACE INTO punchplay_data (id, data) VALUES (?, ?)', (string, repr(data)))
		except: return None

	def delete(self, string):
		try:
			dbcon = connect_database('punchplay_db')
			dbcon.execute('DELETE FROM punchplay_data WHERE id = ?', (string,))
		except: pass

punchplay_cache = PunchPlayCache()

class PunchPlayWatched:
	def set_bulk_movie_watched(self, insert_list):
		self._delete('DELETE FROM watched WHERE db_type = ?', ('movie',))
		self._executemany('INSERT OR IGNORE INTO watched VALUES (?, ?, ?, ?, ?, ?)', insert_list)

	def set_bulk_tvshow_watched(self, insert_list):
		self._delete('DELETE FROM watched WHERE db_type = ?', ('episode',))
		self._executemany('INSERT OR IGNORE INTO watched VALUES (?, ?, ?, ?, ?, ?)', insert_list)

	def set_bulk_movie_progress(self, insert_list):
		self._delete('DELETE FROM progress WHERE db_type = ?', ('movie',))
		self._executemany('INSERT OR IGNORE INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', insert_list)

	def set_bulk_tvshow_progress(self, insert_list):
		self._delete('DELETE FROM progress WHERE db_type = ?', ('episode',))
		self._executemany('INSERT OR IGNORE INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', insert_list)

	def _executemany(self, command, insert_list):
		dbcon = connect_database('punchplay_db')
		dbcon.executemany(command, insert_list)

	def _delete(self, command, args):
		dbcon = connect_database('punchplay_db')
		dbcon.execute(command, args)
		# No VACUUM here — it rewrites the whole DB and blocks Next Episodes / list builds for many seconds.

punchplay_watched_cache = PunchPlayWatched()

def clear_all_punchplay_cache_data(silent=False, refresh=True):
	try:
		if not silent and not kodi_utils.confirm_dialog(): return False
		dbcon = connect_database('punchplay_db')
		dbcon.execute('DELETE FROM punchplay_data')
		dbcon.execute('DELETE FROM watched')
		dbcon.execute('DELETE FROM progress')
		try:
			from caches.lists_cache import lists_cache
			lists_cache.delete_like('punchplay_%')
		except: pass
		if not silent: kodi_utils.notification('PunchPlay Cache Cleared', 3000)
		if refresh:
			from apis.punchplay_api import punchplay_sync_activities
			Thread(target=punchplay_sync_activities, kwargs={'force_update': True}).start()
		return True
	except: return False
