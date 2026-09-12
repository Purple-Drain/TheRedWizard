# -*- coding: utf-8 -*-
"""Watched mark and resume checkpoint for a Redlight play that Kodi resumed on its own (#143).

On sleep Kodi stores the playing item (CPowerManager::StorePlayerState) and on wake reopens it
itself (RestorePlayerState). The interpreter that ran RedLightPlayer.run() already exited at
sleep, so the resumed file plays with no RedLightPlayer, no monitor loop, and nothing that marks
it watched at the end or writes a resume checkpoint (#58's tick lives in that loop).

The service's RedLightMonitor lives for Kodi's whole lifetime and receives the system-wide
JSON-RPC Player.OnAVStart / Player.OnStop notifications whoever opened the file. This module is
what it calls:

- player.py writes a "last Redlight play" record (a home-window property that the stop-time
  clear_playback_properties() leaves alone, so it survives sleep/wake but not a Kodi restart).
- On Player.OnAVStart, if the playing item matches that record and, after a grace period,
  Redlight's own player has not registered it (redlight.active_playback_key), the item is
  tracked as an unclaimed resume.
- While tracked: a local resume checkpoint every POLL_INTERVAL_SEC through the same
  set_bookmark_checkpoint() #58 uses, and a watched mark once it passes 90% (same threshold as
  the monitor loop).
- On Player.OnStop with end=true: watched mark through the existing mark_episode()/mark_movie(),
  at most once per tracked play.

Out of scope here: autoplay into the next episode on wake, relaunching through playback.media,
intercepting RestorePlayerState. Those are #143's bigger slice.
"""
import json
from threading import Lock, Thread
from modules import kodi_utils as ku

LAST_PLAY_RECORD_PROP = 'redlight.last_play_record'
# Same property as player.PROP_ACTIVE_PLAYBACK_KEY: set by RedLightPlayer once its playback is
# confirmed, cleared when its monitor loop ends. Duplicated rather than imported so the service
# does not import player.py; tests assert the two stay equal.
ACTIVE_PLAYBACK_KEY_PROP = 'redlight.active_playback_key'
CLAIM_GRACE_SEC = 15
CLAIM_CHECK_SEC = 5
POLL_INTERVAL_SEC = 60
WATCHED_PERCENT = 90
CHECKPOINT_MIN_PERCENT = 5
_VIDEO_PLAYER_ID = 1


def _normalize_path(path):
	return str(path or '').split('|')[0].strip()


def write_last_play_record(player):
	"""Called from RedLightPlayer.set_playback_properties(). Only real Redlight metadata plays
	(episode or movie with a TMDb id) get a record; generic plays have nothing to mark."""
	try:
		if getattr(player, 'is_generic', False): return
		media_type = getattr(player, 'media_type', None)
		if media_type not in ('episode', 'movie'): return
		tmdb_id, url = getattr(player, 'tmdb_id', None), getattr(player, 'url', None)
		if not tmdb_id or not url: return
		# 'release' is the same value as redlight.now_playing_release. Debrid links are often hash
		# paths, so the URL alone cannot name combined-file sibling episodes (#47 reuses this).
		record = {'url': _normalize_path(url), 'release': getattr(player, 'playing_filename', None) or None,
				'media_type': media_type, 'tmdb_id': tmdb_id,
				'tvdb_id': getattr(player, 'tvdb_id', None), 'imdb_id': getattr(player, 'imdb_id', None),
				'title': getattr(player, 'title', None), 'year': getattr(player, 'year', None),
				'season': getattr(player, 'season', None), 'episode': getattr(player, 'episode', None)}
		ku.set_property(LAST_PLAY_RECORD_PROP, json.dumps(record))
	except Exception: pass


def read_last_play_record():
	try:
		record = json.loads(ku.get_property(LAST_PLAY_RECORD_PROP) or '')
		if not isinstance(record, dict): return None
		if record.get('media_type') not in ('episode', 'movie') or not record.get('tmdb_id') or not record.get('url'): return None
		if record['media_type'] == 'episode':
			int(record.get('season')), int(record.get('episode'))
		return record
	except Exception:
		return None


def item_matches_record(item, record):
	"""The resumed file's URL is the one Redlight played. The TMDb id + season/episode fallback
	covers the fresh-start path, where the info tag's filename is a synthetic label."""
	try:
		if not isinstance(item, dict): return False
		playing = _normalize_path(item.get('file'))
		if playing and playing == _normalize_path(record.get('url')): return True
		tmdb = (item.get('uniqueid') or {}).get('tmdb')
		if not tmdb or str(tmdb) != str(record.get('tmdb_id')): return False
		if record.get('media_type') == 'movie': return True
		return int(item.get('season')) == int(record.get('season')) and int(item.get('episode')) == int(record.get('episode'))
	except Exception:
		return False


def _watched_functions():
	from modules import watched_status as ws
	return ws.mark_episode, ws.mark_movie, ws.set_bookmark_checkpoint


def _jsonrpc(method, params):
	return ku.get_jsonrpc({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})


