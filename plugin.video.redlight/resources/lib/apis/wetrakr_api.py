# -*- coding: utf-8 -*-
"""WeTrakr lists and, when it is the Watched Status Provider, scrobble and history.

Device OAuth plus Red Light's app key. Playback is sent only while WeTrakr is the provider.
"""
import json
import threading
import time
import requests
from caches.settings_cache import set_setting
from modules import kodi_utils, settings
from modules.utils import copy2clip, make_qrcode, make_tinyurl, \
							device_auth_complete_url, device_auth_site_label, authorise_wait_text

BASE_URL = 'https://api.wetrakr.com'
APP_UA = 'RedLight-WeTrakr/%s' % kodi_utils.addon_version()
_REFRESH_LOCK = threading.Lock()
_REFRESH_PROP = 'redlight.wetrakr_refresh_lock'
_POLL_STOP = ('expired_token', 'access_denied', 'invalid_code', 'already_used', 'invalid_client', 'locked_client')
_UP_NEXT_TTL = 90
_UP_NEXT_FAIL_TTL = 20
_UP_NEXT_CACHE = {'at': 0.0, 'rows': None, 'ok': False}
_SYNC_LOCK_PROP = 'redlight.wetrakr_sync_lock'
_SYNC_LOCK_TTL = 300  # seconds; a stale lock from a crashed sync is auto-expired after 5 minutes

def _wetrakr_icon():
	return kodi_utils.get_icon('wetrakr') or kodi_utils.addon_icon()

def _token():
	from caches.settings_cache import live_setting
	return live_setting('wetrakr.token', '0')

def wetrakr_user_active():
	return settings.wetrakr_user_active()

def _parse_int(value):
	if value in (None, '', 'None', 'empty_setting', 0, '0'): return None
	try:
		result = int(value)
		return result if result > 0 else None
	except: return None

def _client_id():
	from modules.http_defaults import shipped_setting
	return shipped_setting('wetrakr.client') or ''

def _with_app_key(headers):
	client_id = _client_id()
	if not client_id: return headers
	headers['wetrakr-api-key'] = client_id
	headers['wetrakr-api-version'] = '1'
	return headers

def _oauth_headers():
	"""Device and refresh calls. The app key goes in the body, not the key header."""
	return {
		'Content-Type': 'application/json',
		'User-Agent': APP_UA,
		'wetrakr-api-version': '1',
	}

def _usable(value):
	return value not in (None, '0', '', 'empty_setting')

def _refresh_token():
	from caches.settings_cache import live_setting
	return live_setting('wetrakr.refresh', '0')

def _expires_at():
	from caches.settings_cache import live_setting
	try: return float(live_setting('wetrakr.expires', '0') or 0)
	except (TypeError, ValueError): return 0.0

def _grant_expiring():
	expires = _expires_at()
	if expires <= 0: return False
	return time.time() >= (expires - 120)

def _save_grant(data):
	access = data.get('access_token')
	if not access: return False
	refresh = data.get('new_refresh_token') or data.get('refresh_token')
	try: expires_in = int(data.get('expires_in') or 604800)
	except (TypeError, ValueError): expires_in = 604800
	set_setting('wetrakr.token', access)
	if refresh: set_setting('wetrakr.refresh', refresh)
	set_setting('wetrakr.expires', str(int(time.time()) + max(120, expires_in) - 120))
	return True

def _clear_grant():
	set_setting('wetrakr.user', 'empty_setting')
	set_setting('wetrakr.token', '0')
	set_setting('wetrakr.refresh', '0')
	set_setting('wetrakr.expires', '0')

def _claim_refresh_window():
	owner = '%s-%s' % (time.time(), threading.current_thread().ident or id(threading.current_thread()))
	try:
		raw = str(kodi_utils.get_property(_REFRESH_PROP) or '')
		if raw:
			try: stamp = float(raw.split('-', 1)[0])
			except (TypeError, ValueError): stamp = 0
			if stamp and (time.time() - stamp) < 20: return ''
		kodi_utils.set_property(_REFRESH_PROP, owner)
		kodi_utils.sleep(50)
		if str(kodi_utils.get_property(_REFRESH_PROP) or '') != owner: return ''
	except Exception:
		return owner
	return owner

def _release_refresh_window(owner):
	try:
		if owner and str(kodi_utils.get_property(_REFRESH_PROP) or '') == owner:
			kodi_utils.clear_property(_REFRESH_PROP)
	except Exception:
		pass

def _claim_sync_lock(wait_secs=0):
	"""Acquire the single-sync guard. Returns an owner token if acquired, '' if another sync holds the lock."""
	owner = '%s-%s' % (time.time(), threading.current_thread().ident or id(threading.current_thread()))
	deadline = time.time() + wait_secs
	while True:
		raw = str(kodi_utils.get_property(_SYNC_LOCK_PROP) or '')
		if raw:
			try: stamp = float(raw.split('-', 1)[0])
			except (TypeError, ValueError): stamp = 0
			if stamp and (time.time() - stamp) < _SYNC_LOCK_TTL:
				# Lock is held and not stale — wait if the caller asked for it, else give up
				if time.time() < deadline:
					kodi_utils.sleep(1000)
					continue
				return ''
		# Lock is free (or stale) — try to claim it with a compare-and-set
		kodi_utils.set_property(_SYNC_LOCK_PROP, owner)
		kodi_utils.sleep(50)
		if str(kodi_utils.get_property(_SYNC_LOCK_PROP) or '') == owner:
			return owner
		# Race: another thread claimed it in the 50 ms window — retry if time allows
		if time.time() < deadline:
			kodi_utils.sleep(500)
			continue
		return ''

def _release_sync_lock(owner):
	try:
		if owner and str(kodi_utils.get_property(_SYNC_LOCK_PROP) or '') == owner:
			kodi_utils.clear_property(_SYNC_LOCK_PROP)
	except Exception:
		pass

def _refresh_access_token(force=False):
	"""Trade the refresh token for a new access token. The old refresh token is rotated."""
	refresh = _refresh_token()
	if not _usable(refresh): return False
	with _REFRESH_LOCK:
		if not force and _usable(_token()) and not _grant_expiring(): return True
		seen = _refresh_token()
		owner = _claim_refresh_window()
		if not owner:
			for _ in range(25):
				kodi_utils.sleep(200)
				if _refresh_token() != seen and _usable(_token()): return True
			return False
		try:
			current = _refresh_token()
			resp = requests.post(
				'%s/oauth/token/refresh' % BASE_URL.rstrip('/'),
				data=json.dumps({'refresh_token': current if _usable(current) else refresh}),
				headers=_oauth_headers(), timeout=15)
			if resp.status_code != 200:
				kodi_utils.logger('WeTrakr', 'refresh failed: HTTP %s' % resp.status_code)
				return False
			try: payload = resp.json() or {}
			except Exception: payload = {}
			if not payload.get('access_token'):
				kodi_utils.logger('WeTrakr', 'refresh failed: no access token')
				return False
			return _save_grant(payload)
		except Exception as e:
			kodi_utils.logger('WeTrakr', 'refresh failed: %s' % e)
			return False
		finally:
			_release_refresh_window(owner)

def wetrakr_lists_active():
	return settings.wetrakr_lists_active()

def request_device_code():
	"""Public device flow. An empty body is the scrobbler webhook and cannot call the account API."""
	client_id = _client_id()
	if not client_id:
		kodi_utils.logger('WeTrakr', 'device/code missing app key')
		return None
	url = '%s/oauth/device/code' % BASE_URL.rstrip('/')
	try:
		resp = requests.post(url, data=json.dumps({'client_id': client_id}), headers=_oauth_headers(), timeout=15)
		if resp.status_code == 200:
			try: data = resp.json()
			except Exception: data = {}
			if isinstance(data, dict) and data.get('device_code'): return data
		kodi_utils.logger('WeTrakr', 'device/code HTTP %s' % resp.status_code)
	except Exception as e:
		kodi_utils.logger('WeTrakr', 'device/code error: %s' % e)
	return None

def _poll_device_token(device_code):
	url = '%s/oauth/device/token' % BASE_URL.rstrip('/')
	body = {'code': device_code, 'client_id': _client_id()}
	try:
		resp = requests.post(url, data=json.dumps(body), headers=_oauth_headers(), timeout=12)
		try: return resp.json()
		except Exception: return {'error': 'http_%s' % resp.status_code}
	except Exception as e:
		kodi_utils.logger('WeTrakr', 'poll error: %s' % e)
		return None

