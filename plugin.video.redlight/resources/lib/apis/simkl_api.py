# -*- coding: utf-8 -*-
import json
import time
import calendar
import requests
from datetime import datetime
from threading import Lock
from urllib.parse import urljoin
from caches import simkl_cache
from caches.settings_cache import get_setting, set_setting
from modules import kodi_utils, settings
from modules.utils import TaskPool, copy2clip, make_qrcode, jsondate_to_datetime as js2date

BASE_URL = 'https://api.simkl.com'
OAUTH_PIN_URL = 'https://api.simkl.com/oauth/pin'
SIMKL_APP_NAME = 'plugin.video.redlight'
SIMKL_CLIENT_ID = '6cacc8db22e67b2cd423ef73a9fd3a4f45146ba7fbf30fb2ae28f2fa9d0c2583'
_request_lock = Lock()
_last_request_time = 0.0

def _throttle():
	global _last_request_time
	with _request_lock:
		elapsed = time.time() - _last_request_time
		if elapsed < 1.0: kodi_utils.sleep(int((1.0 - elapsed) * 1000) + 50)
		_last_request_time = time.time()

def _client_id():
	return SIMKL_CLIENT_ID

def _headers():
	token = get_setting('redlight.simkl.token', '0')
	h = {'Content-Type': 'application/json', 'simkl-api-key': _client_id(), 'User-Agent': '%s/%s' % (SIMKL_APP_NAME, kodi_utils.addon_version())}
	if token not in ('0', '', None, 'empty_setting'): h['Authorization'] = 'Bearer %s' % token
	return h

def _url(path, auth=True):
	cid = _client_id()
	if not cid: return None
	base = path if path.startswith('http') else urljoin(BASE_URL, path.lstrip('/'))
	sep = '&' if '?' in base else '?'
	return '%s%sclient_id=%s&app-name=%s&app-version=%s' % (base, sep, cid, SIMKL_APP_NAME, kodi_utils.addon_version())

def _pin_headers():
	return {'User-Agent': '%s/%s' % (SIMKL_APP_NAME, kodi_utils.addon_version())}

def _pin_url(user_code=None):
	url = '%s/%s' % (OAUTH_PIN_URL, user_code) if user_code else OAUTH_PIN_URL
	sep = '&' if '?' in url else '?'
	return '%s%sclient_id=%s&app-name=%s&app-version=%s' % (url, sep, _client_id(), SIMKL_APP_NAME, kodi_utils.addon_version())

def _simkl_pin_auth_url(pin):
	user_code = pin.get('user_code', '')
	verify = (pin.get('verification_uri') or pin.get('verification_url') or 'https://simkl.com/pin').rstrip('/')
	return '%s/%s' % (verify, user_code)

def call_simkl(path, data=None, method=None, is_delete=False):
	_throttle()
	url = _url(path)
	if not url: return None
	headers = _headers()
	try:
		if is_delete:
			resp = requests.delete(url, headers=headers, timeout=20)
		elif method == 'get' or (data is None and not method):
			resp = requests.get(url, headers=headers, timeout=20)
		else:
			payload = json.dumps(data) if isinstance(data, (dict, list)) else data
			resp = requests.post(url, data=payload, headers=headers, timeout=20)
		if resp.status_code in (200, 201): return resp.json() if resp.text else True
		if resp.status_code == 204: return True
		kodi_utils.logger('Simkl', 'HTTP %s %s' % (resp.status_code, url))
	except Exception as e: kodi_utils.logger('Simkl Error', str(e))
	return None

def simkl_get_pin():
	try: return requests.get(_pin_url(), headers=_pin_headers(), timeout=20).json()
	except: return None

def simkl_poll_pin(pin):
	user_code = pin.get('user_code')
	if not user_code: return None
	expires_in = int(pin.get('expires_in') or 900)
	interval = max(int(pin.get('interval') or 5), 1)
	auth_url = _simkl_pin_auth_url(pin)
	qr_code = make_qrcode(auth_url) or ''
	copy2clip(auth_url)
	content = 'Enter [B]%s[/B] at [B]simkl.com/pin[/B][CR]OR scan the [B]QR Code[/B][CR]Link copied to clipboard[CR][CR]Waiting for authorization...' % user_code
	progress = kodi_utils.progress_dialog('Simkl Authorize', qr_code)
	progress.update(content, 0)
	expires = time.time() + expires_in
	while time.time() < expires:
		if progress.iscanceled():
			progress.close()
			return None
		_throttle()
		try:
			resp = requests.get(_pin_url(user_code), headers=_pin_headers(), timeout=20).json()
			if resp.get('access_token'):
				progress.close()
				return resp['access_token']
		except: pass
		progress.update(content, int(100 * (1 - (expires - time.time()) / float(expires_in))))
		kodi_utils.sleep(interval * 1000)
	progress.close()
	return None

