# -*- coding: utf-8 -*-
"""Small derived-result cache behind the home widgets (#120, #121).

Three kinds of row, all in maincache_db's existing ``maincache`` table under a ``WIDGET_``
id prefix, so the existing tools already cover them: "Clear Main Cache" drops them, the
scheduled clean sweeps expired rows, and nothing new needs creating on-device.

* show facts      -- per show: the aired-episode total and airing status that decide whether a
                     show is in progress or finished. Deriving these is what made the In Progress
                     widget slow (a full tvshow_meta load plus episode-group resolution per
                     watched show); reading them back is one query for every show at once.
* list            -- a finished widget list (In Progress shows) stored with the key it was built
                     under. A read with a different key is a miss, so a change in the watched
                     rows, hidden items or the relevant settings rebuilds; an unchanged widget
                     re-requested after a play/stop is a cache read.
* next episode    -- per show: the (season, episode) Next Episodes resolved for it, stored with a
                     key over that show's watched rows, so only shows whose rows changed recompute
                     group_ordered_episode_pairs()/get_next(). A show with no next episode is a
                     negative result -- also stored, under the same key, so an unwatched-count-only
                     rebuild doesn't recompute it every time (#121). Negative rows get a short ttl
                     (30 min, not the show's usual 6h/168h) since an airing show can gain a new
                     episode at any time; a positive result always replaces a negative one on read.

Rows are JSON, never eval'd. Expiry is seconds from now. Invalidation for things the keys cannot
see (episode-group assignment, a meta refresh) goes through delete_show()/clear(), called from the
caches that own those events.
"""
import json
import time
from caches.base_cache import connect_database
# from modules.kodi_utils import logger

PREFIX = 'WIDGET_'
FACTS_PREFIX = PREFIX + 'SHOWFACTS_'
LIST_PREFIX = PREFIX + 'LIST_'
NEXTEP_PREFIX = PREFIX + 'NEXTEP_'

# Airing shows gain episodes, so their facts and next-episode rows turn over within the day;
# ended shows only change when TMDb data or a group assignment does (hooked below).
AIRING_TTL = 6 * 3600
ENDED_TTL = 168 * 3600
LIST_TTL = 10 * 60
# A show with no next episode is re-checked far sooner than a positive result's own ttl -- an
# airing show can gain a new episode at any point, so this is deliberately short.
NEGATIVE_NEXTEP_TTL = 30 * 60

# get_next_episode() sentinel: a cached negative result (no next episode), distinct from a miss
# (no row / expired / key mismatch), which callers must treat differently -- a miss recomputes,
# a negative hit does not.
NEGATIVE = object()


def _now():
	return int(time.time())


def show_ttl(status):
	return ENDED_TTL if status in ('Ended', 'Canceled') else AIRING_TTL