def _account_username(token):
	"""None means the new token was rejected. '' means the name could not be read."""
	url = '%s/account/settings' % BASE_URL.rstrip('/')
	headers = _with_app_key({'Content-Type': 'application/json', 'User-Agent': APP_UA})
	headers['Authorization'] = 'Bearer %s' % token
	try:
		resp = requests.get(url, headers=headers, timeout=20)
	except Exception as e:
		kodi_utils.logger('WeTrakr', 'account read failed: %s' % e)
		return ''
	if resp.status_code in (401, 403): return None
	if not (200 <= resp.status_code < 300): return ''
	try: payload = resp.json() or {}
	except Exception: return ''
	info = payload.get('info') if isinstance(payload, dict) else None
	if not isinstance(info, dict): info = {}
	name = info.get('username') or info.get('display_name')
	return str(name) if name else ''

def wetrakr_authenticate(dummy=''):
	icon = _wetrakr_icon()
	code_data = request_device_code()
	if not code_data or not code_data.get('device_code'):
		return kodi_utils.ok_dialog(heading='WeTrakr',
			text='WeTrakr authorisation failed.[CR]Could not start device authorisation. Try again, or check your connection.')
	user_code = str(code_data.get('user_code') or '')
	device_code = code_data.get('device_code')
	expires_in = int(code_data.get('expires_in') or 600)
	interval = max(int(code_data.get('interval') or 5), 1)
	auth_url = device_auth_complete_url(code_data, user_code, fallback='https://wetrakr.com/activate', style='query')
	qr_code = make_qrcode(auth_url) or icon
	try: copy2clip(auth_url)
	except: pass
	short_url = make_tinyurl(auth_url)
	content = authorise_wait_text(user_code, device_auth_site_label(code_data, 'https://wetrakr.com/activate'), short_url)
	progress = kodi_utils.progress_dialog('WeTrakr Authorise', qr_code)
	progress.update(content, 0)
	expires = time.time() + expires_in
	grant, canceled = None, False
	while time.time() < expires:
		if progress.iscanceled():
			canceled = True
			break
		data = _poll_device_token(device_code)
		if isinstance(data, dict):
			if data.get('access_token'):
				grant = data
				break
			error = data.get('error') or ''
			if error in _POLL_STOP:
				kodi_utils.logger('WeTrakr', 'poll: %s' % error)
				break
			if error and error not in ('authorization_pending', 'slow_down'):
				kodi_utils.logger('WeTrakr', 'poll: %s' % error)
		progress.update(content, int(100 * (1 - (expires - time.time()) / float(expires_in))))
		if kodi_utils.sleep_while_authorising(progress, interval):
			canceled = True
			break
	try: progress.close()
	except: pass
	if canceled:
		return kodi_utils.notification('WeTrakr Authorisation Canceled', 3000, icon)
	if not grant or not _save_grant(grant):
		return kodi_utils.notification('WeTrakr Error Authorising', 3000, icon)
	username = _account_username(grant.get('access_token'))
	if username is None:
		_clear_grant()
		return kodi_utils.notification('WeTrakr Error Authorising', 3000, icon)
	set_setting('wetrakr.user', username or 'WeTrakr User')
	from caches.settings_cache import settings_cache
	from modules.meta_auth_alerts import clear_alert
	settings_cache.clear_db_cache()
	clear_alert('wetrakr')
	kodi_utils.notification('WeTrakr Account Authorised', 3000, icon)
	try: settings.offer_watched_provider(5, 'WeTrakr')
	except: pass
	try:
		kodi_utils.run_plugin({
			'mode': 'wetrakr.wetrakr_sync_activities',
			'force_update': 'true',
			'notify': 'true',
		}, block=False)
	except: pass
	try: kodi_utils.container_refresh()
	except: pass
	return True

def _logout_remote():
	token = _token()
	refresh = _refresh_token()
	if not _usable(token) or not _usable(refresh): return
	headers = _with_app_key(_oauth_headers())
	headers['Authorization'] = 'Bearer %s' % token
	try:
		requests.post(
			'%s/oauth/logout' % BASE_URL.rstrip('/'),
			data=json.dumps({'refresh_token': refresh}),
			headers=headers, timeout=10)
	except Exception:
		pass

def wetrakr_revoke_authentication(dummy=''):
	if not kodi_utils.confirm_revoke('WeTrakr'): return
	from modules.meta_auth_alerts import clear_alert
	clear_alert('wetrakr')
	_logout_remote()
	_clear_grant()
	try: settings.fallback_watched_provider_on_revoke(5)
	except: pass
	kodi_utils.notification('WeTrakr Authorisation Reset', 3000, _wetrakr_icon())
	try: kodi_utils.container_refresh()
	except: pass
	return True

def _retry_seconds(resp):
	try:
		reset = resp.headers.get('RateLimit-Reset') or resp.headers.get('Retry-After')
		if reset in (None, ''): return 2
		return max(1, min(int(float(reset)), 5))
	except Exception:
		return 2

def _request(method, path, params=None, data=None, user=True, timeout=20, _retried=False, _auth_retried=False):
	"""Public API call. Never logs the key or the token.

	user=False is the app key only (public calendars and catalog lookups).
	A 429 waits for the per-minute window once, then gives up.
	"""
	client_id = _client_id()
	if user and _grant_expiring(): _refresh_access_token()
	token = _token() if user else None
	if not client_id: return None, {}
	if user and not _usable(token): return None, {}
	headers = _with_app_key({
		'Content-Type': 'application/json',
		'User-Agent': APP_UA,
	})
	if user:
		headers['Authorization'] = 'Bearer %s' % token
	url = '%s%s' % (BASE_URL.rstrip('/'), path)
	try:
		if method == 'GET':
			resp = requests.get(url, params=params or {}, headers=headers, timeout=timeout)
		elif method == 'DELETE':
			kwargs = {'headers': headers, 'timeout': timeout}
			if params: kwargs['params'] = params
			if data is not None: kwargs['data'] = json.dumps(data)
			resp = requests.delete(url, **kwargs)
		else:
			resp = requests.post(url, data=json.dumps(data or {}), headers=headers, timeout=timeout)
	except Exception as e:
		kodi_utils.logger('WeTrakr', 'request failed: %s' % e)
		return None, {}
	if resp.status_code == 429 and not _retried:
		kodi_utils.sleep(_retry_seconds(resp) * 1000)
		return _request(method, path, params, data, user, timeout, _retried=True, _auth_retried=_auth_retried)
	if resp.status_code == 420:
		try: body = resp.json()
		except Exception: body = {}
		if not isinstance(body, dict): body = {}
		body['_locked'] = True
		return body, resp.headers
	if resp.status_code == 404: return {'_missing': True}, resp.headers
	if resp.status_code in (401, 403):
		if resp.status_code == 401 and user and not _auth_retried and _refresh_access_token(force=True):
			return _request(method, path, params, data, user, timeout, _retried=_retried, _auth_retried=True)
		kodi_utils.logger('WeTrakr', 'Auth rejected (HTTP %s)' % resp.status_code)
		if user:
			try:
				from modules.meta_auth_alerts import maybe_notify_refresh_failure
				maybe_notify_refresh_failure('wetrakr', resp.status_code, resp.text)
			except Exception: pass
		return None, resp.headers
	if not (200 <= resp.status_code < 300):
		kodi_utils.logger('WeTrakr', 'HTTP %s %s' % (resp.status_code, path))
		return None, resp.headers
	try: return resp.json(), resp.headers
	except Exception: return None, resp.headers

def _api(method, path, params=None, data=None, user=True, timeout=20):
	payload, _headers = _request(method, path, params, data, user, timeout=timeout)
	return payload

def _rows(payload):
	if isinstance(payload, list): return payload
	if isinstance(payload, dict):
		for key in ('data', 'items', 'results'):
			if isinstance(payload.get(key), list): return payload[key]
	return []

def _page_count(headers):
	try: return int(headers.get('X-Pagination-Page-Count') or 0)
	except Exception: return 0

def _page_limit(headers, requested):
	try:
		actual = headers.get('X-Pagination-Limit')
		return int(actual) if actual else requested
	except Exception:
		return requested

def _get_pages(path, params=None, limit=100, max_pages=40, user=True):
	base = dict(params or {})
	rows = []
	for page in range(1, max_pages + 1):
		query = dict(base)
		query['page'] = page
		query['limit'] = limit
		payload, headers = _request('GET', path, params=query, user=user)
		if isinstance(payload, dict) and payload.get('_locked'):
			if page == 1: kodi_utils.notification('This WeTrakr list is over the plan limit', 4000)
			break
		batch = _rows(payload)
		if not batch: break
		rows.extend(batch)
		total_pages = _page_count(headers)
		if total_pages and page >= total_pages: break
		if len(batch) < _page_limit(headers, limit): break
	return rows

