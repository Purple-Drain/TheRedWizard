# -*- coding: utf-8 -*-
"""Rewatch from here (#1 C407): a local, per-show cursor for rewatching watched episodes in order.

Next Episodes normally walks on to the first unwatched episode, so starting an old, already watched
episode (S03E11 when E12 to E21 are watched too) still offers E22. A cursor makes the list offer the
literal next episode instead (E12, E13, ...) until the rewatch catches up with the first unwatched
one. It lives in the add-on profile only: nothing is unmarked in MDBList, Trakt or Simkl.

Set by:
  - the context menu "Rewatch From Here" on a watched episode (always; shows that episode itself);
  - starting an already watched episode, when Next Episodes' "After a Rewatch" setting is
    "Continue From the Rewatch" (shows the episode after it).
Moved on by every later play of that show; cleared by playing an unwatched episode (normal order
has caught up), by the menu's "Stop Rewatching", or after CURSOR_MAX_AGE_SEC without a play.
"""
import json
import os
import time

from modules import kodi_utils

CURSOR_FILE = 'rewatch_cursor.json'
CURSOR_MAX_AGE_SEC = 30 * 86400


def _path():
	return os.path.join(kodi_utils.addon_profile(), CURSOR_FILE)


def load(now=None):
	now = time.time() if now is None else now
	try:
		with open(_path(), 'r') as handle: data = json.load(handle)
	except Exception: return {}
	if not isinstance(data, dict): return {}
	return {k: v for k, v in data.items() if isinstance(v, dict) and now - float(v.get('at', 0)) <= CURSOR_MAX_AGE_SEC}


def _save(data):
	try:
		with open(_path(), 'w') as handle: json.dump(data, handle)
		return True
	except Exception as exc:
		kodi_utils.logger('Red Light', 'Rewatch cursor save failed: %s' % exc)
		return False


def _touch(tmdb_id):
	try:
		from modules.show_touch import touch
		touch(tmdb_id)
	except Exception: pass


def get(tmdb_id, now=None):
	return load(now).get(str(tmdb_id))


def set_cursor(tmdb_id, season, episode, show_self=False, now=None):
	_touch(tmdb_id)
	data = load(now)
	data[str(tmdb_id)] = {'season': int(season), 'episode': int(episode), 'show_self': bool(show_self),
		'at': time.time() if now is None else now}
	return _save(data)


def clear(tmdb_id):
	_touch(tmdb_id)
	data = load()
	if data.pop(str(tmdb_id), None) is None: return False
	return _save(data)


def state_token():
	"""Part of the Next Episodes list cache key, so a cursor change rebuilds the list."""
	data = load()
	return sorted((k, v.get('season'), v.get('episode'), v.get('show_self')) for k, v in data.items())


def seed(tmdb_id):
	"""(season, episode, show_self) for the list, or None."""
	cursor = get(tmdb_id)
	if not cursor: return None
	try: return int(cursor['season']), int(cursor['episode']), bool(cursor.get('show_self'))
	except Exception: return None


def on_play_started(tmdb_id, season, episode, already_watched, continue_mode):
	"""Called once per Red Light episode play. Returns what it did, for the log and the tests."""
	try: season, episode = int(season), int(episode)
	except Exception: return None
	if season == 0: return None
	existing = get(tmdb_id)
	if not already_watched:
		if existing and clear(tmdb_id): return 'cleared'
		return None
	if existing or continue_mode:
		set_cursor(tmdb_id, season, episode)
		return 'moved' if existing else 'set'
	return None


def menu(params):
	"""mode=watched_status.rewatch_cursor: action=set (Rewatch From Here) or action=clear (Stop Rewatching)."""
	tmdb_id = params.get('tmdb_id')
	if params.get('action') == 'clear':
		clear(tmdb_id)
		kodi_utils.notification('Rewatch stopped', 2500)
	else:
		# show_self=false and quiet=true let a maintenance restore put back an automatic cursor exactly.
		set_cursor(tmdb_id, params.get('season'), params.get('episode'), show_self=params.get('show_self', 'true') != 'false')
		if params.get('quiet') != 'true':
			kodi_utils.notification('Next Episodes: rewatching from %sx%02d' % (params.get('season'), int(params.get('episode'))), 3000)
	kodi_utils.logger('Red Light', 'Rewatch cursor %s: tmdb=%s S%sE%s' % (params.get('action', 'set'), tmdb_id, params.get('season'), params.get('episode')))
	try: kodi_utils.kodi_refresh()
	except Exception: pass