class WidgetCache:
	# Exposed on the class so callers holding the instance (indexers/episodes.py compares
	# against widget_cache.NEGATIVE) see the same sentinel as the module constant.
	NEGATIVE = NEGATIVE
	def _connect(self):
		return connect_database('maincache_db')

	# --- show facts -------------------------------------------------------------------------
	def get_show_facts(self, tmdb_ids=None):
		"""{tmdb_id(str): {'aired_eps': int, 'status': str}} for every unexpired facts row, limited
		to tmdb_ids when given. One query regardless of how many shows are asked for."""
		wanted = None if tmdb_ids is None else set(str(i) for i in tmdb_ids)
		facts = {}
		try:
			dbcon = self._connect()
			rows = dbcon.execute('SELECT id, data, expires FROM maincache WHERE id LIKE ?', (FACTS_PREFIX + '%',)).fetchall()
			now = _now()
			for row_id, data, expires in rows:
				tmdb_id = row_id[len(FACTS_PREFIX):]
				if wanted is not None and tmdb_id not in wanted: continue
				try:
					if int(expires) <= now: continue
					facts[tmdb_id] = json.loads(data)
				except Exception: continue
		except Exception: pass
		return facts

	def set_show_facts(self, facts):
		"""facts: {tmdb_id: {'aired_eps': int, 'status': str}}; one write for the whole batch."""
		if not facts: return
		try:
			now = _now()
			rows = [(FACTS_PREFIX + str(tmdb_id), json.dumps(value), now + show_ttl(value.get('status', '')))
					for tmdb_id, value in facts.items()]
			dbcon = self._connect()
			dbcon.executemany('INSERT OR REPLACE INTO maincache (id, data, expires) VALUES (?, ?, ?)', rows)
		except Exception: pass

	# --- finished lists ---------------------------------------------------------------------
	def get_list(self, name, key):
		"""The list stored under name, only if it was stored with exactly this key and is unexpired."""
		try:
			dbcon = self._connect()
			row = dbcon.execute('SELECT data, expires FROM maincache WHERE id = ?', (LIST_PREFIX + name,)).fetchone()
			if not row: return None
			data, expires = row
			if int(expires) <= _now(): return None
			payload = json.loads(data)
			if payload.get('key') != key: return None
			return payload.get('data')
		except Exception: return None

	def set_list(self, name, key, data, ttl=LIST_TTL):
		try:
			dbcon = self._connect()
			dbcon.execute('INSERT OR REPLACE INTO maincache (id, data, expires) VALUES (?, ?, ?)',
				(LIST_PREFIX + name, json.dumps({'key': key, 'data': data}), _now() + int(ttl)))
		except Exception: pass

	# --- per-show next episode --------------------------------------------------------------
	def get_next_episode(self, tmdb_id, key):
		"""(season, episode) stored for the show under exactly this key; NEGATIVE if a "no next
		episode" result was cached under this key; else None (miss -- caller must recompute)."""
		try:
			dbcon = self._connect()
			row = dbcon.execute('SELECT data, expires FROM maincache WHERE id = ?', (NEXTEP_PREFIX + str(tmdb_id),)).fetchone()
			if not row: return None
			data, expires = row
			if int(expires) <= _now(): return None
			payload = json.loads(data)
			if payload.get('key') != key: return None
			season, episode = payload['season'], payload['episode']
			if season is None or episode is None: return NEGATIVE
			return int(season), int(episode)
		except Exception: return None

	def set_next_episode(self, tmdb_id, key, season, episode, status=''):
		"""Store a positive (season, episode) result, or a negative one when season/episode is
		None -- same key, same invalidation, a short ttl since the show may air again any time."""
		try:
			dbcon = self._connect()
			if season is None or episode is None:
				ttl = NEGATIVE_NEXTEP_TTL
				payload = {'key': key, 'season': None, 'episode': None}
			else:
				ttl = show_ttl(status)
				payload = {'key': key, 'season': int(season), 'episode': int(episode)}
			dbcon.execute('INSERT OR REPLACE INTO maincache (id, data, expires) VALUES (?, ?, ?)',
				(NEXTEP_PREFIX + str(tmdb_id), json.dumps(payload), _now() + ttl))
		except Exception: pass

	# --- invalidation -----------------------------------------------------------------------
	def delete_show(self, tmdb_id):
		"""Drop everything derived for one show: its facts, its next episode, and every stored list
		(a list is built from facts, so a changed show can change it)."""
		try:
			dbcon = self._connect()
			dbcon.execute('DELETE FROM maincache WHERE id IN (?, ?)', (FACTS_PREFIX + str(tmdb_id), NEXTEP_PREFIX + str(tmdb_id)))
			dbcon.execute('DELETE FROM maincache WHERE id LIKE ?', (LIST_PREFIX + '%',))
		except Exception: pass

	def clear(self):
		try:
			dbcon = self._connect()
			dbcon.execute('DELETE FROM maincache WHERE id LIKE ?', (PREFIX + '%',))
			return True
		except Exception: return False


widget_cache = WidgetCache()