def simkl_authenticate(dummy=''):
	pin = simkl_get_pin()
	if not pin or not pin.get('user_code'): return kodi_utils.notification('Simkl Authorization Failed', 3000)
	token = simkl_poll_pin(pin)
	if not token: return kodi_utils.notification('Simkl Authorization Canceled', 3000)
	set_setting('simkl.token', token)
	info = call_simkl('/users/settings')
	if info and info.get('user'):
		set_setting('simkl.user', str(info['user'].get('name') or info['user'].get('login') or 'Simkl User'))
	else: set_setting('simkl.user', 'Simkl User')
	set_setting('watched_indicators', '2')
	kodi_utils.notification('Simkl Account Authorized', 3000)
	simkl_sync_activities(force_update=True)
	return True

def simkl_revoke_authentication(dummy=''):
	set_setting('simkl.user', 'empty_setting')
	set_setting('simkl.token', '0')
	if get_setting('redlight.watched_indicators', '0') == '2': set_setting('watched_indicators', '0')
	simkl_cache.clear_all_simkl_cache_data(silent=True, refresh=False)
	kodi_utils.notification('Simkl Authorization Reset', 3000)

def _tmdb_id(ids):
	try:
		if ids.get('tmdb'): return str(int(ids['tmdb']))
	except: pass
	return None

def simkl_watched_status_mark(action, media_type, tmdb_id, tvdb_id=0, season=None, episode=None):
	if action == 'mark_as_watched':
		url, key = '/sync/history', 'added'
		watched_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
	else:
		url, key = '/sync/history/remove', 'deleted'
		watched_at = None
	if media_type == 'movie':
		item = {'ids': {'tmdb': int(tmdb_id)}}
		if watched_at: item['watched_at'] = watched_at
		data = {'movies': [item]}
		success_key = 'movies'
	elif media_type in ('episode',):
		ep = {'number': int(episode)}
		if watched_at: ep['watched_at'] = watched_at
		data = {'shows': [{'ids': {'tmdb': int(tmdb_id)}, 'seasons': [{'number': int(season), 'episodes': [ep]}]}]}
		success_key = 'episodes'
	elif media_type == 'season':
		data = {'shows': [{'ids': {'tmdb': int(tmdb_id)}, 'seasons': [{'number': int(season)}]}]}
		success_key = 'episodes'
	else:
		data = {'shows': [{'ids': {'tmdb': int(tmdb_id)}}]}
		success_key = 'episodes'
	result = call_simkl(url, data=data)
	if not result: return False
	try:
		count = result[key][success_key]
		if count > 0: return True
		# Scrobble stop at 100% may already have synced; treat idempotent mark/unmark as success.
		return True
	except: return False

def _scrobble_payload(media_type, tmdb_id, percent, season=None, episode=None):
	data = {'progress': float(percent)}
	if media_type == 'movie':
		data['movie'] = {'ids': {'tmdb': int(tmdb_id)}}
	else:
		data['show'] = {'ids': {'tmdb': int(tmdb_id)}}
		data['episode'] = {'season': int(season), 'number': int(episode)}
	return data

def simkl_scrobble(action, media_type, tmdb_id, percent=0, season=None, episode=None):
	if not settings.simkl_user_active(): return
	path = {'start': '/scrobble/start', 'pause': '/scrobble/pause', 'stop': '/scrobble/stop'}.get(action)
	if not path: return
	call_simkl(path, data=_scrobble_payload(media_type, tmdb_id, percent, season, episode))

def simkl_progress(action, media_type, tmdb_id, percent, season=None, episode=None, resume_id=None, refresh_simkl=False):
	if action == 'clear_progress' and resume_id:
		_throttle()
		url = _url('/sync/playback/%s' % resume_id)
		if not url: return
		try: requests.delete(url, headers=_headers(), timeout=20)
		except: pass
	else:
		simkl_scrobble('pause', media_type, tmdb_id, percent, season, episode)
	if refresh_simkl: simkl_sync_activities(force_update=True)