def _get_pages_checked(path, params=None, limit=100, max_pages=40):
	"""None unless every page arrived. A short list must not replace local ticks."""
	base = dict(params or {})
	rows = []
	for page in range(1, max_pages + 1):
		query = dict(base)
		query['page'] = page
		query['limit'] = limit
		payload, headers = _request('GET', path, params=query)
		if payload is None:
			payload, headers = _request('GET', path, params=query)
		if payload is None or (isinstance(payload, dict) and (payload.get('_locked') or payload.get('_missing'))):
			kodi_utils.logger('WeTrakr', 'page %s failed for %s; local ticks kept' % (page, path))
			return None
		batch = _rows(payload)
		if not batch: return rows
		rows.extend(batch)
		total_pages = _page_count(headers)
		if total_pages and page >= total_pages: return rows
		if len(batch) < _page_limit(headers, limit): return rows
	kodi_utils.logger('WeTrakr', 'page cap %s hit for %s; local ticks kept' % (max_pages, path))
	return None

def _external_id(ids, key):
	if not isinstance(ids, dict): return None
	value = ids.get(key)
	if isinstance(value, dict): value = value.get('id')
	if value in (None, '', 'None', 0, '0'): return None
	if key == 'imdb':
		text = str(value).strip()
		return text if text.startswith('tt') else None
	parsed = _parse_int(value)
	return parsed if parsed else (str(value).strip() or None)

def _kind_target(media_kind):
	return 'movies' if media_kind in ('movies', 'movie') else 'shows'

def _list_item_kind(entry):
	raw = str(entry.get('type') or entry.get('media_type') or '').lower()
	if raw in ('movie', 'movies'): return 'movie'
	if raw in ('show', 'shows', 'tv', 'tvshow', 'series'): return 'show'
	return ''

def _media_rows(entries):
	rows = []
	for entry in entries or []:
		if not isinstance(entry, dict): continue
		ids = entry.get('ids') or {}
		tmdb, imdb, tvdb = _external_id(ids, 'tmdb'), _external_id(ids, 'imdb'), _external_id(ids, 'tvdb')
		if not tmdb and not imdb: continue
		media_ids = {}
		if tmdb: media_ids['tmdb'] = tmdb
		if imdb: media_ids['imdb'] = imdb
		if tvdb: media_ids['tvdb'] = tvdb
		row = {
			'media_ids': media_ids,
			'title': entry.get('title') or '',
			'release_date': entry.get('release_date') or entry.get('released') or '',
			'date_added': entry.get('list_item_created_at') or '',
			'order': len(rows),
		}
		kind = _list_item_kind(entry)
		if kind: row['type'] = kind
		rows.append(row)
	return rows

def _finish(rows, list_key):
	from modules import list_sort
	return list_sort.sort_user_list(rows, list_key) or []

def _status_list(status, media_kind):
	if not wetrakr_lists_active(): return []
	target = _kind_target(media_kind)
	entries = _get_pages('/sync/tracking/%s/%s' % (status, target))
	return _finish(_media_rows(entries), 'wetrakr.%s:%s' % (status, target))

def wetrakr_plantowatch(media_kind, page_no=None):
	return _status_list('planning', media_kind)

def wetrakr_watching(media_kind, page_no=None):
	return _status_list('watching', media_kind)

def wetrakr_waiting(media_kind, page_no=None):
	return _status_list('waiting', media_kind)

def wetrakr_playing(media_kind, page_no=None):
	return _status_list('playing', media_kind)

def wetrakr_paused(media_kind, page_no=None):
	return _status_list('paused', media_kind)

def wetrakr_watched(media_kind, page_no=None):
	return _status_list('watched', media_kind)

def wetrakr_dropped(media_kind, page_no=None):
	return _status_list('dropped', media_kind)

def wetrakr_favorites(media_kind, page_no=None):
	if not wetrakr_lists_active(): return []
	target = _kind_target(media_kind)
	entries = _get_pages('/sync/favorites/%s' % target)
	return _finish(_media_rows(entries), 'wetrakr.favorites:%s' % target)

def wetrakr_get_lists():
	if not wetrakr_lists_active(): return []
	rows = [i for i in _rows(_api('GET', '/sync/lists', params={'sort': 'name', 'dir': 'asc'})) if isinstance(i, dict) and i.get('id')]
	rows.sort(key=lambda item: (item.get('name') or '').lower())
	return rows

def wetrakr_list_items(list_id, media_kind='movies'):
	if not list_id or not wetrakr_lists_active(): return []
	target = _kind_target(media_kind)
	query = {'sort_by': 'editor_rank', 'sort_dir': 'asc'}
	entries = _get_pages('/lists/%s/items/%s' % (list_id, target), query)
	if not entries:
		wanted = 'movie' if target == 'movies' else 'show'
		mixed = _get_pages('/sync/lists/%s/items' % list_id, query)
		entries = [i for i in mixed if (i.get('type') or '').lower() in (wanted, target)]
	return _media_rows(entries)

def wetrakr_public_mixed_list_items(list_id):
	"""Movies and shows from someone else's public list, in that list's order. No sign-in."""
	if not list_id: return []
	query = {'sort_by': 'editor_rank', 'sort_dir': 'asc'}
	combined = []
	for target, media_type in (('movies', 'movie'), ('shows', 'show')):
		entries = _get_pages('/lists/%s/items/%s' % (list_id, target), query, user=False)
		for entry in entries:
			rows = _media_rows([entry])
			if not rows: continue
			row = rows[0]
			row['type'] = media_type
			row['order'] = len(combined)
			combined.append(row)
	return combined

def wetrakr_mixed_list_items(list_id):
	"""Movies and shows from one of your lists, in that list's order."""
	if not list_id or not wetrakr_lists_active(): return []
	query = {'sort_by': 'editor_rank', 'sort_dir': 'asc'}
	rows = _media_rows(_get_pages('/sync/lists/%s/items' % list_id, query))
	if rows and all(row.get('type') in ('movie', 'show') for row in rows): return rows
	movies = wetrakr_list_items(list_id, 'movies')
	shows = wetrakr_list_items(list_id, 'shows')
	for row in movies: row['type'] = 'movie'
	for row in shows: row['type'] = 'show'
	combined = movies + shows
	for index, row in enumerate(combined):
		row['order'] = index
	return combined

def _iana_tz():
	try:
		from datetime import datetime
		name = getattr(datetime.now().astimezone().tzinfo, 'key', None)
		if name and '/' in str(name): return str(name)
	except Exception:
		pass
	return 'UTC'

def _calendar_window_params(kind, mode='discover'):
	start, end = settings.calendar_day_window()
	params = {
		'start': start.strftime('%Y-%m-%d'),
		'end': end.strftime('%Y-%m-%d'),
		'mode': mode if mode in ('discover', 'following') else 'discover',
	}
	if kind == 'movies':
		params['region'] = settings.mpaa_region() or 'US'
	else:
		params['tz'] = _iana_tz()
	return params

def _calendar_rows(kind, mode='discover', user=False):
	path = '/calendar/%s' % kind
	payload = _api('GET', path, params=_calendar_window_params(kind, mode), user=user)
	if not isinstance(payload, dict): return []
	rows = []
	for day, items in (payload.get('days') or {}).items():
		if not isinstance(items, list): continue
		for item in items:
			if isinstance(item, dict): rows.append((str(day), item))
	return rows

def _cap_calendar_rows(rows, date_of):
	try: max_items = settings.public_calendar_max_items()
	except Exception: max_items = 250
	if len(rows) <= max_items: return rows
	from datetime import datetime
	from modules.utils import get_datetime
	today = get_datetime()

	def _distance(row):
		raw = str(date_of(row) or '')[:10]
		try: day = datetime.strptime(raw, '%Y-%m-%d').date()
		except Exception: return 99999
		return abs((day - today).days)

	return sorted(rows, key=_distance)[:max_items]

def _calendar_tmdb(item, kind='shows'):
	"""TMDb id embedded on a calendar row (API 1.0.8+). Episode rows must use the show block only."""
	if not isinstance(item, dict): return None
	if kind == 'shows':
		show = item.get('show')
		if isinstance(show, dict):
			parsed = _external_id(show.get('ids') or {}, 'tmdb')
			if not parsed:
				parsed = _parse_int(show.get('tmdb_id') or show.get('tmdb'))
			if parsed: return parsed
		return None
	parsed = _external_id(item.get('ids') or {}, 'tmdb')
	if parsed: return parsed
	for key in ('tmdb_id', 'tmdb'):
		parsed = _parse_int(item.get(key))
		if parsed: return parsed
	return None

def _calendar_catalog_id(item, kind='shows'):
	"""WeTrakr catalog id for a row still missing embedded TMDb (fallback detail lookups)."""
	if not isinstance(item, dict): return None
	if kind == 'shows':
		show = item.get('show')
		if isinstance(show, dict):
			parsed = _parse_int(show.get('id'))
			if parsed: return parsed
		return _parse_int(item.get('show_id') or item.get('media_id'))
	return _parse_int(item.get('id') or item.get('media_id'))

def _remember_wetrakr_tmdb(kind, catalog_id, tmdb):
	if not catalog_id or not tmdb: return
	try:
		from caches.main_cache import main_cache
		main_cache.set('WETRAKR_TMDB_%s_%s' % (kind, catalog_id), tmdb, expiration=720)
	except Exception: pass

