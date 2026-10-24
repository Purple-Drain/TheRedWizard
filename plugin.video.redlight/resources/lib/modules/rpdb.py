# -*- coding: utf-8 -*-
import hashlib
import os
import time
from threading import Lock

_HIT_SECONDS = 7 * 24 * 3600
_MISS_SECONDS = 24 * 3600
_MAX_BYTES = 8 * 1024 * 1024
_host_down = False
_swept = False
_guard = Lock()
_url_locks = {}

def resolve_poster(url, fallback):
	"""Local copy of a RatingPosterDB image, or the TMDB poster when that image is missing."""
	if _host_down or not url: return fallback
	folder = _folder()
	if not folder: return fallback
	name = hashlib.sha1(url.encode('utf-8')).hexdigest()
	path, miss = os.path.join(folder, name + '.jpg'), os.path.join(folder, name + '.miss')
	cached = _cached(path, miss, fallback)
	if cached is not None: return cached
	with _lock_for(name):
		if _host_down: return fallback
		cached = _cached(path, miss, fallback)
		if cached is not None: return cached
		_sweep_once(folder)
		return _fetch(url, path, miss, fallback)

def _folder():
	try:
		from modules.kodi_utils import addon_profile
		folder = os.path.join(addon_profile(), 'rpdb_cache')
		os.makedirs(folder, exist_ok=True)
		return folder
	except Exception:
		return ''

def _lock_for(name):
	with _guard:
		lock = _url_locks.get(name)
		if lock is None:
			lock = Lock()
			_url_locks[name] = lock
		return lock

def _fresh(path, seconds, min_size):
	try:
		return os.path.isfile(path) and os.path.getsize(path) >= min_size and (time.time() - os.path.getmtime(path)) < seconds
	except Exception:
		return False

def _cached(path, miss, fallback):
	if _fresh(path, _HIT_SECONDS, 32): return path
	if _fresh(miss, _MISS_SECONDS, 0): return fallback
	return None

def _image_bytes(data):
	if not data or len(data) < 32 or len(data) > _MAX_BYTES: return False
	if data[:3] == b'\xff\xd8\xff': return True
	if data[:8] == b'\x89PNG\r\n\x1a\n': return True
	if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP': return True
	return False

def _mark_miss(miss):
	try:
		with open(miss, 'wb') as handle: handle.write(b'')
	except Exception:
		pass

def _save(path, data):
	temp = path + '.part'
	with open(temp, 'wb') as handle: handle.write(data)
	os.replace(temp, path)

def _fetch(url, path, miss, fallback):
	global _host_down
	try:
		from requests.exceptions import ConnectionError, Timeout
		from modules.kodi_utils import make_session
		response = make_session('https://api.ratingposterdb.com').get(url, timeout=8)
		if response.status_code == 200 and _image_bytes(response.content):
			try: _save(path, response.content)
			except Exception: return fallback
			try:
				if os.path.isfile(miss): os.remove(miss)
			except Exception: pass
			return path
		if response.status_code in (200, 404): _mark_miss(miss)
		return fallback
	except ConnectionError:
		_host_down = True
		return fallback
	except Timeout:
		return fallback
	except Exception:
		return fallback

def _sweep_once(folder):
	global _swept
	if _swept: return
	with _guard:
		if _swept: return
		_swept = True
	cutoff = time.time() - (_HIT_SECONDS + _MISS_SECONDS)
	try:
		for name in os.listdir(folder):
			path = os.path.join(folder, name)
			try:
				if os.path.isfile(path) and os.path.getmtime(path) < cutoff: os.remove(path)
			except Exception: pass
	except Exception: pass