def simkl_reset_scrobble(params):
	from modules.watched_status import erase_bookmark
	media_type, tmdb_id = params.get('media_type'), params.get('tmdb_id')
	season, episode = params.get('season', ''), params.get('episode', '')
	watched_db = __import__('modules.watched_status', fromlist=['get_database']).get_database(2)
	try:
		if media_type == 'movie':
			simkl_scrobble('stop', 'movie', tmdb_id, 0)
			resume_id = watched_db.execute('SELECT resume_id FROM progress WHERE db_type=? AND media_id=?', ('movie', str(tmdb_id))).fetchone()[0]
			simkl_progress('clear_progress', 'movie', tmdb_id, 0, resume_id=resume_id)
			erase_bookmark('movie', tmdb_id, '', '', 'true')
		elif media_type == 'episode' and season and episode:
			simkl_scrobble('stop', 'episode', tmdb_id, 0, season, episode)
			row = watched_db.execute('SELECT resume_id FROM progress WHERE db_type=? AND media_id=? AND season=? AND episode=?',
				('episode', str(tmdb_id), int(season), int(episode))).fetchone()
			if row:
				simkl_progress('clear_progress', 'episode', tmdb_id, 0, season, episode, resume_id=row[0])
			erase_bookmark('episode', tmdb_id, season, episode, 'true')
		else: return kodi_utils.notification('Reset Scrobble is only available for movies and episodes', 3500)
		kodi_utils.notification('Success', 3000)
	except: kodi_utils.notification('Error', 3000)

def simkl_add_to_list(listname, tmdb_id, media_type, imdb_id=None, tvdb_id=None):
	if media_type == 'movie':
		post = {'movies': [{'to': listname, 'ids': {'tmdb': int(tmdb_id)}}]}
	else:
		ids = {'tmdb': int(tmdb_id)}
		if imdb_id and imdb_id not in ('None', None, ''): ids['imdb'] = imdb_id
		if tvdb_id and str(tvdb_id) not in ('None', '0', ''):
			try: ids['tvdb'] = int(tvdb_id)
			except: ids['tvdb'] = tvdb_id
		post = {'shows': [{'to': listname, 'ids': ids}]}
	result = call_simkl('/sync/add-to-list', data=post)
	if result: kodi_utils.notification('Success', 3000)
	else: kodi_utils.notification('Error', 3000)
	return result

def simkl_remove_from_list(listname, tmdb_id, media_type, imdb_id=None, tvdb_id=None):
	if media_type == 'movie':
		post = {'movies': [{'ids': {'tmdb': int(tmdb_id)}}]}
		url = '/sync/%s/remove' % listname if listname in ('plantowatch', 'hold', 'dropped') else '/sync/history/remove'
	else:
		ids = {'tmdb': int(tmdb_id)}
		if imdb_id and imdb_id not in ('None', None, ''): ids['imdb'] = imdb_id
		if tvdb_id and str(tvdb_id) not in ('None', '0', ''):
			try: ids['tvdb'] = int(tvdb_id)
			except: ids['tvdb'] = tvdb_id
		post = {'shows': [{'ids': ids}]}
		url = '/sync/%s/remove' % listname if listname in ('plantowatch', 'hold', 'dropped') else '/sync/history/remove'
	result = call_simkl(url, data=post)
	if result: kodi_utils.notification('Success', 3000)
	else: kodi_utils.notification(kodi_utils.LIST_ITEM_NOT_IN_LIST, 3000)

def simkl_indicators_movies():
	insert_list = []
	insert_append = insert_list.append
	data = call_simkl('/sync/all-items/movies/completed?extended=full', method='get') or {}
	for item in data.get('movies', data if isinstance(data, list) else []):
		try:
			movie = item.get('movie', item)
			tmdb_id = _tmdb_id(movie.get('ids', {}))
			if not tmdb_id: continue
			watched_at = item.get('last_watched_at') or item.get('watched_at') or datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
			insert_append(('movie', tmdb_id, '', '', watched_at, movie.get('title', '')))
		except: pass
	simkl_cache.simkl_watched_cache.set_bulk_movie_watched(insert_list)