def _ids_nearest_today(rows, id_of, date_of, kind='shows'):
	"""Catalog ids still needing a TMDb match, nearest calendar day first."""
	from datetime import datetime
	from modules.utils import get_datetime
	today = get_datetime()
	best = {}
	for day, item in rows:
		if _calendar_tmdb(item, kind): continue
		wid = id_of(item)
		if not wid: continue
		raw = str(date_of(day, item) or '')[:10]
		try: distance = abs((datetime.strptime(raw, '%Y-%m-%d').date() - today).days)
		except Exception: distance = 99999
		if wid not in best or distance < best[wid]: best[wid] = distance
	return [wid for wid, _distance in sorted(best.items(), key=lambda pair: (pair[1], pair[0]))]

def _tmdb_for_wetrakr_ids(kind, wetrakr_ids):
	"""Map WeTrakr catalog ids to TMDb. Catalog details are the only place the TMDb id is sent.

	A public calendar can be hundreds of titles. This waits a short time, keeps every id it
	matched, and leaves the rest for the next open. A failed lookup is not stored.
	"""
	from caches.main_cache import main_cache
	found, missing = {}, []
	for wid in wetrakr_ids:
		key = 'WETRAKR_TMDB_%s_%s' % (kind, wid)
		cached = main_cache.get(key)
		if cached is None:
			missing.append(wid)
		elif cached not in (0, '0', '', None):
			parsed = _parse_int(cached)
			if parsed: found[wid] = parsed
	if not missing: return found, False
	lock = threading.Lock()
	stop = threading.Event()
	pending = list(missing)

	def _store(wid, tmdb):
		hours = 720 if tmdb else 24
		try: main_cache.set('WETRAKR_TMDB_%s_%s' % (kind, wid), tmdb or 0, expiration=hours)
		except Exception: pass
		if tmdb:
			with lock: found[wid] = tmdb

	def _worker():
		while not stop.is_set():
			with lock:
				if not pending: return
				wid = pending.pop(0)
			payload = _api('GET', '/%s/%s' % (kind, wid), user=False, timeout=8)
			if not isinstance(payload, dict) or payload.get('_locked'): continue
			tmdb = None if payload.get('_missing') else _external_id(payload.get('ids') or {}, 'tmdb')
			_store(wid, tmdb)

	threads = []
	for _index in range(min(8, len(missing))):
		thread = threading.Thread(target=_worker, daemon=True)
		thread.start()
		threads.append(thread)
	deadline = time.time() + 20
	finished = False
	while time.time() < deadline:
		with lock: waiting = bool(pending)
		if not waiting and all(not thread.is_alive() for thread in threads):
			finished = True
			break
		time.sleep(0.05)
	stop.set()
	with lock:
		snapshot = dict(found)
		left = bool(pending) or any(thread.is_alive() for thread in threads)
	return snapshot, (not finished and left)

def _note_calendar_partial(incomplete):
	if not incomplete or kodi_utils.external(): return
	kodi_utils.notification('Open again to load the rest of this WeTrakr calendar', 4000)

def wetrakr_get_public_calendar(mode='discover'):
	"""Episode calendar. discover is public. following is the signed-in user's titles."""
	user = mode == 'following'
	if user and not wetrakr_user_active(): return []
	rows = _cap_calendar_rows(
		_calendar_rows('shows', mode, user=user),
		lambda row: (row[1].get('air_datetime') or row[1].get('air_date') or row[0]))
	show_ids = _ids_nearest_today(
		rows, lambda item: _calendar_catalog_id(item, 'shows'),
		lambda day, item: item.get('air_datetime') or item.get('air_date') or day, 'shows')
	tmdb_by_id, incomplete = _tmdb_for_wetrakr_ids('shows', show_ids)
	_note_calendar_partial(incomplete)
	data = []
	for day, item in rows:
		wid = _calendar_catalog_id(item, 'shows')
		tmdb = _calendar_tmdb(item, 'shows') or tmdb_by_id.get(wid)
		if tmdb and wid: _remember_wetrakr_tmdb('shows', wid, tmdb)
		season = _parse_int(item.get('season_number'))
		episode = _parse_int(item.get('number'))
		if not tmdb or not season or not episode: continue
		show = item.get('show') if isinstance(item.get('show'), dict) else {}
		title = show.get('title') or 'Show'
		aired = str(item.get('air_datetime') or item.get('air_date') or day or '').strip()
		if not aired: continue
		data.append({
			'sort_title': '%s s%s e%s' % (title, str(season).zfill(2), str(episode).zfill(2)),
			'media_ids': {'tmdb': tmdb},
			'season': season,
			'episode': episode,
			'first_aired': aired,
		})
	kodi_utils.logger('WeTrakr', 'public calendar %s: %s rows → %s kept' % (mode, len(rows), len(data)))
	return data

def wetrakr_movie_calendar(mode='discover'):
	"""Movie calendar for the same day window. following needs a signed-in account."""
	user = mode == 'following'
	if user and not wetrakr_user_active(): return []
	rows = _cap_calendar_rows(_calendar_rows('movies', mode, user=user), lambda row: (row[1].get('release_date') or row[0]))
	movie_ids = _ids_nearest_today(
		rows, lambda item: _calendar_catalog_id(item, 'movies'),
		lambda day, item: item.get('release_date') or day, 'movies')
	tmdb_by_id, incomplete = _tmdb_for_wetrakr_ids('movies', movie_ids)
	_note_calendar_partial(incomplete)
	data = []
	for day, item in rows:
		wid = _calendar_catalog_id(item, 'movies')
		tmdb = _calendar_tmdb(item, 'movies') or tmdb_by_id.get(wid)
		if tmdb and wid: _remember_wetrakr_tmdb('movies', wid, tmdb)
		if not tmdb: continue
		data.append({'media_ids': {'tmdb': tmdb}, 'release_date': str(item.get('release_date') or day or '')[:10]})
	newest_first = settings.calendar_sort_order() == 0
	data.sort(key=lambda item: item.get('release_date') or '', reverse=newest_first)
	seen, unique = set(), []
	for item in data:
		tmdb = item['media_ids']['tmdb']
		if tmdb in seen: continue
		seen.add(tmdb)
		unique.append(item)
	return unique

_DISCOVER_PATHS = {
	'wetrakr_trending': '/discover/trending',
	'wetrakr_popular': '/discover/popular',
	'wetrakr_toprated': '/discover/toprated',
}

def wetrakr_feed(action, media_kind, page_no=1, query=None, list_id=None):
	"""One page of a public discover feed, catalog search, or public list."""
	target = _kind_target(media_kind)
	kind = 'movie' if target == 'movies' else 'show'
	try: page = max(int(page_no or 1), 1)
	except Exception: page = 1
	user = False
	if action == 'wetrakr_search':
		path = '/search'
		params = {'q': query or '', 'filter_type': kind, 'page': page, 'limit': 20}
	elif action == 'wetrakr_public_list':
		path = '/lists/%s/items/%s' % (list_id, target)
		params = {'sort_by': 'editor_rank', 'sort_dir': 'asc', 'page': page, 'limit': 100}
	else:
		path = _DISCOVER_PATHS.get(action)
		if not path: return [], 1
		params = {'filter_type': kind, 'page': page, 'limit': 20}
	payload, headers = _request('GET', path, params=params, user=user)
	if isinstance(payload, dict) and payload.get('_locked'):
		kodi_utils.notification('This WeTrakr list is over the plan limit', 4000)
		return [], 1
	rows = payload if isinstance(payload, list) else _rows(payload)
	try: pages = int(headers.get('X-Pagination-Page-Count') or 1)
	except Exception: pages = 1
	return _media_rows(rows), max(pages, 1)

def wetrakr_public_lists(kind='trending', query=None, page_no=1):
	"""Other people's public lists. Trending and latest need no account. Search uses the catalog.

	Page size matches Trakt public user lists (50). Next page uses X-Pagination-Page-Count when
	present; otherwise a full page of results still offers another page.
	"""
	try: page = max(int(page_no or 1), 1)
	except Exception: page = 1
	limit = 50
	if query:
		path, params = '/search', {'q': query, 'filter_type': 'list', 'page': page, 'limit': limit}
	elif kind == 'latest':
		path, params = '/lists/last', {'page': page, 'limit': limit}
	else:
		path, params = '/lists/trending', {'page': page, 'limit': limit}
	payload, headers = _request('GET', path, params=params, user=False)
	rows = payload if isinstance(payload, list) else _rows(payload)
	out = []
	for item in rows:
		if not isinstance(item, dict) or not item.get('id') or item.get('locked'): continue
		out.append({'id': item.get('id'), 'name': item.get('name') or 'List'})
	try: pages = int(headers.get('X-Pagination-Page-Count') or 0)
	except Exception: pages = 0
	if pages > 0:
		return out, max(pages, 1)
	# Header missing: still offer Next Page when this page was full (same idea as Trakt always paging).
	if len(out) >= limit:
		return out, page + 1
	return out, page