def _seconds(value):
	try: return value['hours'] * 3600 + value['minutes'] * 60 + value['seconds'] + value.get('milliseconds', 0) / 1000.0
	except Exception: return None


def _claimed_by_redlight():
	return bool(ku.get_property(ACTIVE_PLAYBACK_KEY_PROP))


def _label(record):
	if record.get('media_type') == 'episode':
		try: return '%s S%02dE%02d' % (record.get('title') or '', int(record['season']), int(record['episode']))
		except Exception: pass
	return str(record.get('title') or record.get('tmdb_id'))


class WakeResumeWatcher:
	def __init__(self, wait_for_abort, start_thread=None):
		# wait_for_abort: the service Monitor's waitForAbort(seconds); True means Kodi is exiting.
		self._wait = wait_for_abort
		self._start_thread = start_thread or (lambda target: Thread(target=target, daemon=True, name='wake_resume').start())
		self._lock = Lock()
		self._session = 0
		self._tracked = None

	def on_notification(self, method, data):
		try:
			if method == 'Player.OnAVStart': self.on_av_start(data)
			elif method == 'Player.OnStop': self.on_stop(data)
		except Exception as e:
			ku.logger('Red Light', 'wake resume: %s failed: %s' % (method, e))

	def on_av_start(self, data):
		with self._lock:
			self._session += 1
			session, self._tracked = self._session, None
		record = read_last_play_record()
		if not record: return
		item = self._playing_item(data)
		if not item_matches_record(item, record): return
		self._start_thread(lambda: self._watch(session, record))

	def on_stop(self, data):
		try: info = json.loads(data) if isinstance(data, str) else (data or {})
		except Exception: info = {}
		with self._lock:
			tracked, self._tracked = self._tracked, None
			self._session += 1
		if not tracked or _claimed_by_redlight(): return
		if isinstance(info, dict) and info.get('end') is True:
			self._mark_watched(tracked, 'end of file')

	def _playing_item(self, data):
		try: info = json.loads(data) if isinstance(data, str) else (data or {})
		except Exception: info = {}
		try: player_id = int(info['player']['playerid'])
		except Exception: player_id = _VIDEO_PLAYER_ID
		result = _jsonrpc('Player.GetItem', {'playerid': player_id, 'properties': ['file', 'uniqueid', 'season', 'episode']}) or {}
		return result.get('item')

	def _current(self, session, tracked):
		with self._lock:
			return session == self._session and self._tracked is tracked

	def _watch(self, session, record):
		try:
			if self._wait(CLAIM_GRACE_SEC): return
			with self._lock:
				if session != self._session or _claimed_by_redlight(): return
				tracked = self._tracked = {'record': record, 'marked': False, 'point': 0.0}
			ku.logger('Red Light', 'wake resume: %s is playing outside RedLightPlayer, tracking it (#143)' % _label(record))
			ticks_per_poll, tick = max(1, POLL_INTERVAL_SEC // CLAIM_CHECK_SEC), None
			while self._current(session, tracked):
				if _claimed_by_redlight():
					with self._lock:
						if self._tracked is tracked: self._tracked = None
					return
				if tick is None or tick >= ticks_per_poll:
					tick = 0
					self._poll(tracked)
				if self._wait(CLAIM_CHECK_SEC): return
				tick += 1
		except Exception as e:
			ku.logger('Red Light', 'wake resume: watch failed: %s' % e)

	def _poll(self, tracked):
		props = _jsonrpc('Player.GetProperties', {'playerid': _VIDEO_PLAYER_ID, 'properties': ['time', 'totaltime']}) or {}
		curr, total = _seconds(props.get('time')), _seconds(props.get('totaltime'))
		if not curr or not total or total < 60: return
		point = round(curr / total * 100, 1)
		tracked['point'] = point
		record = tracked['record']
		if point >= WATCHED_PERCENT:
			self._mark_watched(tracked, '%.1f%%' % point)
		elif point >= CHECKPOINT_MIN_PERCENT:
			set_bookmark_checkpoint = _watched_functions()[2]
			set_bookmark_checkpoint({'media_type': record['media_type'], 'tmdb_id': record['tmdb_id'], 'curr_time': curr, 'total_time': total,
									'title': record.get('title'), 'season': record.get('season'), 'episode': record.get('episode'), 'from_playback': 'true'})

	def _mark_watched(self, tracked, reason):
		with self._lock:
			if tracked['marked']: return
			tracked['marked'] = True
		record = tracked['record']
		mark_episode, mark_movie, _ = _watched_functions()
		function = mark_movie if record['media_type'] == 'movie' else mark_episode
		params = {'action': 'mark_as_watched', 'tmdb_id': record['tmdb_id'], 'title': record.get('title'), 'year': record.get('year'),
				'season': record.get('season'), 'episode': record.get('episode'), 'tvdb_id': record.get('tvdb_id'), 'from_playback': 'true'}
		ku.logger('Red Light', 'wake resume: marking %s watched (%s) (#143)' % (_label(record), reason))
		try: function(params)
		except Exception as e: ku.logger('Red Light', 'wake resume: watched mark failed: %s' % e)
