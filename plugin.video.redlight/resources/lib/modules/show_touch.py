# -*- coding: utf-8 -*-
"""When the owner last changed a show's watched state from Red Light (#1, sort "Recently Updated").

Marking or unmarking episodes, seasons or the show, "Unmark Previous Watched", and a rewatch cursor
set, move or clear all touch the show. Next Episodes' "Recently Updated" sort orders by the later of
the show's last watched row and this touch, so a show just worked on rises to the top even when an
unmark made its newest watched row older. Local only (add-on profile); never sent to a tracker.
"""
import json
import os
import time

from modules import kodi_utils

TOUCH_FILE = 'show_touch.json'
TOUCH_MAX_AGE_SEC = 60 * 86400


def _path():
	return os.path.join(kodi_utils.addon_profile(), TOUCH_FILE)


def load(now=None):
	now = time.time() if now is None else now
	try:
		with open(_path(), 'r') as handle: data = json.load(handle)
	except Exception: return {}
	if not isinstance(data, dict): return {}
	return {k: float(v) for k, v in data.items() if isinstance(v, (int, float)) and now - float(v) <= TOUCH_MAX_AGE_SEC}


def touch(tmdb_id, now=None):
	if not tmdb_id: return False
	data = load(now)
	data[str(tmdb_id)] = time.time() if now is None else now
	try:
		with open(_path(), 'w') as handle: json.dump(data, handle)
		return True
	except Exception as exc:
		kodi_utils.logger('Red Light', 'Show touch save failed: %s' % exc)
		return False


def get(tmdb_id):
	return load().get(str(tmdb_id))


def state_token():
	"""Part of the Next Episodes list cache key, so a touch rebuilds the list."""
	return sorted(load().items())