def _write_item(tmdb_id, imdb_id, status=None):
	ids = {}
	tmdb = _parse_int(tmdb_id)
	imdb = str(imdb_id).strip() if imdb_id not in (None, '', 'None', 'empty_setting') else ''
	if imdb and not imdb.startswith('tt'): imdb = ''
	if tmdb: ids['tmdb'] = tmdb
	if imdb: ids['imdb'] = imdb
	if not ids: return None
	item = {'ids': ids}
	if status: item['status'] = status
	return item

def _batch_ok(payload):
	if payload is None or payload is False: return False
	if not isinstance(payload, dict): return True
	if payload.get('_locked') or payload.get('_missing'): return False
	not_found = payload.get('notFound') or {}
	if isinstance(not_found, dict):
		for items in not_found.values():
			if items: return False
	return True

def _plays_removed_ok(payload):
	"""remove/all: a removed block worked. Already clear is success. Any other error left the plays."""
	if not _batch_ok(payload) or not isinstance(payload, dict): return False
	if payload.get('removed'): return True
	errored = payload.get('errored') or {}
	if not isinstance(errored, dict): return False
	saw = False
	for items in errored.values():
		if not items: continue
		if not isinstance(items, list): return False
		for item in items:
			if not isinstance(item, dict) or item.get('error') != 'Nothing to remove': return False
			saw = True
	return saw

def _remove_watched_plays(media_type, tmdb_id, imdb_id):
	item = _write_item(tmdb_id, imdb_id, 'watched')
	if not item: return False
	key = 'movies' if media_type == 'movie' else 'shows'
	return _plays_removed_ok(_api('POST', '/sync/tracking/remove/all', data={key: [item]}))

def _post_group(path, media_type, tmdb_id, imdb_id, status=None):
	item = _write_item(tmdb_id, imdb_id, status)
	if not item: return False
	key = 'movies' if media_type == 'movie' else 'shows'
	return _batch_ok(_api('POST', path, data={key: [item]}))

def _same_media(entry, tmdb_id, imdb_id):
	ids = entry.get('ids') if isinstance(entry, dict) else None
	tmdb = _external_id(ids or {}, 'tmdb')
	imdb = _external_id(ids or {}, 'imdb')
	wanted_tmdb = _parse_int(tmdb_id)
	if tmdb and wanted_tmdb and int(tmdb) == wanted_tmdb: return True
	wanted_imdb = str(imdb_id or '').strip()
	return bool(imdb and wanted_imdb.startswith('tt') and imdb == wanted_imdb)

def _tracking_has(status, media_type, tmdb_id, imdb_id, title):
	target = 'movies' if media_type == 'movie' else 'shows'
	params = {'page': 1, 'limit': 50}
	if title: params['title_search'] = title
	for entry in _rows(_api('GET', '/sync/tracking/%s/%s' % (status, target), params=params)):
		if _same_media(entry, tmdb_id, imdb_id): return True
	return False

def _favorite_has(media_type, tmdb_id, imdb_id):
	target = 'movies' if media_type == 'movie' else 'shows'
	for page in range(1, 4):
		rows = _rows(_api('GET', '/sync/favorites/%s' % target, params={'page': page, 'limit': 100}))
		for entry in rows:
			if _same_media(entry, tmdb_id, imdb_id): return True
		if len(rows) < 100: break
	return False

def _resolve_media(tmdb_id, media_type):
	tmdb = _parse_int(tmdb_id)
	if not tmdb: return None
	kind = 'movie' if media_type == 'movie' else 'show'
	data = _api('GET', '/media/external/tmdb/%s' % tmdb, params={'type': kind})
	if not isinstance(data, dict): return None
	return data.get('id') or None

def _lists_for_item(media_type, item_id):
	target = 'movie' if media_type == 'movie' else 'show'
	data = _api('GET', '/sync/lists/item/%s/%s' % (target, item_id))
	if not isinstance(data, list): return []
	rows = [i for i in data if isinstance(i, dict) and i.get('id') and not i.get('locked')]
	rows.sort(key=lambda item: (item.get('name') or '').lower())
	return rows

def wetrakr_item_in_planning(media_type, tmdb_id, imdb_id, title=''):
	return _tracking_has('planning', media_type, tmdb_id, imdb_id, title)

def wetrakr_toggle_planning(media_type, tmdb_id, imdb_id, add):
	if add:
		ok = _post_group('/sync/tracking', media_type, tmdb_id, imdb_id, 'planning')
	else:
		ok = _post_group('/sync/tracking/remove', media_type, tmdb_id, imdb_id, 'planning')
	if ok:
		kodi_utils.kodi_refresh()
		return kodi_utils.notify_success()
	return kodi_utils.notify_error()

def wetrakr_lists_with_media(media_type, tmdb_id, imdb_id):
	item_id = _resolve_media(tmdb_id, media_type)
	if not item_id: return []
	return [row for row in _lists_for_item(media_type, item_id) if row.get('included')]

_MOVIE_STATUSES = (('planning', 'Plan to Watch'), ('watched', 'Watched'), ('dropped', 'Dropped'))
_SHOW_STATUSES = (
	('planning', 'Plan to Watch'), ('watching', 'Watching'), ('paused', 'Paused'),
	('watched', 'Watched'), ('dropped', 'Dropped'),
)

def wetrakr_manager_choice(params):
	if not wetrakr_lists_active():
		return kodi_utils.notification('No Active WeTrakr Account', 3500)
	media_type = params.get('media_type') or params.get('content') or 'movie'
	if media_type not in ('movie', 'tvshow'): media_type = 'tvshow'
	icon = params.get('icon') or _wetrakr_icon()
	tmdb_id, imdb_id = params.get('tmdb_id'), params.get('imdb_id')
	title = params.get('title') or ''
	statuses = _MOVIE_STATUSES if media_type == 'movie' else _SHOW_STATUSES
	choices = []
	for status, label in statuses:
		if _tracking_has(status, media_type, tmdb_id, imdb_id, title):
			choices.append(('Remove from [B]%s[/B]' % label, 'remove_status:%s' % status))
		else:
			choices.append(('Add to [B]%s[/B]' % label, 'add_status:%s' % status))
	if _favorite_has(media_type, tmdb_id, imdb_id):
		choices.append(('Remove from [B]Favorites[/B]', 'remove_favorite'))
	else:
		choices.append(('Add to [B]Favorites[/B]', 'add_favorite'))
	item_id = _resolve_media(tmdb_id, media_type)
	for entry in _lists_for_item(media_type, item_id) if item_id else []:
		name = entry.get('name') or 'List'
		if entry.get('included'):
			choices.append(('Remove from [B]%s[/B]' % name, 'remove_list:%s' % entry.get('id')))
		else:
			choices.append(('Add to [B]%s[/B]' % name, 'add_list:%s' % entry.get('id')))
	choices.append(('Open [B]WeTrakr Lists[/B]', 'open_lists'))
	choices.append(('Refresh Widgets', 'refresh'))
	list_items = [{'line1': item[0], 'icon': icon} for item in choices]
	choice = kodi_utils.select_dialog([i[1] for i in choices], **{'items': json.dumps(list_items), 'heading': 'WeTrakr Lists Manager'})
	if choice is None: return
	if choice == 'refresh':
		kodi_utils.kodi_refresh()
		return kodi_utils.notification('Widgets Refreshed', 2500)
	if choice == 'open_lists':
		return kodi_utils.container_update({'mode': 'navigator.wetrakr_lists'})
	ok = False
	if choice == 'add_favorite':
		ok = _post_group('/sync/favorites', media_type, tmdb_id, imdb_id)
	elif choice == 'remove_favorite':
		ok = _post_group('/sync/favorites/remove', media_type, tmdb_id, imdb_id)
	elif choice.startswith('add_status:'):
		ok = _post_group('/sync/tracking', media_type, tmdb_id, imdb_id, choice.split(':', 1)[1])
	elif choice.startswith('remove_status:'):
		status = choice.split(':', 1)[1]
		if status == 'watched':
			ok = _remove_watched_plays(media_type, tmdb_id, imdb_id)
		else:
			ok = _post_group('/sync/tracking/remove', media_type, tmdb_id, imdb_id, status)
	elif choice.startswith('add_list:'):
		ok = _post_group('/sync/lists/%s/items' % choice.split(':', 1)[1], media_type, tmdb_id, imdb_id)
	elif choice.startswith('remove_list:'):
		ok = _post_group('/sync/lists/%s/items/remove' % choice.split(':', 1)[1], media_type, tmdb_id, imdb_id)
	if ok:
		kodi_utils.kodi_refresh()
		return kodi_utils.notify_success()
	return kodi_utils.notify_error()