def simkl_indicators_tv():
	insert_list = []
	insert_append = insert_list.append
	data = call_simkl('/sync/all-items/shows/?extended=full&episode_watched_at=yes', method='get') or {}
	for item in data.get('shows', data if isinstance(data, list) else []):
		try:
			show = item.get('show', item)
			tmdb_id = _tmdb_id(show.get('ids', {}))
			if not tmdb_id: continue
			title = show.get('title', '')
			for season in item.get('seasons', []):
				snum = season.get('number', season.get('season'))
				for ep in season.get('episodes', []):
					watched_at = ep.get('watched_at') or ep.get('last_watched_at')
					if not watched_at: continue
					insert_append(('episode', tmdb_id, snum, ep.get('number', ep.get('episode')), watched_at, title))
		except: pass
	simkl_cache.simkl_watched_cache.set_bulk_tvshow_watched(insert_list)

def simkl_sync_playback():
	items = call_simkl('/sync/playback', method='get') or []
	movie_ins, ep_ins = [], []
	for item in items:
		try:
			if item.get('type') == 'movie':
				tmdb_id = _tmdb_id(item.get('movie', {}).get('ids', {}))
				if not tmdb_id: continue
				movie_ins.append(('movie', tmdb_id, '', '', str(round(item['progress'], 1)), 0, item.get('paused_at', ''), item['id'], item['movie'].get('title', '')))
			elif item.get('type') == 'episode':
				show = item.get('show', {})
				tmdb_id = _tmdb_id(show.get('ids', {}))
				if not tmdb_id: continue
				ep = item.get('episode', {})
				ep_ins.append(('episode', tmdb_id, ep.get('season'), ep.get('number'), str(round(item['progress'], 1)), 0,
					item.get('paused_at', ''), item['id'], show.get('title', '')))
		except: pass
	simkl_cache.simkl_watched_cache.set_bulk_movie_progress(movie_ins)
	simkl_cache.simkl_watched_cache.set_bulk_tvshow_progress(ep_ins)

def _activity_ts(ts_str):
	if not ts_str: return 0
	try: return int(calendar.timegm(time.strptime(ts_str.rstrip('Z').split('.')[0], '%Y-%m-%dT%H:%M:%S')))
	except: return 0

def _activity_block_changed(latest_blk, cached_blk, keys):
	for key in keys:
		if _activity_ts(latest_blk.get(key, '')) > _activity_ts(cached_blk.get(key, '')): return True
	return False

def simkl_sync_activities(params=None, force_update=False):
	if isinstance(params, dict): force_update = params.get('force_update', 'false') in ('true', 'True', True) or force_update
	if not settings.simkl_user_active(): return 'no account'
	if force_update: simkl_cache.clear_all_simkl_cache_data(silent=True, refresh=False)
	try: latest = call_simkl('/sync/activities', method='get')
	except: return 'failed'
	if not latest: return 'failed'
	cached = simkl_cache.reset_activity(latest)
	if not force_update and _activity_ts(latest.get('all', '')) <= _activity_ts(cached.get('all', '')): return 'not needed'
	movies, shows = latest.get('movies', {}), latest.get('tv_shows', {})
	cached_movies, cached_shows = cached.get('movies', {}), cached.get('tv_shows', {})
	watched_keys = ('completed', 'removed_from_list', 'all')
	playback_keys = ('playback', 'all')
	if force_update or _activity_block_changed(movies, cached_movies, watched_keys):
		simkl_indicators_movies()
	if force_update or _activity_block_changed(shows, cached_shows, watched_keys):
		simkl_indicators_tv()
	if force_update or _activity_block_changed(movies, cached_movies, playback_keys) or _activity_block_changed(shows, cached_shows, playback_keys):
		simkl_sync_playback()
	return 'success'

def simkl_force_sync(params=None):
	if not settings.simkl_user_active(): return kodi_utils.notification('Simkl account not authorized', 3000)
	progress = kodi_utils.progress_dialog('Simkl Sync')
	progress.update('Syncing with Simkl...', 0)
	status = simkl_sync_activities(force_update=True)
	progress.close()
	if status == 'failed': kodi_utils.notification('Simkl Sync Failed', 3000)
	else:
		kodi_utils.notification('Simkl Sync Complete', 3000)
		kodi_utils.kodi_refresh()
	return status