# ---------- Watched indicator (provider 5 only) ----------

def wetrakr_official_status(media_type=None):
	return wetrakr_lists_active()

def wetrakr_scrobble_deferred():
	"""True when script.wetrakr is installed, enabled, authorised, and sending plays."""
	addon_id = 'script.wetrakr'
	if not kodi_utils.addon_installed(addon_id) or not kodi_utils.addon_enabled(addon_id): return False
	try: inst = kodi_utils.addon(addon_id)
	except Exception: return False
	try:
		if str(inst.getSetting('auth_invalid') or '').strip().lower() == 'true': return False
		token = str(inst.getSetting('api_token') or '').strip()
	except Exception:
		return False
	if not token or token in ('empty_setting', '0'): return False
	for key in ('track_watched', 'track_playing'):
		try:
			if str(inst.getSetting(key) or '').strip().lower() in ('true', '1', 'yes', 'on'): return True
		except Exception:
			pass
	return False

def _scrobble_body(media_type, tmdb_id, percent, season=None, episode=None, title='', year=None):
	try: progress = float(percent or 0)
	except: progress = 0
	try: tid = int(tmdb_id)
	except: return None
	year_int = None
	try:
		if year not in (None, '', 'None'): year_int = int(year)
	except: year_int = None
	if year_int in (0, 2050): year_int = None
	if media_type == 'movie':
		movie = {'ids': {'tmdb': tid}}
		if title: movie['title'] = title
		if year_int: movie['year'] = year_int
		body = {'movie': movie}
	else:
		show = {'ids': {'tmdb': tid}}
		if title: show['title'] = title
		if year_int: show['year'] = year_int
		body = {'show': show, 'episode': {'season': int(season or 0), 'number': int(episode or 0)}}
	body['progress'] = progress
	body['app_version'] = kodi_utils.addon_version()
	return body

def _scrobble_identity(media_type, tmdb_id, season=None, episode=None, title='', year=None):
	body = _scrobble_body(media_type, tmdb_id, 0, season, episode, title, year)
	if not body: return None
	body.pop('progress', None)
	body.pop('app_version', None)
	return body

def wetrakr_scrobble_cancel(media_type, tmdb_id, season=None, episode=None, title='', year=None):
	"""Drop the playing session without logging a play. Already gone is success."""
	if not wetrakr_official_status(media_type): return False
	body = _scrobble_identity(media_type, tmdb_id, season, episode, title, year)
	if not body: return False
	payload = _api('DELETE', '/scrobble/playing', data=body)
	if payload is None: return False
	if isinstance(payload, dict) and payload.get('_missing'): return True
	return _batch_ok(payload)

def wetrakr_scrobble(action, media_type, tmdb_id, percent=0, season=None, episode=None, title='', year=None):
	"""Start, pause, or stop. Stop records a history play at any progress.

	Progress ticks reuse start, which moves an existing session. Callers must
	not send stop unless this playback should count as watched.
	"""
	if not wetrakr_official_status(media_type) or wetrakr_scrobble_deferred(): return False
	path = {'start': '/scrobble/start', 'pause': '/scrobble/pause', 'stop': '/scrobble/stop', 'progress': '/scrobble/start'}.get(action)
	if not path: return False
	body = _scrobble_body(media_type, tmdb_id, percent, season, episode, title, year)
	if not body: return False
	return _batch_ok(_api('POST', path, data=body))

def _tracking_tree(status, media_type, tmdb_id, season=None, episode=None):
	try: tid = int(tmdb_id)
	except: return None
	if media_type == 'movie':
		return {'movies': [{'ids': {'tmdb': tid}, 'status': status}]}
	if media_type == 'episode' and season is not None and episode is not None:
		return {'shows': [{'ids': {'tmdb': tid}, 'seasons': [{'number': int(season), 'episodes': [{'number': int(episode), 'status': status}]}]}]}
	if media_type == 'season' and season is not None:
		return {'shows': [{'ids': {'tmdb': tid}, 'seasons': [{'number': int(season), 'status': status}]}]}
	return {'shows': [{'ids': {'tmdb': tid}, 'status': status}]}

def wetrakr_watched_status_mark(action, media_type, tmdb_id, tvdb_id=0, season=None, episode=None, title=None, year=None):
	if not wetrakr_official_status(media_type): return False
	body = _tracking_tree('watched', media_type, tmdb_id, season, episode)
	if not body: return False
	if action == 'mark_as_watched':
		body['allow_rewatch'] = True
		ok = _batch_ok(_api('POST', '/sync/tracking', data=body))
	else:
		ok = _plays_removed_ok(_api('POST', '/sync/tracking/remove/all', data=body))
	if ok:
		try: kodi_utils.set_property('redlight.wetrakr_skip_list_sync', 'true')
		except Exception: pass
	return ok

def wetrakr_progress(action, media_type, tmdb_id, percent, season=None, episode=None, resume_id=None, refresh_wetrakr=False):
	if not wetrakr_official_status(media_type): return False
	if action == 'clear_progress':
		ok = wetrakr_scrobble_cancel(media_type, tmdb_id, season, episode)
		body = _tracking_tree('playing', media_type, tmdb_id, season, episode)
		if body: _api('POST', '/sync/tracking/remove', data=body)
	else:
		ok = wetrakr_scrobble('pause', media_type, tmdb_id, percent, season, episode)
	if refresh_wetrakr: wetrakr_sync_activities(force_update=True)
	return ok

def wetrakr_get_dropped_items():
	ids = []
	for kind in ('shows', 'movies'):
		for item in wetrakr_dropped(kind) or []:
			tmdb = _parse_int((item.get('media_ids') or {}).get('tmdb'))
			if tmdb: ids.append(tmdb)
	return ids

def _up_next_count(value):
	try:
		result = int(value)
		return result if result >= 0 else None
	except Exception:
		return None

def wetrakr_up_next_episodes():
	"""Episode WeTrakr has lined up for watching and paused shows.

	None when the list could not be read. An empty list means nothing is up next.
	Watching is kept ahead of paused when a show is in both.
	"""
	now = time.time()
	age = now - _UP_NEXT_CACHE.get('at', 0)
	if _UP_NEXT_CACHE.get('ok') and age < _UP_NEXT_TTL:
		return _UP_NEXT_CACHE.get('rows')
	if _UP_NEXT_CACHE.get('at') and not _UP_NEXT_CACHE.get('ok') and age < _UP_NEXT_FAIL_TTL:
		return None
	rows, seen = [], set()
	for status in ('watching', 'paused'):
		batch = _get_pages_checked('/sync/tracking/%s/shows' % status, {'extended': 'episode_level_1'})
		if batch is None:
			_UP_NEXT_CACHE['at'] = now
			_UP_NEXT_CACHE['ok'] = False
			_UP_NEXT_CACHE['rows'] = None
			return None
		for item in batch:
			episode = item.get('next_watching_episode')
			if not isinstance(episode, dict): continue
			tmdb = _parse_int(_external_id(item.get('ids') or {}, 'tmdb'))
			season = _up_next_count(episode.get('season_number'))
			if season is None: season = _up_next_count(episode.get('season'))
			number = episode.get('number')
			if number is None: number = episode.get('episode')
			number = _up_next_count(number)
			if not tmdb or season is None or number is None or tmdb in seen: continue
			seen.add(tmdb)
			rows.append({
				'media_ids': {'tmdb': tmdb},
				'season': season,
				'episode': number,
				'title': item.get('title') or '',
				'wetrakr_next': True,
			})
	_UP_NEXT_CACHE['at'] = now
	_UP_NEXT_CACHE['ok'] = True
	_UP_NEXT_CACHE['rows'] = rows
	return rows

def wetrakr_up_next_token():
	"""Stable fingerprint of the up-next episodes. A failed read keeps the last good one."""
	rows = wetrakr_up_next_episodes()
	if rows is None:
		try:
			from caches.wetrakr_cache import wetrakr_cache
			saved = wetrakr_cache.get('up_next_token')
			if isinstance(saved, str) and saved.startswith('up:'): return saved
		except Exception: pass
		return 'up:'
	parts = sorted('%s:%s:%s' % (i['media_ids']['tmdb'], i['season'], i['episode']) for i in rows)
	token = 'up:' + ','.join(parts)
	try:
		from caches.wetrakr_cache import wetrakr_cache
		wetrakr_cache.set('up_next_token', token)
	except Exception: pass
	return token

def wetrakr_hide_unhide_progress_items(params):
	action, media_id = params.get('action'), params.get('media_id')
	media_type = 'tvshow' if params.get('media_type') in ('shows', 'tvshow', 'episode') else 'movie'
	status_action = 'add' if action == 'drop' else 'undrop'
	# Dropped shows stay out of Next Episodes. Undrop puts them back on Watching is not required;
	# removing the dropped flag is enough for the hidden-progress list.
	if status_action == 'add':
		return _post_group('/sync/tracking', 'tvshow' if media_type != 'movie' else 'movie', media_id, None, 'dropped')
	return _post_group('/sync/tracking/remove', 'tvshow' if media_type != 'movie' else 'movie', media_id, None, 'dropped')

def _node_tmdb(node):
	if not isinstance(node, dict): return None
	ids = node.get('ids') if isinstance(node.get('ids'), dict) else {}
	return _parse_int(_external_id(ids, 'tmdb') or node.get('tmdb') or node.get('tmdb_id'))

def _pagination_next(headers):
	try: return str((headers or {}).get('X-Pagination-Next') or '').strip()
	except Exception: return ''

def _history_rows(target, progress=None, label='', span=(0, 100), from_date=None):
	"""Compact history, walked by cursor.

	Pass from_date (ISO-8601 UTC string) for an incremental sync — only rows created after that
	timestamp are returned.  Cursor pagination is suppressed when from_date is set because a
	single 5,000-row page comfortably covers one sync window and mixing cursor + date-filter
	parameters is avoided.  Omit from_date for a full download (first connect or force-sync).
	"""
	path = '/sync/tracking/watched/history/%s' % target
	rows, after = [], None
	start, end = span
	for page in range(1, 41):
		kodi_utils.sync_progress_page(progress, label, start, end, page)
		params = {'compact': 'true', 'limit': 5000}
		if after: params['after'] = after
		if from_date: params['from_date'] = from_date
		payload, headers = _request('GET', path, params=params, timeout=60)
		if payload is None:
			payload, headers = _request('GET', path, params=params, timeout=60)
		if payload is None or (isinstance(payload, dict) and (payload.get('_locked') or payload.get('_missing'))):
			kodi_utils.logger('WeTrakr', 'history page failed for %s; local ticks kept' % target)
			return None
		batch = _rows(payload)
		if not batch: return rows
		rows.extend(batch)
		after = _pagination_next(headers)
		if from_date: after = None  # one page covers one sync window; don't mix cursor with date filter
		if not after:
			kodi_utils.sync_progress(progress, label, end)
			return rows
	kodi_utils.logger('WeTrakr', 'history cursor cap hit for %s; local ticks kept' % target)
	return None

def _show_tmdb_id(show_id, cache):
	"""WeTrakr show id to TMDb.

	Checks a 30-day persistent cache (main_cache key WETRAKR_TMDB_shows_<id>) before hitting the
	API, matching the convention used by _tmdb_for_wetrakr_ids for calendar lookups.
	False is returned for transient API failures and is NOT written to the persistent cache so the
	next sync retries the lookup automatically.
	"""
	key = str(show_id)
	if key in cache: return cache[key]
	try:
		from caches.main_cache import main_cache as _mc
		cached = _mc.get('WETRAKR_TMDB_shows_%s' % key)
		if cached is not None:
			# 0 / '0' means a prior lookup confirmed no TMDb id for this show
			result = _parse_int(cached) if cached not in (0, '0', False) else None
			cache[key] = result
			return result
	except Exception:
		pass
	data = _api('GET', '/shows/%s' % show_id, timeout=30)
	if data is None:
		# Transient failure — keep in local dict to avoid repeated calls this sync,
		# but do NOT persist so the next sync retries.
		cache[key] = False
		return False
	if isinstance(data, dict) and (data.get('_missing') or data.get('_locked')):
		cache[key] = None
		try:
			from caches.main_cache import main_cache as _mc
			_mc.set('WETRAKR_TMDB_shows_%s' % key, 0, expiration=24)
		except Exception:
			pass
		return None
	tmdb = _node_tmdb(data)
	cache[key] = tmdb
	try:
		from caches.main_cache import main_cache as _mc
		_mc.set('WETRAKR_TMDB_shows_%s' % key, tmdb or 0, expiration=720 if tmdb else 24)
	except Exception:
		pass
	return tmdb

def _history_watched_at(item):
	"""Latest play on a history row. last_watched_at wins when both are sent."""
	if not isinstance(item, dict): return ''
	value = item.get('last_watched_at') or item.get('watched_at') or ''
	return str(value).strip() if value else ''

def _keep_latest_watch(latest, key, row):
	prev = latest.get(key)
	if prev and prev[4] >= row[4]: return
	latest[key] = row

def wetrakr_indicators_movies(history=None, incremental=False):
	if history is None:
		history = _history_rows('movies')
		if history is None: return False
	latest = {}
	for item in history:
		if not isinstance(item, dict): continue
		movie = item.get('movie') if isinstance(item.get('movie'), dict) else item
		tmdb_id = _node_tmdb(movie)
		if not tmdb_id: continue
		watched_at = _history_watched_at(item) or time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
		title = movie.get('title') or item.get('title') or ''
		_keep_latest_watch(latest, tmdb_id, ('movie', str(tmdb_id), '', '', watched_at, title))
	from caches.wetrakr_cache import wetrakr_watched_cache
	if incremental:
		wetrakr_watched_cache.upsert_movie_watched(list(latest.values()))
	else:
		wetrakr_watched_cache.set_bulk_movie_watched(list(latest.values()))
	return True

def wetrakr_indicators_tv(history=None, progress=None, incremental=False):
	if history is None:
		history = _history_rows('episodes', progress, 'Watched episodes', (32, 52))
		if history is None: return False
	lookup_total, seen_ids = 0, set()
	if progress:
		for item in history:
			if not isinstance(item, dict): continue
			episode = item.get('episode') if isinstance(item.get('episode'), dict) else item
			show = episode.get('show') if isinstance(episode.get('show'), dict) else {}
			if _node_tmdb(show): continue
			wetrakr_show = item.get('show_id') or episode.get('show_id')
			if not wetrakr_show: continue
			key = str(wetrakr_show)
			if key in seen_ids: continue
			seen_ids.add(key)
		lookup_total = len(seen_ids)
	latest, shows = {}, {}
	looked, last_pct = 0, -1
	for item in history:
		if not isinstance(item, dict): continue
		episode = item.get('episode') if isinstance(item.get('episode'), dict) else item
		show = episode.get('show') if isinstance(episode.get('show'), dict) else {}
		show_tmdb = _node_tmdb(show)
		if not show_tmdb:
			wetrakr_show = item.get('show_id') or episode.get('show_id')
			if wetrakr_show:
				fresh = str(wetrakr_show) not in shows
				show_tmdb = _show_tmdb_id(wetrakr_show, shows)
				if fresh and progress and lookup_total:
					looked += 1
					pct = 52 + int(36 * looked / float(lookup_total))
					if looked == 1 or looked == lookup_total or pct != last_pct:
						last_pct = pct
						kodi_utils.sync_progress(progress, 'Matching shows, %s of %s' % (looked, lookup_total), pct)
				if show_tmdb is False:
					kodi_utils.logger('WeTrakr', 'show lookup failed; local ticks kept')
					return False
		season = episode.get('season_number', episode.get('season'))
		if season is None: season = item.get('season_number')
		number = episode.get('number', episode.get('episode'))
		if number is None: number = item.get('number')
		if not show_tmdb or season is None or number is None: continue
		try: key = (int(show_tmdb), int(season), int(number))
		except: continue
		watched_at = _history_watched_at(item) or time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
		title = (show.get('title') or episode.get('title') or item.get('title') or '')
		_keep_latest_watch(latest, key, ('episode', str(show_tmdb), int(season), int(number), watched_at, title))
	if progress and not lookup_total:
		kodi_utils.sync_progress(progress, 'Watched episodes', 88)
	from caches.wetrakr_cache import wetrakr_watched_cache
	if incremental:
		wetrakr_watched_cache.upsert_tvshow_watched(list(latest.values()))
	else:
		wetrakr_watched_cache.set_bulk_tvshow_watched(list(latest.values()))
	return True

def wetrakr_refresh_watched_indicators(progress=None, from_date=None):
	incremental = from_date is not None
	movies = _history_rows('movies', progress, 'Watched movies', (8, 32), from_date=from_date)
	episodes = _history_rows('episodes', progress, 'Watched episodes', (32, 52), from_date=from_date)
	if movies is None or episodes is None: return False
	return wetrakr_indicators_movies(movies, incremental=incremental) and wetrakr_indicators_tv(episodes, progress, incremental=incremental)

def _playing_progress_rows(target):
	return _get_pages_checked('/sync/tracking/playing/%s' % target)

def wetrakr_sync_playback(progress=None):
	kodi_utils.sync_progress(progress, 'Movies in progress', 90)
	movies = _playing_progress_rows('movies')
	if movies is None: return False
	kodi_utils.sync_progress(progress, 'Episodes in progress', 95)
	episodes = _playing_progress_rows('episodes')
	if episodes is None: return False
	movie_ins, ep_ins = [], []
	for item in movies:
		try:
			if isinstance(item.get('movie'), dict):
				playback = item.get('playback') if isinstance(item.get('playback'), dict) else {}
				item = dict(item['movie'], playback=item.get('playback') or item['movie'].get('playback'))
			playback = item.get('playback') if isinstance(item.get('playback'), dict) else {}
			percent = float(playback.get('progress_percent') or item.get('progress_percent') or 0)
			if percent < 0: continue
			tmdb_id = _node_tmdb(item)
			if not tmdb_id: continue
			updated = playback.get('tracked_at') or item.get('tracked_at') or ''
			movie_ins.append(('movie', str(tmdb_id), '', '', str(round(percent, 1)), 0, updated, 0, item.get('title') or ''))
		except: pass
	for item in episodes:
		try:
			if isinstance(item.get('episode'), dict):
				item = dict(item['episode'], playback=item.get('playback') or item['episode'].get('playback'))
			playback = item.get('playback') if isinstance(item.get('playback'), dict) else {}
			percent = float(playback.get('progress_percent') or item.get('progress_percent') or 0)
			if percent < 0: continue
			show = item.get('show') if isinstance(item.get('show'), dict) else {}
			show_id = _node_tmdb(show) or _node_tmdb(item)
			season, number = item.get('season_number', item.get('season')), item.get('number', item.get('episode'))
			if not show_id or season is None or number is None: continue
			updated = playback.get('tracked_at') or item.get('tracked_at') or ''
			ep_ins.append(('episode', str(show_id), int(season), int(number), str(round(percent, 1)), 0, updated, 0,
				show.get('title') or item.get('title') or ''))
		except: pass
	from caches.wetrakr_cache import wetrakr_watched_cache
	wetrakr_watched_cache.set_bulk_movie_progress(movie_ins)
	wetrakr_watched_cache.set_bulk_tvshow_progress(ep_ins)
	return True

def _activity_values(stamp, field):
	if not isinstance(stamp, dict): return []
	values = []
	for section in ('movies', 'shows', 'episodes'):
		block = stamp.get(section)
		if isinstance(block, dict): values.append(block.get(field))
	return values

def _activity_moved(previous, stamp, fields):
	if not isinstance(previous, dict) or not isinstance(stamp, dict): return True
	for field in fields:
		if _activity_values(previous, field) != _activity_values(stamp, field): return True
	return False

_WATCHED_HISTORY_KEY = 'watched_history_complete'

def wetrakr_sync_activities(params=None, force_update=False, progress=None, allow_history_repair=False):
	notify = False
	if isinstance(params, dict):
		force_update = params.get('force_update', 'false') in ('true', 'True', True) or force_update
		notify = params.get('notify', 'false') in ('true', 'True', True)
	def _done(status):
		if notify:
			if status == 'failed': kodi_utils.notification('WeTrakr Sync Failed', 3000)
			elif status == 'success': kodi_utils.notification('WeTrakr Sync Complete', 3000)
		return status
	if not wetrakr_user_active(): return _done('no account')
	# Bug 3 fix: one sync at a time per device.
	# force_update (user-triggered) waits up to 30 s for any running sync to finish;
	# background/list-open callers bail out immediately and use cached data.
	lock_owner = _claim_sync_lock(wait_secs=30 if force_update else 0)
	if not lock_owner:
		kodi_utils.logger('WeTrakr', 'sync skipped: another sync is running')
		return _done('busy')
	try:
		from caches import wetrakr_cache as wt_cache
		if force_update:
			wt_cache.wetrakr_cache.delete('last_activities')
		kodi_utils.sync_progress(progress, 'Checking WeTrakr', 4)
		stamp = _api('GET', '/sync/last_activities')
		if not isinstance(stamp, dict):
			if not force_update: return _done('failed')
			stamp = None
		repair_history = allow_history_repair and wt_cache.wetrakr_cache.get(_WATCHED_HISTORY_KEY) is None
		previous = None if force_update else wt_cache.wetrakr_cache.get('last_activities')
		# Bug 4 fix: compare only the specific timestamp fields that drive refresh decisions.
		# A full-dict (==) comparison breaks whenever the API includes ephemeral fields that
		# change on every response (server time, trace ID, etc.).
		_ALL_ACTIVITY_FIELDS = ('last_tracking_watched_at', 'last_tracking_paused_at', 'last_tracking_watching_at')
		if isinstance(stamp, dict) and previous is not None and not repair_history:
			if not _activity_moved(previous, stamp, _ALL_ACTIVITY_FIELDS): return _done('not needed')
		if repair_history: kodi_utils.logger('WeTrakr', 'watched history re-download')
		refresh_history = force_update or repair_history or stamp is None or _activity_moved(previous, stamp, ('last_tracking_watched_at',))
		refresh_playing = force_update or stamp is None or _activity_moved(previous, stamp, ('last_tracking_paused_at', 'last_tracking_watching_at'))
		# Bug 2 fix: use from_date for incremental syncs once a full history has been downloaded.
		# Full download: first connect (no _WATCHED_HISTORY_KEY), force_update, or repair.
		# Incremental:   subsequent normal syncs — only new rows, merged via upsert.
		from_date = None
		if refresh_history and not force_update and not repair_history:
			if wt_cache.wetrakr_cache.get(_WATCHED_HISTORY_KEY) is not None:
				from_date = wt_cache.wetrakr_cache.get('last_sync')
		try:
			if refresh_history and not wetrakr_refresh_watched_indicators(progress, from_date=from_date): return _done('failed')
			if refresh_playing and not wetrakr_sync_playback(progress): return _done('failed')
			if isinstance(stamp, dict): wt_cache.wetrakr_cache.set('last_activities', stamp)
			wt_cache.wetrakr_cache.set('last_sync', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
			if refresh_history: wt_cache.wetrakr_cache.set(_WATCHED_HISTORY_KEY, True)
			return _done('success')
		except Exception as e:
			kodi_utils.logger('WeTrakr', 'sync failed: %s' % e)
			return _done('failed')
	finally:
		_release_sync_lock(lock_owner)

def wetrakr_force_sync(params=None):
	if not wetrakr_user_active(): return kodi_utils.notification('WeTrakr account not authorised', 3000)
	progress = kodi_utils.progress_dialog('WeTrakr Sync')
	status = 'failed'
	try:
		status = wetrakr_sync_activities(force_update=True, progress=progress)
	except Exception as e:
		kodi_utils.logger('WeTrakr', 'Force sync failed: %s' % e)
	finally:
		kodi_utils.close_progress_dialog(progress)
	if status in ('failed', 'busy'): kodi_utils.notification('WeTrakr Sync Failed', 3000)
	else:
		kodi_utils.notification('WeTrakr Sync Complete', 3000)
		kodi_utils.kodi_refresh()
	return status

def in_wetrakr_lists(params):
	import sys
	def _process():
		for item in lists:
			try:
				list_id = item.get('id')
				if list_id in (None, '', 0, '0'): continue
				name = item.get('name') or 'List'
				item_count = item.get('itemCount') or item.get('item_count') or 0
				display = '%s | [I](x%s)[/I]' % (name, item_count)
				url_params = {
					'mode': 'navigator.wetrakr_user_list', 'list_id': list_id, 'list_name': name, 'name': name,
					'iconImage': 'wetrakr'
				}
				url = kodi_utils.build_url(url_params)
				from modules import list_sort
				cm = [
					list_sort.custom_sort_context(kodi_utils.build_url, 'wetrakr.list:%s' % list_id),
					('[B]Add to Shortcut Folder[/B]', 'RunPlugin(%s)' % kodi_utils.build_url(
						{'mode': 'menu_editor.shortcut_folder_add_known', 'url': url}))
				]
				listitem = kodi_utils.make_listitem()
				listitem.setLabel(display)
				icon = kodi_utils.get_icon('wetrakr')
				fanart = kodi_utils.get_addon_fanart()
				listitem.setArt({'icon': icon, 'poster': icon, 'thumb': icon, 'fanart': fanart, 'banner': fanart})
				info_tag = listitem.getVideoInfoTag()
				info_tag.setPlot(kodi_utils.list_folder_plot(item.get('description'), '', item_count))
				listitem.addContextMenuItems(cm)
				yield (url, listitem, True)
			except: pass
	handle = int(sys.argv[1])
	media_type = params.get('media_type') or 'movie'
	if media_type not in ('movie', 'tvshow'): media_type = 'tvshow'
	try:
		if wetrakr_lists_active():
			lists = wetrakr_lists_with_media(media_type, params.get('tmdb_id'), params.get('imdb_id'))
		else:
			lists = []
		kodi_utils.add_items(handle, list(_process()))
	except: pass
	kodi_utils.set_content(handle, kodi_utils.MENU_FOLDER_CONTENT)
	kodi_utils.set_category(handle, params.get('category_name', 'WeTrakr Lists'))
	kodi_utils.end_directory(handle)
	kodi_utils.set_view_mode('view.main', kodi_utils.MENU_FOLDER_CONTENT)
