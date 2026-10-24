# -*- coding: utf-8 -*-
import json
import os
import time
import calendar
import requests
from datetime import datetime
from threading import Lock
from urllib.parse import urljoin, quote
from caches import simkl_cache
from caches.settings_cache import get_setting, set_setting
from modules import kodi_utils, settings, list_sort
from modules.http_defaults import META_API_TIMEOUT, shipped_simkl_alt, shipped_simkl_client, shipped_simkl_client_v2, shipped_simkl_ids
from modules.utils import copy2clip, make_qrcode, make_tinyurl, device_auth_complete_url, device_auth_site_label, authorise_wait_text

BASE_URL = 'https://api.simkl.com'
OAUTH_PIN_URL = 'https://api.simkl.com/oauth/pin'
OAUTH2_DEVICE_URL = 'https://api.simkl.com/oauth2/device'
OAUTH2_TOKEN_URL = 'https://api.simkl.com/oauth2/token'
OAUTH2_REVOKE_URL = 'https://api.simkl.com/oauth2/revoke'
SIMKL_APP_NAME = 'plugin.video.redlight'
SIMKL_V2_SCOPE = 'media:read media:write'
_SIMKL_REFRESHING_PROP = 'redlight.simkl_refreshing_token'
# Shared across plugin invokers + SimklMonitor (in-memory alone is not enough in Kodi).
_SIMKL_MIN_REQUEST_GAP = 1.5
_SIMKL_THROTTLE_PROP = 'redlight.simkl_last_request_at'
_SIMKL_SYNC_BUSY_PROP = 'redlight.simkl_sync_busy'
_SIMKL_SYNC_BUSY_AT_PROP = 'redlight.simkl_sync_busy_at'
_SIMKL_ACTIVITIES_AT_PROP = 'redlight.simkl_activities_at'
_SIMKL_ACTIVITIES_MIN_GAP = 60
_SIMKL_REWATCH_DENIED_PROP = 'redlight.simkl_rewatch_denied'
_SIMKL_PLAN_AT_PROP = 'redlight.simkl_plan_at'
_SIMKL_PLAN_REFRESH_SECONDS = 600
_request_lock = Lock()
_sync_lock = Lock()
_last_request_time = 0.0
_throttle_path = None

def _simkl_throttle_path():
	global _throttle_path
	if not _throttle_path:
		_throttle_path = os.path.join(kodi_utils.addon_profile(), 'simkl_api_throttle')
	return _throttle_path

def _shared_last_request_at():
	last = _last_request_time
	try:
		last = max(last, float(kodi_utils.get_property(_SIMKL_THROTTLE_PROP) or 0))
	except Exception:
		pass
	try:
		last = max(last, os.path.getmtime(_simkl_throttle_path()))
	except Exception:
		pass
	return last

def _claim_request_slot(now):
	global _last_request_time
	_last_request_time = now
	try: kodi_utils.set_property(_SIMKL_THROTTLE_PROP, '%.3f' % now)
	except Exception: pass
	try:
		path = _simkl_throttle_path()
		folder = os.path.dirname(path)
		if folder and not os.path.isdir(folder):
			try: os.makedirs(folder)
			except Exception: pass
		with open(path, 'w') as handle:
			handle.write('%.3f\n' % now)
	except Exception:
		pass

def _throttle():
	"""Space Simkl HTTP calls across threads and separate Kodi Python invokers."""
	with _request_lock:
		while True:
			now = time.time()
			wait = _SIMKL_MIN_REQUEST_GAP - (now - _shared_last_request_at())
			if wait <= 0:
				_claim_request_slot(now)
				return
			kodi_utils.sleep(int(wait * 1000) + 25)

def _stored_simkl_client():
	from caches.settings_cache import normalize_credential_string
	try: return normalize_credential_string(get_setting('redlight.simkl.client', ''))
	except Exception: return ''

def _client_id():
	"""Client ID for API calls. A V1 grant stays on a V1 app even if Restore wrote the V2 key."""
	stored = _stored_simkl_client()
	official = set(shipped_simkl_ids())
	if _has_simkl_token() and _auth_version() == '1':
		if stored and stored not in official:
			return stored
		return shipped_simkl_client() or stored
	if stored: return stored
	return shipped_simkl_client_v2() or shipped_simkl_client()

def _auth_client_id():
	"""Client ID for a new Authorise. Official installs use the V2 app; a custom id is left as-is."""
	stored = _stored_simkl_client()
	official = set(shipped_simkl_ids())
	if stored and stored not in official:
		return stored
	return shipped_simkl_client_v2() or stored or shipped_simkl_client()

def _client_ids_to_try():
	"""Authorised: whatever they signed in with. Otherwise V2 then V1, never the 2.4.6 alt."""
	primary = _client_id()
	if _has_simkl_token():
		return [primary] if primary else []
	ids = []
	for cid in (primary, shipped_simkl_client_v2(), shipped_simkl_client()):
		if cid and cid != shipped_simkl_alt() and cid not in ids:
			ids.append(cid)
	return ids

def _client_id_rejected(resp):
	if resp is None: return False
	if resp.status_code == 412: return True
	try: text = (resp.text or '').lower()
	except Exception: text = ''
	return resp.status_code in (400, 401, 403) and 'client_id' in text

def _has_simkl_token():
	token = _simkl_token()
	return token not in ('0', '', None, 'empty_setting')

def _auth_version():
	from caches.settings_cache import live_setting
	version = str(live_setting('simkl.auth_version', '') or '').strip()
	if version in ('1', '2'): return version
	refresh = live_setting('simkl.refresh', '0')
	if refresh not in ('0', '', None, 'empty_setting'): return '2'
	token = _simkl_token()
	if str(token or '').startswith('simkl_at_'): return '2'
	if _has_simkl_token(): return '1'
	return ''

def _simkl_refresh_token_value():
	from caches.settings_cache import live_setting
	return live_setting('simkl.refresh', '0')

def _has_simkl_refresh():
	refresh = _simkl_refresh_token_value()
	return refresh not in ('0', '', None, 'empty_setting')

def _reset_shipped_client_id(force=False):
	"""After Revoke, land on the V2 client. Leave a custom V2 key unless force (V1 revoke)."""
	current = _stored_simkl_client() or _client_id()
	if not force and current and current not in shipped_simkl_ids(): return
	cid = shipped_simkl_client_v2() or shipped_simkl_client()
	if not cid: return
	try: set_setting('simkl.client', cid)
	except Exception: pass

def _adopt_shipped_client_id(cid):
	"""Remember which shipped app Simkl accepted. Never overwrite a custom Client ID."""
	shipped = shipped_simkl_ids()
	if not cid or cid not in shipped: return
	if cid == shipped_simkl_alt() and not _has_simkl_token(): return
	current = _client_id()
	if current == cid: return
	if current and current not in shipped: return
	try: set_setting('simkl.client', cid)
	except Exception: pass

def _clear_alt_client_id():
	"""After Revoke, the 2.4.6 key must not remain as the stored default."""
	if _client_id() != shipped_simkl_alt(): return
	try: set_setting('simkl.client', shipped_simkl_client())
	except Exception: pass

def _simkl_token():
	from caches.settings_cache import live_setting
	return live_setting('simkl.token', '0')

def _headers(client_id=None):
	token = _simkl_token()
	cid = client_id or _client_id()
	h = {'Content-Type': 'application/json', 'simkl-api-key': cid, 'User-Agent': '%s/%s' % (SIMKL_APP_NAME, kodi_utils.addon_version())}
	if token not in ('0', '', None, 'empty_setting'): h['Authorization'] = 'Bearer %s' % token
	return h

def _url(path, auth=True, client_id=None):
	cid = client_id or _client_id()
	if not cid: return None
	base = path if path.startswith('http') else urljoin(BASE_URL, path.lstrip('/'))
	sep = '&' if '?' in base else '?'
	return '%s%sclient_id=%s&app-name=%s&app-version=%s' % (base, sep, cid, SIMKL_APP_NAME, kodi_utils.addon_version())

def _pin_headers():
	return {'User-Agent': '%s/%s' % (SIMKL_APP_NAME, kodi_utils.addon_version())}

def _pin_url(user_code=None, client_id=None):
	cid = client_id or _client_id()
	url = '%s/%s' % (OAUTH_PIN_URL, user_code) if user_code else OAUTH_PIN_URL
	sep = '&' if '?' in url else '?'
	return '%s%sclient_id=%s&app-name=%s&app-version=%s' % (url, sep, cid, SIMKL_APP_NAME, kodi_utils.addon_version())

def _oauth2_url(base, client_id=None):
	cid = client_id or _client_id()
	if not cid: return None
	sep = '&' if '?' in base else '?'
	return '%s%sclient_id=%s&app-name=%s&app-version=%s' % (base, sep, cid, SIMKL_APP_NAME, kodi_utils.addon_version())

def _oauth_form_headers():
	return {
		'Content-Type': 'application/x-www-form-urlencoded',
		'User-Agent': '%s/%s' % (SIMKL_APP_NAME, kodi_utils.addon_version())
	}

def _simkl_pin_auth_url(pin):
	complete = (pin or {}).get('verification_uri_complete')
	if complete and str(complete).startswith('http'):
		return str(complete).strip()
	return device_auth_complete_url(pin, pin.get('user_code', ''), fallback='https://simkl.com/pin', style='path')

def _store_v2_tokens(payload, client_id=None):
	if not isinstance(payload, dict): return False
	token = payload.get('access_token')
	if not token: return False
	expires_in = int(payload.get('expires_in') or 604800)
	refresh = payload.get('refresh_token') or _simkl_refresh_token_value()
	set_setting('simkl.token', token)
	if refresh and refresh not in ('0', '', None, 'empty_setting'):
		set_setting('simkl.refresh', refresh)
	set_setting('simkl.expires', str(time.time() + expires_in))
	set_setting('simkl.auth_version', '2')
	if client_id:
		set_setting('simkl.client', client_id)
	scope = str(payload.get('scope') or '')
	if 'media:write' not in scope:
		kodi_utils.logger('Simkl', 'V2 token scope is read-only (%s)' % (scope or 'empty'))
	return True

def simkl_refresh_token():
	if not _has_simkl_refresh(): return False
	if kodi_utils.get_property(_SIMKL_REFRESHING_PROP) == 'true':
		while kodi_utils.get_property(_SIMKL_REFRESHING_PROP) == 'true':
			kodi_utils.sleep(250)
		return _has_simkl_token()
	kodi_utils.set_property(_SIMKL_REFRESHING_PROP, 'true')
	stored = False
	try:
		from caches.settings_cache import reload_auth_from_db
		reload_auth_from_db('simkl.token', 'simkl.refresh', 'simkl.expires', 'simkl.client')
		refresh = _simkl_refresh_token_value()
		cid = _client_id()
		if not refresh or refresh in ('0', '', 'empty_setting') or not cid:
			return False
		_throttle()
		url = _oauth2_url(OAUTH2_TOKEN_URL, client_id=cid)
		resp = requests.post(url, data={
			'grant_type': 'refresh_token',
			'client_id': cid,
			'refresh_token': refresh
		}, headers=_oauth_form_headers(), timeout=META_API_TIMEOUT)
		if resp.status_code == 200:
			stored = bool(_store_v2_tokens(resp.json() or {}, cid))
			return stored
		from modules.meta_auth_alerts import maybe_notify_refresh_failure
		maybe_notify_refresh_failure('simkl', resp.status_code, resp.text)
		return False
	except Exception as e:
		kodi_utils.logger('Simkl', 'V2 refresh failed: %s' % e)
		stored = False
		return False
	finally:
		kodi_utils.clear_property(_SIMKL_REFRESHING_PROP)
		if stored:
			try: _save_simkl_profile()
			except: pass

def _ensure_v2_access_token():
	if _auth_version() != '2' or not _has_simkl_refresh(): return
	while kodi_utils.get_property(_SIMKL_REFRESHING_PROP) == 'true':
		kodi_utils.sleep(250)
	try: expires_at = float(_simkl_setting_expires())
	except Exception: expires_at = 0.0
	# Refresh a day before the 7-day access token dies.
	if time.time() + 86400 >= expires_at:
		simkl_refresh_token()

def _simkl_setting_expires():
	from caches.settings_cache import live_setting
	return live_setting('simkl.expires', '0')

def call_simkl(path, data=None, method=None, is_delete=False, _retried=False):
	last_error = None
	last_status = None
	last_client_reject = False
	_ensure_v2_access_token()
	used_token = _simkl_token()
	for cid in _client_ids_to_try():
		_throttle()
		url = _url(path, client_id=cid)
		if not url: return None
		headers = _headers(client_id=cid)
		try:
			if is_delete:
				resp = requests.delete(url, headers=headers, timeout=META_API_TIMEOUT)
			elif method == 'get' or (data is None and not method):
				resp = requests.get(url, headers=headers, timeout=META_API_TIMEOUT)
			else:
				payload = json.dumps(data) if isinstance(data, (dict, list)) else data
				resp = requests.post(url, data=payload, headers=headers, timeout=META_API_TIMEOUT)
			if resp.status_code in (200, 201):
				if cid != _client_id(): _adopt_shipped_client_id(cid)
				return resp.json() if resp.text else True
			if resp.status_code == 204:
				if cid != _client_id(): _adopt_shipped_client_id(cid)
				return True
			last_status = resp.status_code
			last_client_reject = _client_id_rejected(resp)
			last_error = 'HTTP %s %s' % (resp.status_code, url)
			if not last_client_reject:
				break
		except Exception as e:
			kodi_utils.logger('Simkl Error', str(e))
			return None
	if last_error: kodi_utils.logger('Simkl', last_error)
	if last_status == 401 and not last_client_reject:
		if not _retried:
			from caches.settings_cache import reload_auth_from_db
			fresh = (reload_auth_from_db('simkl.token', 'simkl.refresh', 'simkl.expires') or {}).get('simkl.token')
			if fresh and fresh not in ('0', '', 'empty_setting') and fresh != used_token:
				return call_simkl(path, data=data, method=method, is_delete=is_delete, _retried=True)
			if _auth_version() == '2' and simkl_refresh_token():
				return call_simkl(path, data=data, method=method, is_delete=is_delete, _retried=True)
		from modules.meta_auth_alerts import maybe_notify_refresh_failure
		maybe_notify_refresh_failure('simkl', 401)
	return None

def _simkl_rewatch_denied(payload):
	return isinstance(payload, dict) and payload.get('error') == 'pro_required'

def _simkl_handle_rewatch_denied(payload):
	if not _simkl_rewatch_denied(payload): return False
	if kodi_utils.get_property(_SIMKL_REWATCH_DENIED_PROP) != 'true':
		kodi_utils.set_property(_SIMKL_REWATCH_DENIED_PROP, 'true')
		msg = (payload.get('message') or '').strip() or 'Simkl rewatches need PRO or VIP. Track Rewatches was turned off.'
		kodi_utils.notification(msg, 4500)
		kodi_utils.logger('Simkl', 'rewatch denied: pro_required')
		try: set_setting('simkl.track_rewatches', 'false')
		except: pass
	return True

def _turn_off_free_rewatches():
	if get_setting('redlight.simkl.track_rewatches', 'false') != 'true': return
	set_setting('simkl.track_rewatches', 'false')
	kodi_utils.notification('Simkl rewatches need PRO or VIP. Track Rewatches was turned off.', 4500)

def _simkl_want_rewatch():
	if kodi_utils.get_property(_SIMKL_REWATCH_DENIED_PROP) == 'true': return False
	refresh_simkl_account_plan()
	return settings.simkl_track_rewatches()

def _simkl_rewatch_qs(query=''):
	query = query or ''
	if not _simkl_want_rewatch(): return query
	if 'allow_rewatch=' in query: return query
	return ('%s&allow_rewatch=yes' % query) if query else 'allow_rewatch=yes'

def _simkl_rewatch_path(path):
	if not path or not _simkl_want_rewatch(): return path
	if 'allow_rewatch=' in path: return path
	sep = '&' if '?' in path else '?'
	return '%s%sallow_rewatch=yes' % (path, sep)

def _simkl_path_without_rewatch(path):
	if not path or 'allow_rewatch=' not in path: return path
	base, sep, qs = path.partition('?')
	if not sep: return path
	parts = [p for p in qs.split('&') if p and not p.startswith('allow_rewatch=')]
	return '%s?%s' % (base, '&'.join(parts)) if parts else base

def call_simkl_rewatch(path, data=None, method=None, is_delete=False):
	result = call_simkl(path, data=data, method=method, is_delete=is_delete)
	if not _simkl_handle_rewatch_denied(result): return result
	retry = _simkl_path_without_rewatch(path)
	if retry == path: return result
	return call_simkl(retry, data=data, method=method, is_delete=is_delete)

def simkl_get_pin():
	try:
		for cid in _client_ids_to_try():
			_throttle()
			resp = requests.get(_pin_url(client_id=cid), headers=_pin_headers(), timeout=META_API_TIMEOUT)
			if resp.status_code == 200:
				data = resp.json() or {}
				if data.get('user_code'):
					data['_client_id'] = cid
					return data
			if not _client_id_rejected(resp):
				break
	except: pass
	return None

def _simkl_v1_pin_ok(cid):
	try:
		_throttle()
		pin_resp = requests.get(_pin_url(client_id=cid), headers=_pin_headers(), timeout=META_API_TIMEOUT)
		if pin_resp.status_code != 200: return False
		pin_body = {}
		try: pin_body = pin_resp.json() or {}
		except: pin_body = {}
		return bool(pin_body.get('user_code'))
	except Exception:
		return False

def _simkl_v2_device_ok(cid):
	try:
		_throttle()
		url = _oauth2_url(OAUTH2_DEVICE_URL, client_id=cid)
		resp = requests.post(url, data={'client_id': cid, 'scope': SIMKL_V2_SCOPE},
			headers=_oauth_form_headers(), timeout=META_API_TIMEOUT)
		if resp.status_code != 200: return False
		body = {}
		try: body = resp.json() or {}
		except: body = {}
		return bool(body.get('user_code') and body.get('device_code'))
	except Exception:
		return False

def _simkl_test_client_id_value():
	"""The Client ID shown in Settings, not the grant-substituted API key."""
	stored = _stored_simkl_client()
	if stored: return stored
	if _has_simkl_token() and _auth_version() == '1':
		return shipped_simkl_client()
	return shipped_simkl_client_v2() or shipped_simkl_client()

def simkl_test_client_id():
	"""Classify the Client ID as AUTH V2 (device) or AUTH V1 (PIN). V2 wins if both answer."""
	cid = _simkl_test_client_id_value()
	if not cid:
		return False, 'Simkl Client ID Key is not set.'
	try:
		v2_ok = _simkl_v2_device_ok(cid)
		v1_ok = False if v2_ok else _simkl_v1_pin_ok(cid)
	except Exception as e:
		return False, 'Simkl Client ID Key failed.[CR]Could not reach Simkl: %s' % str(e)
	grant = _auth_version() if _has_simkl_token() else ''
	if v2_ok:
		msg = 'Simkl Client ID Key is valid (AUTH V2).'
		if grant == '1':
			msg = 'This Client ID is AUTH V2. You are signed in with V1. Restore Default for the V1 key, or Revoke and Authorise to use V2.'
		return True, msg
	if v1_ok:
		msg = 'Simkl Client ID Key is valid (AUTH V1). Revoke and Authorise again for V2.'
		if grant == '2':
			msg = 'This Client ID is AUTH V1. You are signed in with V2. Restore Default for the V2 key.'
		return True, msg
	return False, 'Simkl Client ID Key failed.[CR]Simkl did not accept this Client ID as AUTH V1 or AUTH V2.'

def simkl_get_device(client_id=None):
	cid = client_id or _auth_client_id()
	shipped_v2 = shipped_simkl_client_v2()
	tried = []
	for candidate in (cid, shipped_v2):
		if not candidate or candidate in tried: continue
		tried.append(candidate)
		try:
			_throttle()
			url = _oauth2_url(OAUTH2_DEVICE_URL, client_id=candidate)
			resp = requests.post(url, data={'client_id': candidate, 'scope': SIMKL_V2_SCOPE},
				headers=_oauth_form_headers(), timeout=META_API_TIMEOUT)
			if resp.status_code == 200:
				data = resp.json() or {}
				if data.get('user_code') and data.get('device_code'):
					data['_client_id'] = candidate
					return data
		except Exception as e:
			kodi_utils.logger('Simkl', 'V2 device start failed: %s' % e)
	return None

def simkl_poll_device(pin):
	device_code = pin.get('device_code')
	user_code = pin.get('user_code')
	if not device_code or not user_code: return None
	cid = pin.get('_client_id') or _auth_client_id()
	expires_in = int(pin.get('expires_in') or 900)
	interval = max(int(pin.get('interval') or 5), 1)
	auth_url = _simkl_pin_auth_url(pin)
	qr_code = make_qrcode(auth_url) or ''
	copy2clip(auth_url)
	short_url = make_tinyurl(auth_url)
	content = authorise_wait_text(user_code, device_auth_site_label(pin, 'https://simkl.com/pin'), short_url)
	progress = kodi_utils.progress_dialog('Simkl Authorise', qr_code)
	progress.update(content, 0)
	expires = time.time() + expires_in
	url = _oauth2_url(OAUTH2_TOKEN_URL, client_id=cid)
	while time.time() < expires:
		if progress.iscanceled():
			progress.close()
			return 'canceled'
		# Device poll is its own timer; skip the shared 1.5s throttle so interval stays honest.
		try:
			resp = requests.post(url, data={
				'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
				'client_id': cid,
				'device_code': device_code
			}, headers=_oauth_form_headers(), timeout=META_API_TIMEOUT)
			body = {}
			try: body = resp.json() or {}
			except: body = {}
			if resp.status_code == 200 and body.get('access_token'):
				progress.close()
				return body
			error = str(body.get('error') or '')
			if error == 'slow_down':
				interval += 5
			elif error == 'expired_token':
				progress.close()
				return None
			elif error == 'invalid_client':
				progress.close()
				return None
		except: pass
		progress.update(content, int(100 * (1 - (expires - time.time()) / float(expires_in))))
		if kodi_utils.sleep_while_authorising(progress, interval):
			progress.close()
			return 'canceled'
	progress.close()
	return None

def _save_simkl_profile():
	info = call_simkl('/users/settings', data={})
	if not isinstance(info, dict) or not (info.get('user') or info.get('account')):
		return
	_apply_simkl_profile(info)

def _apply_simkl_profile(info):
	user_id = ''
	if info.get('user'):
		u = info['user']
		name = str(u.get('name') or u.get('login') or u.get('username') or '').strip()
		if name:
			set_setting('simkl.user', name)
		else:
			from caches.settings_cache import live_setting
			if str(live_setting('simkl.user', '') or '') in ('', 'empty_setting'):
				set_setting('simkl.user', 'Simkl User')
		user_id = u.get('id') or u.get('user_id') or ''
		if not user_id:
			account = info.get('account') or {}
			user_id = account.get('id') or ''
	if user_id not in (None, '', 0, '0'):
		set_setting('simkl.user_id', str(user_id))
	_store_simkl_plan(info)

def _simkl_plan():
	from caches.settings_cache import live_setting
	plan = str(live_setting('simkl.account_type', '') or '').strip().lower()
	if plan == 'empty_setting': return ''
	return plan

def _clear_simkl_custom_list_cache():
	try:
		from caches.lists_cache import lists_cache
		dbcon = lists_cache.manual_connect('lists_db')
		dbcon.execute('DELETE FROM lists WHERE id = ? OR id LIKE ?', ('simkl_custom_lists', 'simkl_custom_list_%'))
	except: pass

def _store_simkl_plan(info):
	"""Save account.type. A response with no type leaves the saved plan alone."""
	if not isinstance(info, dict): return
	account = info.get('account') or {}
	if not isinstance(account, dict): return
	plan = str(account.get('type') or '').strip().lower()
	if not plan: return
	previous = _simkl_plan()
	set_setting('simkl.account_type', plan)
	if plan == 'free':
		_turn_off_free_rewatches()
	if previous != plan:
		_clear_simkl_custom_list_cache()
	try: kodi_utils.set_property(_SIMKL_PLAN_AT_PROP, '%.3f' % time.time())
	except: pass

def refresh_simkl_account_plan():
	"""Re-read the plan for any signed-in account. A failed call does not mark it free."""
	if not settings.simkl_user_active(): return
	try: last = float(kodi_utils.get_property(_SIMKL_PLAN_AT_PROP) or 0)
	except: last = 0.0
	if last and (time.time() - last) < _SIMKL_PLAN_REFRESH_SECONDS: return
	info = call_simkl('/users/settings', data={})
	if not isinstance(info, dict): return
	if not info.get('user') and not info.get('account'): return
	_apply_simkl_profile(info)

def _simkl_lists_plan_block():
	if not settings.simkl_plan_known_free(): return None
	return {'error': 'premium_only', 'message': 'Simkl PRO/VIP required for custom lists.'}

def simkl_authenticate(dummy=''):
	pin = simkl_get_device()
	if not pin or not pin.get('user_code'):
		_ok, message = simkl_test_client_id()
		return kodi_utils.ok_dialog(heading='Simkl', text=message)
	token = simkl_poll_device(pin)
	if token == 'canceled':
		return kodi_utils.notification('Simkl Authorisation Canceled', 3000)
	if not token or not isinstance(token, dict) or not token.get('access_token'):
		return kodi_utils.notification('Simkl Error Authorising', 3000)
	if not _store_v2_tokens(token, pin.get('_client_id')):
		return kodi_utils.notification('Simkl Error Authorising', 3000)
	from caches.settings_cache import settings_cache
	settings_cache.clear_db_cache()
	_save_simkl_profile()
	settings.offer_watched_provider(2, 'Simkl')
	from modules.meta_auth_alerts import clear_alert
	clear_alert('simkl')
	kodi_utils.notification('Simkl Account Authorised', 3000)
	try:
		status = simkl_sync_activities(force_update=True)
	except Exception as e:
		kodi_utils.logger('Simkl', 'Post-auth sync failed: %s' % e)
		status = 'failed'
	settings.notify_post_auth_sync('Simkl', status)
	try: kodi_utils.container_refresh()
	except: pass
	return True

def simkl_revoke_authentication(dummy=''):
	if not kodi_utils.confirm_revoke('Simkl'): return
	from modules.meta_auth_alerts import clear_alert
	clear_alert('simkl')
	was_v1 = _auth_version() == '1'
	if _auth_version() == '2':
		try:
			cid = _client_id()
			token = _simkl_refresh_token_value() or _simkl_token()
			if cid and token not in ('0', '', None, 'empty_setting'):
				_throttle()
				url = _oauth2_url(OAUTH2_REVOKE_URL, client_id=cid)
				requests.post(url, data={'client_id': cid, 'token': token},
					headers=_oauth_form_headers(), timeout=META_API_TIMEOUT)
		except Exception:
			pass
	set_setting('simkl.user', 'empty_setting')
	set_setting('simkl.user_id', 'empty_setting')
	set_setting('simkl.token', '0')
	set_setting('simkl.refresh', '0')
	set_setting('simkl.expires', '0')
	set_setting('simkl.auth_version', '')
	set_setting('simkl.account_type', 'empty_setting')
	# After the V1 grant is gone so Restore Default is hidden for the shipped V2 key.
	_reset_shipped_client_id(force=was_v1)
	try:
		from caches.settings_cache import settings_cache
		settings_cache.set_memory_cache('simkl.client', _stored_simkl_client() or 'empty_setting')
	except Exception:
		pass
	settings.fallback_watched_provider_on_revoke(2)
	simkl_cache.clear_all_simkl_cache_data(silent=True, refresh=False)
	kodi_utils.notification('Simkl Authorisation Reset', 3000)

def _tmdb_id(ids):
	try:
		if ids.get('tmdb'): return str(int(ids['tmdb']))
	except: pass
	return None

# Defined early — anime id enrich helpers use this before the sync-cache block below.
_SIMKL_ID_EMPTY = ('None', None, '', 'empty_setting', 0, '0')

def _simkl_ids_from_dict(ids):
	media_ids = {}
	if not isinstance(ids, dict): return media_ids
	for key in ('tmdb', 'imdb', 'tvdb', 'simkl'):
		value = ids.get(key)
		if value in _SIMKL_ID_EMPTY: continue
		if key in ('tmdb', 'tvdb', 'simkl'):
			try: value = int(value)
			except: pass
		media_ids[key] = value
	return media_ids

def _simkl_enrich_anime_ids(simkl_id, media_ids):
	"""Sync all-items often omits TVDb / ships movie TMDb ids for season-split anime."""
	try:
		sid = int(simkl_id)
	except: return media_ids
	cache_key = 'anime_ids_%s' % sid
	try:
		cached = simkl_cache.simkl_cache.get(cache_key)
		if isinstance(cached, dict):
			merged = dict(media_ids or {})
			for key, value in cached.items():
				if key not in merged and value not in _SIMKL_ID_EMPTY: merged[key] = value
			return merged
	except: pass
	detail = call_simkl('/anime/%s' % sid, method='get')
	if not isinstance(detail, dict): return media_ids
	detail_ids = _simkl_ids_from_dict(detail.get('ids') or {})
	try: simkl_cache.simkl_cache.set(cache_key, detail_ids)
	except: pass
	merged = dict(media_ids or {})
	for key, value in detail_ids.items():
		if key not in merged and value not in _SIMKL_ID_EMPTY: merged[key] = value
	return merged

def _simkl_media_ids(item, media_kind):
	try:
		if media_kind == 'movies': obj = item.get('movie')
		else: obj = item.get('show') or item.get('anime')
		if not isinstance(obj, dict): obj = item
		ids = obj.get('ids') or item.get('ids') or {}
		if not isinstance(ids, dict): ids = {}
		media_ids = _simkl_ids_from_dict(ids)
		if media_kind == 'anime':
			simkl_id = ids.get('simkl')
			# Incomplete sync ids (e.g. Re:Zero S4 = episode IMDb only; Steel Ball Run = dead TMDb only).
			if simkl_id and not (media_ids.get('tvdb') and media_ids.get('imdb') and media_ids.get('tmdb')):
				media_ids = _simkl_enrich_anime_ids(simkl_id, media_ids)
		return media_ids
	except: return {}

def _simkl_all_items(media_kind, status):
	# Default sync payload includes title/year (needed for list_sort). ids_only strips titles and
	# makes Title A–Z a no-op.
	path = '/sync/all-items/%s/%s' % (media_kind, status)
	response = call_simkl(path, method='get')
	if response is None:
		kodi_utils.logger('Simkl', 'list fetch failed: %s' % path)
		return None
	if response is True:
		return []
	if isinstance(response, list):
		return response
	if not isinstance(response, dict):
		kodi_utils.logger('Simkl', 'list fetch unexpected response for %s: %s' % (path, type(response).__name__))
		return None
	items = response.get(media_kind)
	if items is None and media_kind in ('shows', 'anime'):
		items = response.get('shows') or response.get('anime')
	if items is None:
		items = response.get('items') or response.get('list')
	if items is None:
		return []
	return items if isinstance(items, list) else []

def _simkl_release_key(item, media_kind):
	block = _simkl_item_block(item, media_kind)
	if not isinstance(block, dict): block = {}
	for key in ('released', 'released_at', 'first_aired', 'aired'):
		val = block.get(key)
		if val not in (None, '', 'None'): return val
	year = block.get('year')
	if year not in (None, '', 'None', 0):
		try: return '%04d-01-01' % int(year)
		except: pass
	return '9999-12-31' if media_kind == 'movies' else '9999-12-31T00:00:00.000Z'

_SIMKL_STATUS_CACHE_PREFIX = 'simkl_all_items'
_SIMKL_LIST_ACTIVITY_KEYS = ('plantowatch', 'watching', 'completed', 'hold', 'dropped', 'removed_from_list', 'all')
_SIMKL_STATUS_KEYS = ('plantowatch', 'watching', 'completed', 'hold', 'dropped')
_SIMKL_STATUS_LABELS = {'plantowatch': 'Plan to Watch', 'watching': 'Watching', 'completed': 'Completed', 'hold': 'On Hold', 'dropped': 'Dropped'}

def _simkl_list_cache_key(media_kind, status):
	return '%s_%s_%s' % (_SIMKL_STATUS_CACHE_PREFIX, media_kind, status)

def _simkl_delete_list_cache_key(key):
	try: simkl_cache.simkl_cache.delete(key)
	except: pass
	try:
		from caches.lists_cache import lists_cache
		lists_cache.delete(key)
	except: pass

def clear_simkl_list_status_cache(media_kind=None, status=None):
	try:
		from caches.lists_cache import lists_cache
		if media_kind and status:
			_simkl_delete_list_cache_key(_simkl_list_cache_key(media_kind, status))
		elif media_kind:
			if media_kind == 'movies': kinds = ('movies',)
			elif media_kind == 'anime': kinds = ('anime',)
			else: kinds = ('shows', 'anime')
			for kind in kinds:
				for st in _SIMKL_STATUS_KEYS:
					_simkl_delete_list_cache_key(_simkl_list_cache_key(kind, st))
		else:
			for kind in ('movies', 'shows', 'anime'):
				for st in _SIMKL_STATUS_KEYS:
					_simkl_delete_list_cache_key(_simkl_list_cache_key(kind, st))
			lists_cache.delete_like('%s_%%' % _SIMKL_STATUS_CACHE_PREFIX)
	except: pass

def _simkl_list_cache_get(media_kind, status):
	key = _simkl_list_cache_key(media_kind, status)
	data = simkl_cache.simkl_cache.get(key)
	if data is not None: return data
	try:
		from caches.lists_cache import lists_cache
		old = lists_cache.get(key)
		if old is not None:
			simkl_cache.simkl_cache.set(key, old)
			try: lists_cache.delete(key)
			except: pass
			return old
	except: pass
	return None

def _simkl_status_cache_warmed(media_kind):
	for st in _SIMKL_STATUS_KEYS:
		if _simkl_list_cache_get(media_kind, st) is not None:
			return True
	return False

def _simkl_list_item_keys(media_ids):
	keys = set()
	if not isinstance(media_ids, dict): return keys
	for key in ('tmdb', 'imdb', 'tvdb', 'simkl'):
		value = media_ids.get(key)
		if value in _SIMKL_ID_EMPTY: continue
		keys.add('%s:%s' % (key, value))
	return keys

def _simkl_payload_items(data, key):
	if isinstance(data, list): return data
	if not isinstance(data, dict): return []
	items = data.get(key)
	if items is None and key in ('shows', 'anime'):
		items = data.get('shows') or data.get('anime')
	return items if isinstance(items, list) else []

def _simkl_item_block(item, media_kind):
	if media_kind == 'movies': return item.get('movie', {}) or {}
	return item.get('show') or item.get('anime') or {}

def _simkl_normalize_list_item(item, media_kind, order):
	if not isinstance(item, dict) or item.get('is_rewatch'): return None
	media_ids = _simkl_media_ids(item, media_kind)
	if not media_ids: return None
	block = _simkl_item_block(item, media_kind)
	return {'order': order, 'media_ids': media_ids, 'type': 'movie' if media_kind == 'movies' else 'show',
		'title': block.get('title', ''), 'collected_at': item.get('added_to_watchlist_at') or '',
		'released': _simkl_release_key(item, media_kind)}

def _simkl_sort_status_list(result, status, media_kind):
	# Anime shelves use the shows sort default (same episode content type in Red Light).
	sort_media = 'movies' if media_kind == 'movies' else 'shows'
	try: return list_sort.sort_source(result, 'simkl.%s' % status, sort_media, 'simkl')
	except Exception as e:
		kodi_utils.logger('Simkl', 'sort %s/%s failed: %s' % (media_kind, status, e))
		return result

def _simkl_store_status_list(media_kind, status, result):
	try: simkl_cache.simkl_cache.set(_simkl_list_cache_key(media_kind, status), result)
	except: pass

def _simkl_write_status_buckets(media_kind, buckets):
	for st, rows in buckets.items():
		for index, row in enumerate(rows, 1):
			row['order'] = index
		_simkl_store_status_list(media_kind, st, _simkl_sort_status_list(rows, st, media_kind))

def _simkl_items_to_buckets(items, media_kind):
	buckets = {st: [] for st in _SIMKL_STATUS_KEYS}
	unstatused = 0
	for item in items or []:
		if not isinstance(item, dict) or item.get('is_rewatch'): continue
		st = (item.get('status') or '').lower()
		if st not in buckets:
			unstatused += 1
			continue
		entry = _simkl_normalize_list_item(item, media_kind, len(buckets[st]) + 1)
		if entry: buckets[st].append(entry)
	return buckets, unstatused

def _simkl_replace_lists_from_items(items, media_kind):
	if items is None: return
	buckets, unstatused = _simkl_items_to_buckets(items, media_kind)
	if unstatused and not any(buckets.values()): return
	_simkl_write_status_buckets(media_kind, buckets)

def _simkl_load_status_buckets(media_kind):
	buckets = {}
	for st in _SIMKL_STATUS_KEYS:
		cached = _simkl_list_cache_get(media_kind, st)
		buckets[st] = list(cached) if cached else []
	return buckets

def _simkl_remove_list_item(buckets, media_ids):
	keys = _simkl_list_item_keys(media_ids)
	if not keys: return
	for st, rows in buckets.items():
		kept = []
		for row in rows:
			if keys & _simkl_list_item_keys(row.get('media_ids') or {}): continue
			kept.append(row)
		buckets[st] = kept

def _simkl_merge_lists_from_payload(data):
	"""Apply a Phase 2 date_from delta to already-warmed watchlist shelves."""
	if not isinstance(data, dict): return
	for media_kind, key in (('movies', 'movies'), ('shows', 'shows'), ('anime', 'anime')):
		items = _simkl_payload_items(data, key)
		if not items: continue
		if not _simkl_status_cache_warmed(media_kind): continue
		buckets = _simkl_load_status_buckets(media_kind)
		for item in items:
			if not isinstance(item, dict) or item.get('is_rewatch'): continue
			st = (item.get('status') or '').lower()
			media_ids = _simkl_media_ids(item, media_kind)
			if not media_ids: continue
			_simkl_remove_list_item(buckets, media_ids)
			if st not in buckets: continue
			entry = _simkl_normalize_list_item(item, media_kind, len(buckets[st]) + 1)
			if entry: buckets[st].append(entry)
		_simkl_write_status_buckets(media_kind, buckets)

def _simkl_fetch_status_live(media_kind, status):
	items = _simkl_all_items(media_kind, status)
	if items is None: return None
	result, skipped = [], 0
	for count, item in enumerate(items, 1):
		entry = _simkl_normalize_list_item(item, media_kind, count)
		if entry is None:
			if isinstance(item, dict) and not item.get('is_rewatch'): skipped += 1
			continue
		result.append(entry)
	if skipped and not result:
		kodi_utils.logger('Simkl', 'list %s/%s: %s items had no tmdb/imdb/tvdb ids' % (media_kind, status, skipped))
	return _simkl_sort_status_list(result, status, media_kind)

def _simkl_warm_status_caches(media_kind):
	"""Phase 1 fallback: one /sync/all-items/{type}/all pull when local shelves are empty."""
	items = _simkl_all_items(media_kind, 'all')
	if items is None: return False
	buckets, unstatused = _simkl_items_to_buckets(items, media_kind)
	# Rows without status mean we cannot bucket — fall back to per-status fetches.
	if unstatused and not any(buckets.values()):
		kodi_utils.logger('Simkl', 'list %s/all: %s items missing status; using per-status fetch' % (media_kind, unstatused))
		return False
	_simkl_write_status_buckets(media_kind, buckets)
	return True

def _simkl_fetch_status(media_kind, status):
	if not settings.simkl_user_active(): return []
	cached = _simkl_list_cache_get(media_kind, status)
	if cached is not None: return cached
	# Cold shelves only — after Phase 1/2 they live in simkl_db until the next delta.
	if _simkl_warm_status_caches(media_kind):
		cached = _simkl_list_cache_get(media_kind, status)
		if cached is not None: return cached
	result = _simkl_fetch_status_live(media_kind, status)
	result = [] if result is None else result
	_simkl_store_status_list(media_kind, status, result)
	return result

def _simkl_fetch_tv_status(status):
	"""Shows + anime combined (Next Episodes watchlist, manager membership, dropped IDs)."""
	shows = _simkl_fetch_status('shows', status)
	anime = _simkl_fetch_status('anime', status)
	if not shows and not anime: return []
	return list_sort.sort_source(shows + anime, 'simkl.%s' % status, 'shows', 'simkl')

def simkl_plantowatch(media_kind, page_no=None):
	return _simkl_fetch_status(media_kind, 'plantowatch')

def simkl_completed(media_kind, page_no=None):
	return _simkl_fetch_status(media_kind, 'completed')

def simkl_watching(media_kind, page_no=None):
	return _simkl_fetch_status(media_kind, 'watching')

def simkl_hold(media_kind, page_no=None):
	return _simkl_fetch_status(media_kind, 'hold')

def simkl_dropped(media_kind, page_no=None):
	return _simkl_fetch_status(media_kind, 'dropped')

_SIMKL_DROPPED_CACHE_KEY = 'simkl_hidden_items_dropped'

def clear_simkl_dropped_cache():
	simkl_cache.simkl_cache.delete(_SIMKL_DROPPED_CACHE_KEY)

def simkl_get_dropped_items():
	cached = simkl_cache.simkl_cache.get(_SIMKL_DROPPED_CACHE_KEY)
	if cached is not None: return cached
	items = []
	for item in _simkl_fetch_tv_status('dropped'):
		try:
			tmdb_id = item.get('media_ids', {}).get('tmdb')
			if tmdb_id: items.append(int(tmdb_id))
		except: pass
	simkl_cache.simkl_cache.set(_SIMKL_DROPPED_CACHE_KEY, items)
	return items

def _simkl_list_ids(tmdb_id, imdb_id=None, tvdb_id=None, simkl_id=None):
	# Prefer Simkl's own id for season-split anime — parent TMDb/IMDb/TVDb often miss the library row.
	if simkl_id not in _SIMKL_ID_EMPTY:
		try: return {'simkl': int(simkl_id)}
		except: return {'simkl': simkl_id}
	ids = {'tmdb': int(tmdb_id)}
	if imdb_id and imdb_id not in ('None', None, ''): ids['imdb'] = imdb_id
	if tvdb_id and str(tvdb_id) not in ('None', '0', ''):
		try: ids['tvdb'] = int(tvdb_id)
		except: ids['tvdb'] = tvdb_id
	return ids

def _simkl_list_bucket(media_type, media_kind=None):
	if media_type == 'movie': return 'movies'
	if media_kind == 'anime': return 'anime'
	return 'shows'

def _simkl_list_add_ok(result, media_type, media_kind=None):
	if not isinstance(result, dict): return False
	keys = ('movies',) if media_type == 'movie' else ('shows', 'anime')
	added = result.get('added') or {}
	for key in keys:
		if added.get(key): return True
	not_found = result.get('not_found') or {}
	for key in keys:
		if not_found.get(key):
			kodi_utils.logger('Simkl', 'add-to-list not_found: %s' % not_found.get(key))
			break
	return False

def _simkl_list_remove_ok(result, media_type, media_kind=None):
	# Bare True/empty body used to toast Success while deleting nothing (season-split anime miss).
	if not isinstance(result, dict): return False
	keys = ('movies',) if media_type == 'movie' else ('shows', 'anime')
	deleted = result.get('deleted') or {}
	if not isinstance(deleted, dict): return False
	for key in keys:
		val = deleted.get(key)
		if val: return True
	return False

def _simkl_refresh_after_list_change(listname=None, media_type='movie', media_kind=None):
	clear_simkl_dropped_cache()
	if media_type != 'movie': clear_simkl_calendar_cache()
	if settings.simkl_user_active():
		simkl_sync_activities(force_delta=True)
	if kodi_utils.path_check('simkl') or kodi_utils.external():
		kodi_utils.kodi_refresh()

def _simkl_id_match(item_ids, imdb_id=None, tvdb_id=None, tmdb_id=None, simkl_id=None):
	if not isinstance(item_ids, dict): return False
	if simkl_id not in _SIMKL_ID_EMPTY and str(item_ids.get('simkl')) == str(simkl_id): return True
	if imdb_id and imdb_id not in _SIMKL_ID_EMPTY and str(item_ids.get('imdb')) == str(imdb_id): return True
	if tvdb_id and tvdb_id not in _SIMKL_ID_EMPTY and str(item_ids.get('tvdb')) == str(tvdb_id): return True
	if tmdb_id and tmdb_id not in _SIMKL_ID_EMPTY and str(item_ids.get('tmdb')) == str(tmdb_id): return True
	return False

def _simkl_item_in_status(media_type, status, imdb_id=None, tvdb_id=None, tmdb_id=None, simkl_id=None, media_kind=None):
	try:
		if media_type == 'movie':
			items = _simkl_fetch_status('movies', status)
		elif media_kind in ('shows', 'anime'):
			items = _simkl_fetch_status(media_kind, status)
		else:
			items = _simkl_fetch_tv_status(status)
		for item in items:
			if _simkl_id_match(item.get('media_ids'), imdb_id, tvdb_id, tmdb_id, simkl_id): return True
	except: pass
	return False

def simkl_search_my_lists(query):
	query = (query or '').strip().lower()
	if not query or not settings.simkl_user_active(): return []
	results = []
	statuses = ('plantowatch', 'completed', 'watching', 'hold', 'dropped')
	for status in statuses:
		for item in _simkl_fetch_status('movies', status):
			if status in ('watching', 'hold'): continue
			title = (item.get('title') or '').strip()
			if query not in title.lower(): continue
			entry = dict(item)
			entry['result_type'] = 'title'
			entry['status'] = status
			entry['status_label'] = _SIMKL_STATUS_LABELS.get(status, status)
			entry['media_kind'] = 'movies'
			results.append(entry)
	for status in statuses:
		for media_kind in ('shows', 'anime'):
			for item in _simkl_fetch_status(media_kind, status):
				title = (item.get('title') or '').strip()
				if query not in title.lower(): continue
				entry = dict(item)
				entry['result_type'] = 'title'
				entry['status'] = status
				entry['status_label'] = _SIMKL_STATUS_LABELS.get(status, status)
				entry['media_kind'] = media_kind
				results.append(entry)
	if settings.simkl_auth_v2():
		results.extend(_simkl_search_custom_lists(query))
	return results

def _simkl_search_custom_lists(query):
	results = []
	payload = simkl_get_custom_lists()
	if payload.get('premium_only'):
		return results
	skip_contents = False
	for lst in payload.get('lists') or []:
		list_id = lst.get('id')
		name = (lst.get('name') or '').strip()
		if not name or list_id in (None, '', 0, '0'): continue
		if query in name.lower():
			counts = lst.get('counts') or {}
			results.append({
				'result_type': 'list',
				'title': name,
				'list_id': list_id,
				'status_label': 'My Lists',
				'item_count': counts.get('items', 0),
				'description': ((lst.get('description') or {}).get('short')
					or (lst.get('description') or {}).get('full') or '')
			})
		if skip_contents: continue
		contents = simkl_get_custom_list_contents(list_id)
		if contents.get('premium_only'):
			skip_contents = True
			continue
		for item in contents.get('items') or []:
			title = (item.get('title') or '').strip()
			if not title or query not in title.lower(): continue
			media_ids = item.get('media_ids') or {}
			if not media_ids.get('tmdb'): continue
			results.append({
				'result_type': 'title',
				'title': title,
				'status_label': name,
				'media_kind': 'movies' if item.get('type') == 'movie' else 'shows',
				'media_ids': media_ids
			})
	return results

def _simkl_user_id():
	from caches.settings_cache import live_setting
	user_id = live_setting('simkl.user_id', 'empty_setting')
	if user_id not in (None, '', 'empty_setting', '0'):
		return str(user_id)
	_save_simkl_profile()
	user_id = live_setting('simkl.user_id', 'empty_setting')
	if user_id not in (None, '', 'empty_setting', '0'):
		return str(user_id)
	return None

def _simkl_premium_only(payload):
	return isinstance(payload, dict) and payload.get('error') == 'premium_only'

def simkl_get_custom_lists():
	"""User's custom lists. AUTH V2 only. Returns {'lists': [...], 'premium_only': dict|None}."""
	empty = {'lists': [], 'premium_only': None}
	if not settings.simkl_auth_v2(): return empty
	refresh_simkl_account_plan()
	blocked = _simkl_lists_plan_block()
	if blocked: return {'lists': [], 'premium_only': blocked}
	cache_key = 'simkl_custom_lists'
	try:
		from caches.lists_cache import lists_cache
		cached = lists_cache.get(cache_key)
		if cached is not None: return cached
	except: pass
	user_id = _simkl_user_id()
	if not user_id: return empty
	lists, page, limit = [], 1, 50
	premium = None
	while page <= 40:
		path = '/lists/user/%s?limit=%s&page=%s&sort=name&direction=asc' % (user_id, limit, page)
		payload = call_simkl(path, method='get')
		if _simkl_premium_only(payload):
			result = {'lists': [], 'premium_only': payload}
			_simkl_store_custom_cache(cache_key, result)
			return result
		if not isinstance(payload, dict):
			break
		chunk = payload.get('lists')
		if not isinstance(chunk, list):
			break
		lists.extend(chunk)
		pagination = payload.get('pagination') or {}
		total_pages = int(pagination.get('total_pages') or page)
		if page >= total_pages or len(chunk) < limit:
			break
		page += 1
	result = {'lists': lists, 'premium_only': premium}
	_simkl_store_custom_cache(cache_key, result)
	return result

def _simkl_custom_item_type(item):
	kind = str((item or {}).get('type') or '').lower()
	if kind in ('movie', 'movies'): return 'movie'
	if kind in ('tv', 'show', 'shows', 'anime'): return 'show'
	return ''

def _simkl_custom_media_ids(item):
	ids = (item or {}).get('ids') or {}
	if not isinstance(ids, dict): return {}
	media_ids = {}
	tmdb = ids.get('tmdb')
	if tmdb not in _SIMKL_ID_EMPTY:
		try: media_ids['tmdb'] = int(tmdb)
		except: pass
	imdb = ids.get('imdb')
	if imdb not in _SIMKL_ID_EMPTY:
		media_ids['imdb'] = imdb
	tvdb = ids.get('tvdb')
	if tvdb not in _SIMKL_ID_EMPTY:
		try: media_ids['tvdb'] = int(tvdb)
		except: media_ids['tvdb'] = tvdb
	simkl_id = ids.get('simkl_id') or ids.get('simkl')
	if simkl_id not in _SIMKL_ID_EMPTY:
		try: media_ids['simkl'] = int(simkl_id)
		except: pass
	return media_ids

def _simkl_id_list(items, limit=5):
	out = []
	for item in items or []:
		if not isinstance(item, dict): continue
		ids = item.get('ids') or {}
		sid = ids.get('simkl_id')
		if sid in (None, ''): sid = ids.get('simkl')
		if sid in (None, ''): sid = item.get('id')
		if sid in (None, ''): continue
		try: sid = int(sid)
		except: pass
		out.append(sid)
		if len(out) >= limit: break
	return out

def _simkl_head_matches(items, preview_ids):
	"""True when the payload head is the order simkl.com previews for this list."""
	if not preview_ids: return True
	want = []
	for sid in preview_ids:
		try: want.append(int(sid))
		except: want.append(sid)
	head = _simkl_id_list(items, len(want))
	n = min(len(head), len(want))
	return n > 0 and head[:n] == want[:n]

def _simkl_top_ids(top_items):
	ids = []
	for item in top_items or []:
		if not isinstance(item, dict): continue
		sid = item.get('id')
		if sid in (None, ''): continue
		ids.append(sid)
	return ids

def _simkl_preview_ids(list_id, payload):
	"""Website order, from the list preview. Item pages can disagree with that preview."""
	ids = _simkl_top_ids((payload or {}).get('top_items'))
	if ids: return ids
	try:
		known = simkl_get_custom_lists()
		for item in known.get('lists') or []:
			if str(item.get('id')) != str(list_id): continue
			ids = _simkl_top_ids(item.get('top_items'))
			if ids: return ids
	except: pass
	user_id = ((payload or {}).get('user') or {}).get('id')
	if not user_id: return []
	try:
		listed = call_simkl('/lists/user/%s?limit=50&page=1' % user_id, method='get')
		for item in (listed or {}).get('lists') or []:
			if str(item.get('id')) != str(list_id): continue
			return _simkl_top_ids(item.get('top_items'))
	except: pass
	return []

def _simkl_display_query(list_id, preview):
	"""Sort query whose first page matches the simkl.com preview. Empty keeps the API default."""
	# Hyphenated keys are the ones Simkl actually applies. Unknown keys are echoed and ignored.
	for key, direction in (('dvd-date', 'desc'), ('dvd-date', 'asc'), ('air-date', 'desc'), ('air-date', 'asc')):
		payload = call_simkl('/lists/%s?limit=5&page=1&sort=%s&direction=%s' % (list_id, key, direction), method='get')
		chunk = payload.get('items') if isinstance(payload, dict) else None
		if _simkl_head_matches(chunk, preview):
			return '&sort=%s&direction=%s' % (key, direction)
	return ''

def _simkl_custom_release(item):
	"""First-release day. The DVD day is a different Simkl sort, so it is not used here."""
	item = item or {}
	val = item.get('release_date')
	if val not in (None, '', 'None'): return str(val)[:10]
	year = item.get('year')
	if year not in (None, '', 'None', 0, '0'):
		try: return '%04d-01-01' % int(year)
		except (TypeError, ValueError): pass
	return ''

def simkl_get_custom_list_contents(list_id):
	"""Items in one custom list. AUTH V2 + PRO/VIP. Returns {'items': [...], 'premium_only': dict|None}."""
	empty = {'items': [], 'premium_only': None}
	if not settings.simkl_auth_v2() or list_id in (None, '', 0, '0'): return empty
	refresh_simkl_account_plan()
	blocked = _simkl_lists_plan_block()
	if blocked: return {'items': [], 'premium_only': blocked}
	cache_key = 'simkl_custom_list_v3_%s' % list_id
	try:
		from caches.lists_cache import lists_cache
		cached = lists_cache.get(cache_key)
		if cached is not None: return cached
	except: pass
	probe = call_simkl('/lists/%s?limit=5&page=1' % list_id, method='get')
	if _simkl_premium_only(probe):
		result = {'items': [], 'premium_only': probe}
		_simkl_store_custom_cache(cache_key, result)
		return result
	query = ''
	if isinstance(probe, dict):
		preview = _simkl_preview_ids(list_id, probe)
		chunk = probe.get('items') if isinstance(probe.get('items'), list) else []
		if preview and not _simkl_head_matches(chunk, preview):
			query = _simkl_display_query(list_id, preview)
	rows, page, limit = [], 1, 500
	while page <= 40:
		path = '/lists/%s?limit=%s&page=%s%s' % (list_id, limit, page, query)
		payload = call_simkl(path, method='get')
		if _simkl_premium_only(payload):
			result = {'items': [], 'premium_only': payload}
			_simkl_store_custom_cache(cache_key, result)
			return result
		if not isinstance(payload, dict):
			break
		chunk = payload.get('items')
		if not isinstance(chunk, list):
			break
		for index, item in enumerate(chunk):
			item_type = _simkl_custom_item_type(item)
			media_ids = _simkl_custom_media_ids(item)
			if not item_type or not (media_ids.get('tmdb') or media_ids.get('imdb') or media_ids.get('tvdb')):
				continue
			position = item.get('position')
			try: order = int(position) - 1 if position not in (None, '') else len(rows) + index
			except: order = len(rows) + index
			rows.append({
				'type': item_type,
				'media_ids': media_ids,
				'order': order,
				'title': (item.get('title') or '').strip(),
				'release_date': _simkl_custom_release(item)
			})
		pagination = payload.get('pagination') or {}
		total_pages = int(pagination.get('total_pages') or page)
		if page >= total_pages or len(chunk) < limit:
			break
		page += 1
	result = {'items': rows, 'premium_only': None}
	_simkl_store_custom_cache(cache_key, result)
	return result

def _simkl_store_custom_cache(cache_key, result):
	try:
		from caches.lists_cache import lists_cache
		lists_cache.set(cache_key, result, expiration=settings.lists_cache_duraton())
	except: pass

def _simkl_custom_list_contains(list_id, tmdb_id, imdb_id, tvdb_id):
	payload = simkl_get_custom_list_contents(list_id)
	want_tmdb = str(tmdb_id or '').strip()
	want_imdb = str(imdb_id or '').strip()
	want_tvdb = str(tvdb_id or '').strip()
	for item in payload.get('items') or []:
		ids = item.get('media_ids') or {}
		if want_tmdb and str(ids.get('tmdb') or '') == want_tmdb: return True
		if want_imdb and str(ids.get('imdb') or '') == want_imdb: return True
		if want_tvdb and str(ids.get('tvdb') or '') == want_tvdb: return True
	return False

def simkl_lists_with_media(tmdb_id, imdb_id, tvdb_id):
	if not settings.simkl_custom_lists_allowed(): return []
	payload = simkl_get_custom_lists()
	if payload.get('premium_only'): return []
	lists = payload.get('lists') or []
	results = []
	def _check(item):
		list_id = item.get('id')
		if list_id in (None, '', 0, '0'): return
		if not _simkl_custom_list_contains(list_id, tmdb_id, imdb_id, tvdb_id): return
		counts = item.get('counts') or {}
		results.append({
			'id': list_id,
			'name': item.get('name') or 'Untitled',
			'item_count': counts.get('items', 0),
		})
	if lists:
		from modules.utils import TaskPool
		threads = TaskPool().tasks(_check, lists, min(len(lists), settings.max_threads()) or 1)
		[i.join() for i in threads]
	results.sort(key=lambda k: (k.get('name') or '').lower())
	return results

def simkl_manager_choice(params):
	if not settings.simkl_user_active(): return kodi_utils.notification('No Active Simkl Account', 3500)
	media_type = params.get('media_type') or params.get('content') or 'movie'
	list_media = 'movie' if media_type == 'movie' else 'tvshow'
	media_kind = params.get('simkl_media_kind') or None
	icon = params.get('icon') or kodi_utils.get_icon('simkl')
	imdb_id, tvdb_id, tmdb_id = params.get('imdb_id'), params.get('tvdb_id'), params.get('tmdb_id')
	simkl_id = params.get('simkl_id')
	status_map = [
		('plantowatch', 'Add to [B]Plan to Watch[/B]', 'Remove from [B]Plan to Watch[/B]'),
		('completed', 'Add to [B]Completed[/B]', 'Remove from [B]Completed[/B]'),
		('dropped', 'Add to [B]Dropped[/B]', 'Remove from [B]Dropped[/B]')
	]
	if media_type != 'movie':
		status_map.insert(1, ('watching', 'Add to [B]Watching[/B]', 'Remove from [B]Watching[/B]'))
		status_map.insert(3, ('hold', 'Add to [B]On Hold[/B]', 'Remove from [B]On Hold[/B]'))
	choices = []
	kind = media_kind if media_kind in ('shows', 'anime', 'movies') else None
	for status, add_label, remove_label in status_map:
		if _simkl_item_in_status(list_media, status, imdb_id, tvdb_id, tmdb_id, simkl_id, kind):
			choices.append((remove_label, 'remove_%s' % status))
		else:
			choices.append((add_label, status))
	from indexers.dialogs import _manager_mark_watched_choices
	choices.extend(_manager_mark_watched_choices(params))
	choices.extend([
		('Reset [B]Scrobble[/B]', 'reset_scrobble'),
		('Open [B]Plan to Watch[/B]', 'open_plantowatch'),
		('Open [B]Completed[/B]', 'open_completed'),
	])
	if media_type != 'movie':
		choices.append(('Open [B]Watching[/B]', 'open_watching'))
		choices.append(('Open [B]On Hold[/B]', 'open_hold'))
	choices.extend([
		('Open [B]Dropped[/B]', 'open_dropped'),
		('Open [B]Simkl Lists[/B]', 'open_lists'),
		('Refresh Widgets', 'refresh'),
	])
	if settings.simkl_auth_v2():
		choices.insert(-1, ('Open [B]My Lists[/B]', 'open_my_lists'))
	list_items = [{'line1': item[0], 'icon': icon} for item in choices]
	choice = kodi_utils.select_dialog([i[1] for i in choices], **{'items': json.dumps(list_items), 'heading': 'Simkl Lists Manager'})
	if choice == None: return
	if choice == 'refresh':
		kodi_utils.kodi_refresh()
		return kodi_utils.notification('Widgets Refreshed', 2500)
	open_modes = {
		'open_plantowatch': 'navigator.simkl_watchlists',
		'open_completed': 'navigator.simkl_completed',
		'open_watching': 'navigator.simkl_watching',
		'open_hold': 'navigator.simkl_hold',
		'open_dropped': 'navigator.simkl_dropped',
		'open_lists': 'navigator.simkl_lists',
		'open_my_lists': 'simkl.list.get_simkl_custom_lists',
	}
	if choice in open_modes:
		return kodi_utils.container_update({'mode': open_modes[choice]})
	if choice == 'mark_watched':
		from indexers.dialogs import _trakt_manager_mark
		return _trakt_manager_mark(params, 'mark_as_watched')
	if choice == 'mark_unwatched':
		from indexers.dialogs import _trakt_manager_mark
		return _trakt_manager_mark(params, 'mark_as_unwatched')
	if choice == 'reset_scrobble':
		return simkl_reset_scrobble(params)
	if choice in ('plantowatch', 'watching', 'completed', 'hold', 'dropped'):
		return simkl_add_to_list(choice, tmdb_id, list_media, imdb_id, tvdb_id, simkl_id, media_kind)
	if choice.startswith('remove_'):
		return simkl_remove_from_list(choice.replace('remove_', ''), tmdb_id, list_media, imdb_id, tvdb_id, simkl_id, media_kind)

def simkl_hide_unhide_progress_items(params):
	action, media_id = params['action'], params.get('media_id')
	imdb_id, tvdb_id = params.get('imdb_id'), params.get('tvdb_id', 'None')
	if action == 'drop': return simkl_add_to_list('dropped', media_id, 'tvshow', imdb_id, tvdb_id)
	return simkl_remove_from_list('dropped', media_id, 'tvshow', imdb_id, tvdb_id)

def _simkl_history_counts_ok(result, action, media_type):
	"""True when Simkl reports added/deleted counts > 0 (not a silent no-op)."""
	if not isinstance(result, dict): return False
	result_key = 'added' if action == 'mark_as_watched' else 'deleted'
	bucket = result.get(result_key) or {}
	if media_type == 'movie':
		keys = ('movies',)
	else:
		keys = ('shows', 'anime', 'episodes')
	if bucket.get('episodes', 0) > 0: return True
	for item_key in keys:
		val = bucket.get(item_key, 0)
		if isinstance(val, list) and val: return True
		try:
			if int(val or 0) > 0: return True
		except Exception:
			pass
	return False

def _simkl_history_not_found(result):
	if not isinstance(result, dict): return False
	nf = result.get('not_found') or {}
	for key in ('movies', 'shows', 'anime', 'episodes'):
		val = nf.get(key)
		if isinstance(val, list) and val: return True
		try:
			if int(val or 0) > 0: return True
		except Exception:
			pass
	return False

def _simkl_added_episodes(result, action='mark_as_watched'):
	if not isinstance(result, dict): return 0
	key = 'added' if action == 'mark_as_watched' else 'deleted'
	try: return int((result.get(key) or {}).get('episodes') or 0)
	except Exception:
		return 0

def _simkl_regular_season_numbers(tmdb_id):
	try:
		from modules import metadata
		from modules.utils import get_datetime
		meta = metadata.tvshow_meta('tmdb_id', tmdb_id, settings.tmdb_api_key(), settings.mpaa_region(), get_datetime())
	except Exception:
		meta = None
	nums, seen = [], set()
	for item in (meta or {}).get('season_data') or []:
		try: n = int(item.get('season_number'))
		except Exception: continue
		if n > 0 and n not in seen:
			seen.add(n)
			nums.append(n)
	return nums

def _simkl_tmdb_is_anime(tmdb_id):
	"""TMDb anime keyword — used to choose Simkl anime[] history vs shows[]."""
	try:
		from modules.metadata import is_anime_check
		return bool(is_anime_check(tmdb_id=str(tmdb_id)))
	except Exception:
		return False

def _simkl_history_tv_entry(tmdb_id, tvdb_id=0, season=None, episode=None, watched_at=None, use_tvdb_anime_seasons=False):
	entry = {'ids': _simkl_list_ids(tmdb_id, tvdb_id=tvdb_id)}
	# Kodi/TMDb season numbering for anime needs this flag or Simkl may only set watching status.
	if use_tvdb_anime_seasons:
		entry['use_tvdb_anime_seasons'] = True
	if season is not None and episode is not None:
		ep = {'number': int(episode)}
		if watched_at: ep['watched_at'] = watched_at
		entry['seasons'] = [{'number': int(season), 'episodes': [ep]}]
	elif season is not None:
		entry['seasons'] = [{'number': int(season)}]
	return entry

def _simkl_history_expand_seasons(url, tmdb_id, tvdb_id, bucket, use_tvdb):
	"""Whole-show history can move Completed without episode timestamps (Force Sync then undoes local ticks).
	Season-number POSTs are Simkl's documented 'mark every episode in this season'."""
	nums = _simkl_regular_season_numbers(tmdb_id)
	if not nums: return False, None
	entry = _simkl_history_tv_entry(tmdb_id, tvdb_id, None, None, None, use_tvdb)
	entry['seasons'] = [{'number': n} for n in nums]
	result = call_simkl_rewatch(url, data={bucket: [entry]})
	kodi_utils.logger('Simkl', 'history show season-expand tmdb=%s seasons=%s added_episodes=%s' % (
		tmdb_id, len(nums), _simkl_added_episodes(result)))
	return True, result

def simkl_watched_status_mark(action, media_type, tmdb_id, tvdb_id=0, season=None, episode=None):
	if action == 'mark_as_watched':
		url = _simkl_rewatch_path('/sync/history')
		watched_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
	else:
		url = '/sync/history/remove'
		watched_at = None
	if media_type == 'movie':
		item = {'ids': {'tmdb': int(tmdb_id)}}
		if watched_at: item['watched_at'] = watched_at
		result = call_simkl_rewatch(url, data={'movies': [item]}) if action == 'mark_as_watched' else call_simkl(url, data={'movies': [item]})
		if _simkl_history_counts_ok(result, action, 'movie'):
			return True
		# Already clear on Simkl.
		if action == 'mark_as_unwatched' and isinstance(result, dict): return True
		# Add with 0 added = already watched (e.g. finished after mid-play pause, or marked in Simkl app).
		# Same idea as Trakt — not a failure unless Simkl reports not_found.
		if action == 'mark_as_watched' and isinstance(result, dict) and not _simkl_history_not_found(result):
			if _simkl_rewatch_denied(result): return False
			return True
		kodi_utils.logger('Simkl', 'history %s failed for movie tmdb=%s: %s' % (action, tmdb_id, result))
		return False
	# TV / episode / season — Simkl stores many titles under anime[], not shows[].
	if media_type in ('episode',):
		item_type, s_num, e_num = 'episode', season, episode
	elif media_type == 'season':
		item_type, s_num, e_num = 'season', season, None
	else:
		item_type, s_num, e_num = 'tvshow', None, None
	known_anime = _simkl_tmdb_is_anime(tmdb_id)
	# history/remove: top-level anime[] is silently ignored — must use shows[].
	# history add: anime[] is valid; prefer it for known anime then fall back to shows[].
	if action == 'mark_as_unwatched':
		attempts = (('shows', True), ('shows', False)) if known_anime else (('shows', False), ('shows', True))
	elif known_anime:
		attempts = (('anime', True), ('shows', True))
	else:
		attempts = (('shows', False), ('anime', True), ('shows', True))
	last_result = None
	saw_not_found = False
	shelf_only = False
	for bucket, use_tvdb in attempts:
		entry = _simkl_history_tv_entry(tmdb_id, tvdb_id, s_num, e_num, watched_at, use_tvdb)
		# Documented whole-show expand: no seasons/episodes + status=completed.
		if action == 'mark_as_watched' and item_type == 'tvshow':
			entry['status'] = 'completed'
		last_result = call_simkl_rewatch(url, data={bucket: [entry]}) if action == 'mark_as_watched' else call_simkl(url, data={bucket: [entry]})
		# Network/HTTP failure (None) — do not stack more long timeouts on other buckets.
		if last_result is None:
			kodi_utils.logger('Simkl', 'history %s network failure for %s tmdb=%s tvdb=%s' % (action, item_type, tmdb_id, tvdb_id))
			return False
		if _simkl_history_counts_ok(last_result, action, 'shows'):
			if action != 'mark_as_watched' or item_type != 'tvshow' or _simkl_added_episodes(last_result) > 0:
				return True
			# added.shows>0 with 0 episodes: shelf-only. Expand via season numbers (#238).
			shelf_only = True
			kodi_utils.logger('Simkl', 'history mark_as_watched show added.episodes=0 tmdb=%s, expanding seasons' % tmdb_id)
			posted, expanded = _simkl_history_expand_seasons(url, tmdb_id, tvdb_id, bucket, use_tvdb)
			if not posted:
				continue
			if expanded is None:
				kodi_utils.logger('Simkl', 'history season-expand network failure for tvshow tmdb=%s tvdb=%s' % (tmdb_id, tvdb_id))
				return False
			last_result = expanded
			if _simkl_added_episodes(expanded) > 0:
				return True
			continue
		if action == 'mark_as_unwatched' and _simkl_history_not_found(last_result):
			saw_not_found = True
	if action == 'mark_as_unwatched' and item_type == 'tvshow' and tvdb_id and int(tvdb_id) > 0:
		fallback = call_simkl(url, data={'shows': [{'ids': {'tvdb': int(tvdb_id)}}]})
		if fallback is None:
			kodi_utils.logger('Simkl', 'history %s network failure for %s tmdb=%s tvdb=%s' % (action, item_type, tmdb_id, tvdb_id))
			return False
		if _simkl_history_counts_ok(fallback, action, 'shows'):
			return True
		if _simkl_history_not_found(fallback): saw_not_found = True
		last_result = fallback
	# Unwatched already clear on Simkl (explicit not_found). Do not treat bare deleted:0 as success —
	# that was hiding silent no-ops when remove used the wrong envelope.
	if action == 'mark_as_unwatched' and saw_not_found:
		kodi_utils.logger('Simkl', 'history mark_as_unwatched already clear for %s tmdb=%s tvdb=%s' % (item_type, tmdb_id, tvdb_id))
		return True
	# Add with 0 added across buckets = already watched — not a failure unless not_found.
	# Show-level added.shows with 0 episodes is not already-watched: shelf moved, ticks did not.
	if action == 'mark_as_watched' and item_type == 'tvshow' and shelf_only:
		kodi_utils.logger('Simkl', 'history mark_as_watched show no episode expansion tmdb=%s tvdb=%s: %s' % (tmdb_id, tvdb_id, last_result))
		return False
	if action == 'mark_as_watched' and isinstance(last_result, dict) and not _simkl_history_not_found(last_result):
		if _simkl_rewatch_denied(last_result): return False
		return True
	kodi_utils.logger('Simkl', 'history %s failed for %s tmdb=%s tvdb=%s: %s' % (action, item_type, tmdb_id, tvdb_id, last_result))
	return False

def _scrobble_payload(media_type, tmdb_id, percent, season=None, episode=None):
	data = {'progress': float(percent)}
	if media_type == 'movie':
		data['movie'] = {'ids': {'tmdb': int(tmdb_id)}}
	else:
		data['show'] = {'ids': {'tmdb': int(tmdb_id)}}
		data['episode'] = {'season': int(season), 'number': int(episode)}
	return data

def simkl_official_status(media_type):
	if kodi_utils.service_scrobbler_defer('script.simkl',
		auth_keys=('access_token', 'token', 'authorization', 'Authorization', 'simkl_token'),
		scrobble_enable_keys=('auto_scrobble', 'autoscrobble', 'scrobble_enabled', 'auto_scrobble_enabled')): return False
	return True

def simkl_scrobble(action, media_type, tmdb_id, percent=0, season=None, episode=None):
	if not settings.simkl_user_active(): return
	path = {'start': '/scrobble/start', 'pause': '/scrobble/pause', 'stop': '/scrobble/stop'}.get(action)
	if not path: return
	payload = _scrobble_payload(media_type, tmdb_id, percent, season, episode)
	# allow_rewatch is stop-only. Never send it on start (Simkl rejects it) or pause.
	if action == 'stop':
		call_simkl_rewatch(_simkl_rewatch_path(path), data=payload)
		return
	call_simkl(path, data=payload)

def simkl_progress(action, media_type, tmdb_id, percent, season=None, episode=None, resume_id=None, refresh_simkl=False):
	if action == 'clear_progress' and resume_id:
		_throttle()
		url = _url('/sync/playback/%s' % resume_id)
		if not url: return
		try: requests.delete(url, headers=_headers(), timeout=META_API_TIMEOUT)
		except: pass
	else:
		simkl_scrobble('pause', media_type, tmdb_id, percent, season, episode)
	if refresh_simkl: simkl_sync_activities()

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
			erase_bookmark('movie', tmdb_id, '', '', 'true', 2)
		elif media_type == 'episode' and season and episode:
			simkl_scrobble('stop', 'episode', tmdb_id, 0, season, episode)
			row = watched_db.execute('SELECT resume_id FROM progress WHERE db_type=? AND media_id=? AND season=? AND episode=?',
				('episode', str(tmdb_id), int(season), int(episode))).fetchone()
			if row:
				simkl_progress('clear_progress', 'episode', tmdb_id, 0, season, episode, resume_id=row[0])
			erase_bookmark('episode', tmdb_id, season, episode, 'true', 2)
		else: return kodi_utils.notification('Reset Scrobble is only available for movies and episodes', 3500)
		kodi_utils.notification('Success', 3000)
	except: kodi_utils.notification('Error', 3000)

def _simkl_hide_progress_locally(tmdb_id, action):
	"""Hide/unhide In Progress when Simkl has no catalog row (e.g. TMDb-only miniseries)."""
	try:
		from modules.watched_status import hide_unhide_progress_items
		hide_unhide_progress_items({'action': action, 'media_id': tmdb_id, 'refresh': 'false'})
		return True
	except Exception:
		return False

def simkl_add_to_list(listname, tmdb_id, media_type, imdb_id=None, tvdb_id=None, simkl_id=None, media_kind=None):
	bucket = _simkl_list_bucket(media_type, media_kind)
	ids = _simkl_list_ids(tmdb_id, imdb_id, tvdb_id, simkl_id)
	post = {bucket: [{'to': listname, 'ids': ids}]}
	result = call_simkl('/sync/add-to-list', data=post)
	success = _simkl_list_add_ok(result, media_type, media_kind)
	if success:
		_simkl_refresh_after_list_change(listname, media_type, media_kind)
		kodi_utils.notify_success()
		return True
	if media_type != 'movie' and listname == 'dropped' and _simkl_hide_progress_locally(tmdb_id, 'drop'):
		_simkl_refresh_after_list_change(listname, media_type, media_kind)
		kodi_utils.notification('Simkl does not have this title. Hidden from In Progress.', 4000)
		return True
	kodi_utils.notify_error()
	return False

def simkl_remove_from_list(listname, tmdb_id, media_type, imdb_id=None, tvdb_id=None, simkl_id=None, media_kind=None):
	bucket = _simkl_list_bucket(media_type, media_kind)
	ids = _simkl_list_ids(tmdb_id, imdb_id, tvdb_id, simkl_id)
	post = {bucket: [{'ids': ids}]}
	result = call_simkl('/sync/history/remove', data=post)
	success = _simkl_list_remove_ok(result, media_type, media_kind)
	# Season-split anime often only matches under anime[]; some removes only clear via shows[].
	if not success and bucket == 'anime':
		result = call_simkl('/sync/history/remove', data={'shows': [{'ids': ids}]})
		success = _simkl_list_remove_ok(result, media_type, media_kind)
	if success:
		_simkl_refresh_after_list_change(listname, media_type, media_kind)
		kodi_utils.notify_success()
		return True
	if media_type != 'movie' and listname == 'dropped' and _simkl_hide_progress_locally(tmdb_id, 'undrop'):
		_simkl_refresh_after_list_change(listname, media_type, media_kind)
		kodi_utils.notify_success()
		return True
	kodi_utils.notify_not_in_list()
	return False

_SIMKL_TV_SYNC_QUERY = 'extended=full&episode_watched_at=yes&include_all_episodes=yes'
# full_anime_seasons adds tvdb {season,episode} so Kodi/TMDb SxE matches AniDB-backed library rows.
_SIMKL_ANIME_SYNC_QUERY = 'extended=full_anime_seasons&episode_watched_at=yes&include_all_episodes=yes'
# Phase 2: one /sync/all-items?date_from=… for movies + shows + anime (https://api.simkl.org/guides/sync#phase-2-continuous-sync).
_SIMKL_PHASE2_ALL_QUERY = 'extended=full_anime_seasons&episode_watched_at=yes&include_all_episodes=yes'
# Keep ISO-8601 from /sync/activities; do not percent-encode ':' or 'Z'.
_SIMKL_DATE_FROM_SAFE = ':-T.Z+'

def _simkl_date_from(cached_activities):
	"""Previous activities.all for Phase 2 date_from — None means full (Phase 1) pull."""
	ts = str((cached_activities or {}).get('all') or '').strip()
	if not ts: return None
	# Sentinel from default_activities() / never-synced installs.
	if ts.startswith('2020-01-01'): return None
	return ts

def _simkl_with_date_from(query, date_from=None):
	if date_from:
		query = '%s&date_from=%s' % (query, quote(str(date_from).strip(), safe=_SIMKL_DATE_FROM_SAFE))
	return _simkl_rewatch_qs(query)

def _simkl_ids_from_all_item(item, media_kind):
	if not isinstance(item, dict): return {}
	block = _simkl_item_block(item, media_kind)
	ids = {}
	if isinstance(block, dict): ids = block.get('ids') or {}
	if not isinstance(ids, dict) or not ids:
		ids = item.get('ids') or {}
	if not isinstance(ids, dict) or not ids:
		ids = {key: item.get(key) for key in ('tmdb', 'imdb', 'tvdb', 'simkl') if item.get(key) not in _SIMKL_ID_EMPTY}
	return _simkl_ids_from_dict(ids)

def _simkl_reconcile_ids_only():
	"""Deletions are not in date_from — diff a cheap ids_only snapshot against local cache."""
	data = call_simkl('/sync/all-items?extended=ids_only', method='get')
	if not isinstance(data, dict): return
	movie_tmdb, tv_tmdb = set(), set()
	list_keys = {'movies': set(), 'shows': set(), 'anime': set()}
	movie_rows = data.get('movies')
	show_rows = data.get('shows')
	anime_rows = data.get('anime')
	if isinstance(movie_rows, list):
		for item in movie_rows:
			media_ids = _simkl_ids_from_all_item(item, 'movies')
			list_keys['movies'] |= _simkl_list_item_keys(media_ids)
			tmdb = _tmdb_id(media_ids)
			if tmdb: movie_tmdb.add(tmdb)
	if isinstance(show_rows, list):
		for item in show_rows:
			media_ids = _simkl_ids_from_all_item(item, 'shows')
			list_keys['shows'] |= _simkl_list_item_keys(media_ids)
			tmdb = _tmdb_id(media_ids)
			if tmdb: tv_tmdb.add(tmdb)
	if isinstance(anime_rows, list):
		for item in anime_rows:
			media_ids = _simkl_ids_from_all_item(item, 'anime')
			list_keys['anime'] |= _simkl_list_item_keys(media_ids)
			tmdb = _tmdb_id(media_ids)
			if tmdb: tv_tmdb.add(tmdb)
	prune_movies = isinstance(movie_rows, list) and (movie_tmdb or not movie_rows)
	prune_tv = (isinstance(show_rows, list) or isinstance(anime_rows, list)) and (tv_tmdb or not ((show_rows or []) + (anime_rows or [])))
	if prune_movies or prune_tv:
		simkl_cache.simkl_watched_cache.prune_not_in(
			keep_movie_ids=movie_tmdb if prune_movies else None,
			keep_episode_media_ids=tv_tmdb if prune_tv else None)
	for media_kind, rows in (('movies', movie_rows), ('shows', show_rows), ('anime', anime_rows)):
		if not isinstance(rows, list) or not _simkl_status_cache_warmed(media_kind): continue
		keep = list_keys[media_kind]
		if rows and not keep: continue
		buckets = _simkl_load_status_buckets(media_kind)
		for st, bucket_rows in buckets.items():
			kept = [row for row in bucket_rows if _simkl_list_item_keys(row.get('media_ids') or {}) & keep]
			buckets[st] = kept
		_simkl_write_status_buckets(media_kind, buckets)

def _simkl_apply_movie_watched(data, date_from=None, filter_status=False):
	"""Apply movies[] from an all-items payload into the local watched cache."""
	insert_list = []
	insert_append = insert_list.append
	unwatch = []
	for item in _simkl_payload_items(data, 'movies'):
		try:
			movie = item.get('movie', item)
			tmdb_id = _tmdb_id(movie.get('ids', {}))
			if not tmdb_id: continue
			status = str(item.get('status') or '').lower()
			watched_at = item.get('last_watched_at') or item.get('watched_at')
			is_watched = bool(watched_at) or status == 'completed'
			if filter_status and not is_watched:
				if date_from: unwatch.append(tmdb_id)
				continue
			if not watched_at: watched_at = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
			insert_append(('movie', tmdb_id, '', '', watched_at, movie.get('title', '')))
		except: pass
	if date_from:
		if unwatch: simkl_cache.simkl_watched_cache.delete_movie_watched(unwatch)
		simkl_cache.simkl_watched_cache.merge_bulk_movie_watched(insert_list)
	else:
		simkl_cache.simkl_watched_cache.set_bulk_movie_watched(insert_list)

def simkl_indicators_movies(date_from=None, progress=None):
	kodi_utils.sync_progress(progress, 'Watched movies', 12)
	path = '/sync/all-items/movies?%s' % _simkl_with_date_from('extended=full', date_from)
	data = call_simkl_rewatch(path, method='get') or {}
	_simkl_apply_movie_watched(data, date_from, filter_status=True)
	if not date_from:
		_simkl_replace_lists_from_items(_simkl_payload_items(data, 'movies'), 'movies')
	kodi_utils.sync_progress(progress, 'Watched movies', 40)

def _simkl_drop_specials_mirrors(rows):
	"""Keep S01E01 over a same-timestamp S00E01 duplicate from include_all_episodes."""
	regular = set()
	for row in rows:
		try:
			if int(row[2]) != 0: regular.add((str(row[1]), int(row[3]), row[4]))
		except Exception:
			pass
	kept = []
	for row in rows:
		try:
			if int(row[2]) == 0 and (str(row[1]), int(row[3]), row[4]) in regular:
				continue
		except Exception:
			pass
		kept.append(row)
	return kept

def _simkl_append_tv_watched(insert_append, data, item_key, touched_ids=None):
	"""Flatten shows[] or anime[] all-items into local episode watched rows."""
	items = data.get(item_key, data if isinstance(data, list) else [])
	pending = []
	pending_append = pending.append
	for item in items:
		try:
			if item_key == 'anime':
				show = item.get('anime') or item.get('show') or item
			else:
				show = item.get('show') or item
			tmdb_id = _tmdb_id(show.get('ids', {}))
			if not tmdb_id: continue
			if touched_ids is not None: touched_ids.add(str(tmdb_id))
			title = show.get('title', '')
			for season in item.get('seasons', []):
				try: snum = int(season.get('number', season.get('season')))
				except: continue
				for ep in season.get('episodes', []):
					watched_at = ep.get('watched_at') or ep.get('last_watched_at')
					if not watched_at: continue
					# Prefer TVDB-mapped S/E for anime so Red Light matches TMDb season lists.
					tvdb_map = ep.get('tvdb') if isinstance(ep.get('tvdb'), dict) else None
					try:
						if tvdb_map and tvdb_map.get('season') is not None and tvdb_map.get('episode') is not None:
							ep_snum = int(tvdb_map['season'])
							epnum = int(tvdb_map['episode'])
						else:
							ep_snum = snum
							epnum = int(ep.get('number', ep.get('episode')))
					except Exception:
						continue
					pending_append(('episode', tmdb_id, ep_snum, epnum, watched_at, title))
		except: pass
	for row in _simkl_drop_specials_mirrors(pending):
		insert_append(row)

def _simkl_apply_tv_watched(data, date_from=None):
	"""Apply shows[] + anime[] from an all-items payload into the local watched cache."""
	insert_list = []
	insert_append = insert_list.append
	touched_ids = set() if date_from else None
	_simkl_append_tv_watched(insert_append, data or {}, 'shows', touched_ids)
	_simkl_append_tv_watched(insert_append, data or {}, 'anime', touched_ids)
	if date_from:
		simkl_cache.simkl_watched_cache.merge_bulk_tvshow_watched(insert_list, touched_ids)
	else:
		simkl_cache.simkl_watched_cache.set_bulk_tvshow_watched(insert_list)

def simkl_indicators_tv(date_from=None, progress=None):
	if date_from:
		data = call_simkl_rewatch('/sync/all-items?%s' % _simkl_with_date_from(_SIMKL_PHASE2_ALL_QUERY, date_from), method='get') or {}
		_simkl_apply_tv_watched(data, date_from)
		_simkl_merge_lists_from_payload(data)
		return data
	# Phase 1: sequential per-type full pulls (Simkl guidance for large libraries).
	insert_list = []
	insert_append = insert_list.append
	kodi_utils.sync_progress(progress, 'Watched shows', 42)
	shows = call_simkl_rewatch('/sync/all-items/shows?%s' % _simkl_rewatch_qs(_SIMKL_TV_SYNC_QUERY), method='get') or {}
	_simkl_append_tv_watched(insert_append, shows, 'shows')
	_simkl_replace_lists_from_items(_simkl_payload_items(shows, 'shows'), 'shows')
	kodi_utils.sync_progress(progress, 'Watched shows', 68)
	kodi_utils.sync_progress(progress, 'Watched anime', 70)
	anime = call_simkl_rewatch('/sync/all-items/anime?%s' % _simkl_rewatch_qs(_SIMKL_ANIME_SYNC_QUERY), method='get') or {}
	_simkl_append_tv_watched(insert_append, anime, 'anime')
	_simkl_replace_lists_from_items(_simkl_payload_items(anime, 'anime'), 'anime')
	simkl_cache.simkl_watched_cache.set_bulk_tvshow_watched(insert_list)
	kodi_utils.sync_progress(progress, 'Watched anime', 88)
	return {'shows': _simkl_payload_items(shows, 'shows'), 'anime': _simkl_payload_items(anime, 'anime')}

def simkl_indicators_phase2(date_from):
	"""Phase 2 continuous sync: one /sync/all-items?date_from= for movies + shows + anime."""
	if not date_from: return {}
	data = call_simkl_rewatch('/sync/all-items?%s' % _simkl_with_date_from(_SIMKL_PHASE2_ALL_QUERY, date_from), method='get') or {}
	if not isinstance(data, dict): data = {}
	_simkl_apply_movie_watched(data, date_from, filter_status=True)
	_simkl_apply_tv_watched(data, date_from)
	return data

def simkl_sync_playback(progress=None):
	kodi_utils.sync_progress(progress, 'In progress', 92)
	items = call_simkl('/sync/playback', method='get') or []
	movie_ins, ep_ins = [], []
	for item in items:
		try:
			percent = float(item.get('progress') or 0)
			if percent <= 1: continue
			if item.get('type') == 'movie':
				tmdb_id = _tmdb_id(item.get('movie', {}).get('ids', {}))
				if not tmdb_id: continue
				movie_ins.append(('movie', tmdb_id, '', '', str(round(percent, 1)), 0, item.get('paused_at', ''), item['id'], item['movie'].get('title', '')))
			elif item.get('type') == 'episode':
				show = item.get('show', {})
				tmdb_id = _tmdb_id(show.get('ids', {}))
				if not tmdb_id: continue
				ep = item.get('episode', {})
				ep_ins.append(('episode', tmdb_id, ep.get('season'), ep.get('number'), str(round(percent, 1)), 0,
					item.get('paused_at', ''), item['id'], show.get('title', '')))
		except: pass
	simkl_cache.simkl_watched_cache.set_bulk_movie_progress(movie_ins)
	simkl_cache.simkl_watched_cache.set_bulk_tvshow_progress(ep_ins)
	kodi_utils.sync_progress(progress, 'In progress', 100)

def _activity_ts(ts_str):
	if not ts_str: return 0
	try: return int(calendar.timegm(time.strptime(ts_str.rstrip('Z').split('.')[0], '%Y-%m-%dT%H:%M:%S')))
	except: return 0

def _activity_block_changed(latest_blk, cached_blk, keys):
	for key in keys:
		if _activity_ts(latest_blk.get(key, '')) > _activity_ts(cached_blk.get(key, '')): return True
	return False

def _simkl_activities_recent():
	try:
		at = float(kodi_utils.get_property(_SIMKL_ACTIVITIES_AT_PROP) or 0)
		return at > 0 and (time.time() - at) < _SIMKL_ACTIVITIES_MIN_GAP
	except Exception:
		return False

def _simkl_note_activities_poll():
	try: kodi_utils.set_property(_SIMKL_ACTIVITIES_AT_PROP, '%.3f' % time.time())
	except Exception: pass

def _simkl_has_saved_sync():
	try: return bool(_simkl_date_from(simkl_cache.simkl_cache.get('simkl_get_activity') or {}))
	except Exception: return False

def simkl_sync_activities(params=None, force_update=False, force_delta=False, progress=None):
	if isinstance(params, dict):
		force_update = params.get('force_update', 'false') in ('true', 'True', True) or force_update
		force_delta = params.get('force_delta', 'false') in ('true', 'True', True) or force_delta
	if not settings.simkl_user_active(): return 'no account'
	# Widget/list opens: one /sync/activities per minute (AUTH V2 free = 500/day).
	if not force_update and not force_delta and _simkl_activities_recent() and _simkl_has_saved_sync():
		return 'not needed'
	# Coalesce overlapping syncs (monitor + list refresh + mark-watched) across invokers.
	if force_update or force_delta:
		_sync_lock.acquire(True)
	else:
		try:
			if kodi_utils.get_property(_SIMKL_SYNC_BUSY_PROP) == 'true':
				started = float(kodi_utils.get_property(_SIMKL_SYNC_BUSY_AT_PROP) or 0)
				if started and (time.time() - started) < 180:
					return 'not needed'
		except Exception:
			pass
		if not _sync_lock.acquire(False):
			return 'not needed'
	try:
		try:
			kodi_utils.set_property(_SIMKL_SYNC_BUSY_PROP, 'true')
			kodi_utils.set_property(_SIMKL_SYNC_BUSY_AT_PROP, '%.3f' % time.time())
		except Exception:
			pass
		return _simkl_sync_activities_body(force_update=force_update, force_delta=force_delta, progress=progress)
	finally:
		try:
			kodi_utils.set_property(_SIMKL_SYNC_BUSY_PROP, 'false')
			kodi_utils.clear_property(_SIMKL_SYNC_BUSY_AT_PROP)
		except Exception:
			pass
		_sync_lock.release()

def _simkl_sync_activities_body(force_update=False, force_delta=False, progress=None):
	if force_update:
		kodi_utils.sync_progress(progress, 'Checking Simkl', 6)
		simkl_cache.clear_all_simkl_cache_data(silent=True, refresh=False)
		clear_simkl_list_status_cache()
	try: latest = call_simkl('/sync/activities', method='get')
	except: return 'failed'
	if not latest: return 'failed'
	cached = simkl_cache.reset_activity(latest)
	_simkl_note_activities_poll()
	# Phase 2: date_from = previously saved activities.all (before this poll overwrote it).
	date_from = None if force_update else _simkl_date_from(cached)
	if not force_update and _activity_ts(latest.get('all', '')) <= _activity_ts(cached.get('all', '')):
		if not (force_delta and date_from): return 'not needed'
	movies, shows = latest.get('movies', {}), latest.get('tv_shows', {})
	anime = latest.get('anime', {})
	cached_movies, cached_shows = cached.get('movies', {}), cached.get('tv_shows', {})
	cached_anime = cached.get('anime', {})
	playback_keys = ('playback', 'all')
	tv_list_changed = force_update or _activity_block_changed(shows, cached_shows, _SIMKL_LIST_ACTIVITY_KEYS) \
		or _activity_block_changed(anime, cached_anime, _SIMKL_LIST_ACTIVITY_KEYS)
	removed = _activity_block_changed(movies, cached_movies, ('removed_from_list',)) \
		or _activity_block_changed(shows, cached_shows, ('removed_from_list',)) \
		or _activity_block_changed(anime, cached_anime, ('removed_from_list',))
	if force_update or not date_from:
		simkl_indicators_movies(progress=progress)
		simkl_indicators_tv(progress=progress)
		clear_simkl_dropped_cache()
		if tv_list_changed: clear_simkl_calendar_cache()
	else:
		data = simkl_indicators_phase2(date_from)
		_simkl_merge_lists_from_payload(data)
		clear_simkl_dropped_cache()
		if tv_list_changed: clear_simkl_calendar_cache()
		if removed or force_delta: _simkl_reconcile_ids_only()
	if force_update or _activity_block_changed(movies, cached_movies, playback_keys) or _activity_block_changed(shows, cached_shows, playback_keys):
		simkl_sync_playback(progress=progress)
	return 'success'

def simkl_force_sync(params=None):
	if not settings.simkl_user_active(): return kodi_utils.notification('Simkl account not authorised', 3000)
	progress = kodi_utils.progress_dialog('Simkl Sync')
	status = 'failed'
	try:
		status = simkl_sync_activities(force_update=True, progress=progress)
	except Exception as e:
		kodi_utils.logger('Simkl', 'Force sync failed: %s' % e)
	finally:
		kodi_utils.close_progress_dialog(progress)
	if status == 'failed': kodi_utils.notification('Simkl Sync Failed', 3000)
	else:
		kodi_utils.notification('Simkl Sync Complete', 3000)
		kodi_utils.kodi_refresh()
	return status

SIMKL_TRENDING_BASE = 'https://data.simkl.in/discover/trending'
SIMKL_CALENDAR_CDN_BASE = 'https://data.simkl.in/calendar/v2'
SIMKL_CALENDAR_CACHE_KEY = 'simkl_calendar_v3_joined'
SIMKL_PUBLIC_CALENDAR_CACHE_KEYS = {
	'tv': 'simkl_calendar_v3_public_tv',
	'anime': 'simkl_calendar_v3_public_anime',
	'all': 'simkl_calendar_v3_public_all',
}
_SIMKL_TRENDING_TRAKT_KEYS = {'movies': 'movie', 'tv': 'show', 'anime': 'show'}

def _simkl_trending_url(media_kind):
	return '%s/%s/today_100.json' % (SIMKL_TRENDING_BASE, media_kind)

def _simkl_cdn_query_url(url, client_id=None):
	cid = client_id or _client_id()
	sep = '&' if '?' in url else '?'
	return '%s%sclient_id=%s&app-name=%s&app-version=%s' % (url, sep, cid, SIMKL_APP_NAME, kodi_utils.addon_version())

def _simkl_public_get(base_url):
	"""GET a Simkl CDN/public URL, retrying the other shipped Client ID on 412."""
	last_resp, last_error = None, None
	for cid in _client_ids_to_try():
		_throttle()
		url = _simkl_cdn_query_url(base_url, client_id=cid)
		try:
			resp = requests.get(url, headers=_pin_headers(), timeout=META_API_TIMEOUT)
		except Exception as e:
			kodi_utils.logger('Simkl', str(e))
			return None
		last_resp = resp
		if resp.status_code == 200:
			if not _has_simkl_token(): _adopt_shipped_client_id(cid)
			return resp
		last_error = 'HTTP %s %s' % (resp.status_code, url)
		if not _client_id_rejected(resp):
			break
	if last_error: kodi_utils.logger('Simkl', last_error)
	return last_resp

def _simkl_trending_query_url(url):
	return _simkl_cdn_query_url(url)

def clear_simkl_calendar_cache():
	try: simkl_cache.simkl_cache.delete(SIMKL_CALENDAR_CACHE_KEY)
	except: pass
	for key in SIMKL_PUBLIC_CALENDAR_CACHE_KEYS.values():
		try: simkl_cache.simkl_cache.delete(key)
		except: pass

def _simkl_calendar_library_by_simkl():
	"""Watching + Plan to Watch (shows + anime), keyed by Simkl id."""
	shows_by_simkl = {}
	for status in ('watching', 'plantowatch'):
		for media_kind in ('shows', 'anime'):
			for row in _simkl_fetch_status(media_kind, status) or []:
				try:
					sid = str((row.get('media_ids') or {}).get('simkl') or '')
					if sid: shows_by_simkl[sid] = row
				except Exception:
					continue
	return shows_by_simkl

def _simkl_fetch_calendar_payload(feed):
	"""Return (calendar_rows, metadata_by_simkl_id) from a public CDN v2 feed."""
	resp = _simkl_public_get('%s/%s.json' % (SIMKL_CALENDAR_CDN_BASE, feed))
	if resp is None or resp.status_code != 200:
		if resp is not None:
			kodi_utils.logger('Simkl', 'Calendar CDN HTTP %s for %s' % (resp.status_code, feed))
		return [], {}
	try:
		payload = resp.json()
	except Exception as e:
		kodi_utils.logger('Simkl', 'Calendar CDN error %s: %s' % (feed, e))
		return [], {}
	if isinstance(payload, dict):
		calendar_rows = payload.get('calendar', [])
		metadata = payload.get('metadata') or {}
	else:
		calendar_rows = payload or []
		metadata = {}
	if not isinstance(calendar_rows, list): calendar_rows = []
	if not isinstance(metadata, dict): metadata = {}
	return calendar_rows, metadata

def _simkl_fetch_calendar_feed(feed):
	calendar_rows, _metadata = _simkl_fetch_calendar_payload(feed)
	return calendar_rows

def _simkl_calendar_row(entry, library_row):
	"""Normalize a CDN airing + library show into the shared calendar row shape."""
	ep = entry.get('episode', {}) or {}
	try:
		ep_no = int(ep.get('episode'))
	except Exception:
		return None
	try:
		season_no = int(ep.get('season') or 0)
	except Exception:
		season_no = 0
	mids = dict(library_row.get('media_ids') or {})
	try:
		tmdb = int(mids.get('tmdb'))
	except Exception:
		return None
	# Anime v2 rows often omit season — include as S01 when TMDb exists (better than FenLight drop).
	if season_no <= 0: season_no = 1
	title = library_row.get('title') or ''
	# Keep full ISO (…Z) so Calendars UTC (+/-) can shift Today/Tomorrow labels.
	aired = str(entry.get('date') or '').strip()
	if not aired: return None
	media_ids = {'tmdb': tmdb}
	for key in ('imdb', 'tvdb', 'simkl'):
		val = mids.get(key)
		if val not in _SIMKL_ID_EMPTY: media_ids[key] = val
	return {
		'sort_title': '%s s%s e%s' % (title, str(season_no).zfill(2), str(ep_no).zfill(2)),
		'media_ids': media_ids,
		'season': season_no,
		'episode': ep_no,
		'first_aired': aired
	}

def _simkl_calendar_row_public(entry, meta):
	"""Normalize a CDN airing + feed metadata (no personal library join)."""
	if not isinstance(meta, dict): return None
	ep = entry.get('episode', {}) or {}
	try:
		ep_no = int(ep.get('episode'))
	except Exception:
		return None
	try:
		season_no = int(ep.get('season') or 0)
	except Exception:
		season_no = 0
	ids = meta.get('ids') or {}
	try:
		tmdb = int(ids.get('tmdb'))
	except Exception:
		return None
	if season_no <= 0: season_no = 1
	title = meta.get('title') or meta.get('en_title') or ''
	aired = str(entry.get('date') or '').strip()
	if not aired: return None
	media_ids = {'tmdb': tmdb}
	for key in ('imdb', 'tvdb'):
		val = ids.get(key)
		if val not in _SIMKL_ID_EMPTY: media_ids[key] = val
	sid = ids.get('simkl_id') or ids.get('simkl') or entry.get('simkl_id')
	if sid not in _SIMKL_ID_EMPTY: media_ids['simkl'] = sid
	return {
		'sort_title': '%s s%s e%s' % (title, str(season_no).zfill(2), str(ep_no).zfill(2)),
		'media_ids': media_ids,
		'season': season_no,
		'episode': ep_no,
		'first_aired': aired
	}

def _simkl_build_calendar_joined():
	shows_by_simkl = _simkl_calendar_library_by_simkl()
	if not shows_by_simkl: return []
	data = []
	for feed in ('tv', 'anime'):
		for entry in _simkl_fetch_calendar_feed(feed):
			if not isinstance(entry, dict): continue
			try:
				row = shows_by_simkl.get(str(entry.get('simkl_id') or ''))
				if not row: continue
				normalized = _simkl_calendar_row(entry, row)
				if normalized: data.append(normalized)
			except Exception:
				continue
	return [i for n, i in enumerate(data) if i not in data[n + 1:]]

def _simkl_build_public_calendar(feeds):
	data = []
	for feed in feeds:
		calendar_rows, metadata = _simkl_fetch_calendar_payload(feed)
		for entry in calendar_rows:
			if not isinstance(entry, dict): continue
			try:
				sid = str(entry.get('simkl_id') or '')
				meta = metadata.get(sid) or {}
				normalized = _simkl_calendar_row_public(entry, meta)
				if normalized: data.append(normalized)
			except Exception:
				continue
	return [i for n, i in enumerate(data) if i not in data[n + 1:]]

def _filter_simkl_calendar_day_window(data):
	from modules.utils import calendar_service_local_date
	start_date, end_date = settings.calendar_day_window()
	filtered = []
	for item in data:
		aired, _ = calendar_service_local_date(item.get('first_aired', ''))
		if aired is None: continue
		if start_date <= aired <= end_date:
			filtered.append(item)
	return filtered

def simkl_get_my_calendar(dummy=None):
	"""Personal episode calendar: CDN calendar/v2 joined to Watching + Plan to Watch.

	Cached joined payload is unfiltered; Show Previous/Future Days is applied on read
	so shared Calendars settings match PunchPlay/MDBList without waiting for cache expiry.
	"""
	if not settings.simkl_user_active(): return []
	cached = simkl_cache.simkl_cache.get(SIMKL_CALENDAR_CACHE_KEY)
	if cached:
		data = cached
	else:
		data = _simkl_build_calendar_joined() or []
		if data: simkl_cache.simkl_cache.set(SIMKL_CALENDAR_CACHE_KEY, data)
		elif cached is not None:
			simkl_cache.simkl_cache.delete(SIMKL_CALENDAR_CACHE_KEY)
	filtered = _filter_simkl_calendar_day_window(data)
	try:
		start_date, end_date = settings.calendar_day_window()
		kodi_utils.logger('Red Light', 'Simkl calendar: %s cached/fetched, %s in day window (%s → %s)' % (
			len(data), len(filtered), start_date, end_date))
	except Exception:
		pass
	return filtered

def simkl_get_public_calendar(feeds='all'):
	"""Public episode calendar from Simkl CDN (no personal library join; no auth required).

	feeds: 'tv', 'anime', or 'all' (tv + anime). Uses calendar[] + metadata[] TMDb ids.
	"""
	if feeds not in ('tv', 'anime', 'all'): feeds = 'all'
	feed_list = ('tv', 'anime') if feeds == 'all' else (feeds,)
	cache_key = SIMKL_PUBLIC_CALENDAR_CACHE_KEYS[feeds]
	cached = simkl_cache.simkl_cache.get(cache_key)
	if cached:
		data = cached
	else:
		data = _simkl_build_public_calendar(feed_list) or []
		if data: simkl_cache.simkl_cache.set(cache_key, data)
		elif cached is not None:
			simkl_cache.simkl_cache.delete(cache_key)
	filtered = _filter_simkl_calendar_day_window(data)
	capped = filtered
	try:
		max_items = settings.public_calendar_max_items()
		if max_items and len(filtered) > max_items:
			from modules.utils import calendar_service_local_date, get_datetime
			today = get_datetime()
			def _distance(item):
				aired, _ = calendar_service_local_date(item.get('first_aired', ''))
				if aired is None: return 99999
				return abs((aired - today).days)
			# Keep episodes closest to today so Previous/Future Days still feel useful under a cap.
			capped = sorted(filtered, key=_distance)[:max_items]
	except Exception:
		capped = filtered
	try:
		start_date, end_date = settings.calendar_day_window()
		kodi_utils.logger('Red Light', 'Simkl public calendar (%s): %s cached/fetched, %s in day window → %s capped (%s → %s)' % (
			feeds, len(data), len(filtered), len(capped), start_date, end_date))
	except Exception:
		pass
	return capped

def _simkl_trending_ids(item):
	ids = item.get('ids') or {}
	result = {}
	for key in ('tmdb', 'imdb', 'tvdb', 'slug'):
		value = ids.get(key)
		if value in (None, ''): continue
		if key == 'tmdb':
			try: result[key] = int(value)
			except: result[key] = value
		else: result[key] = value
	return result

def _simkl_trending_to_trakt(item, media_kind):
	ids = _simkl_trending_ids(item)
	if not ids.get('tmdb'): return None
	return {_SIMKL_TRENDING_TRAKT_KEYS[media_kind]: {'ids': ids}}

def _simkl_fetch_trending_today(media_kind):
	from caches.lists_cache import lists_cache_object
	def _fetch(dummy):
		try:
			resp = _simkl_public_get(_simkl_trending_url(media_kind))
			if resp is None or resp.status_code != 200:
				if resp is not None:
					kodi_utils.logger('Simkl Trending', 'HTTP %s for %s' % (resp.status_code, media_kind))
				return None
			data = resp.json()
			if isinstance(data, list): items = data
			else: items = data.get(media_kind) or data.get('items') or []
			results = []
			for item in items:
				converted = _simkl_trending_to_trakt(item, media_kind)
				if converted: results.append(converted)
			if not results:
				kodi_utils.logger('Simkl Trending', 'No usable items for %s' % media_kind)
				return None
			return results
		except Exception as e:
			kodi_utils.logger('Simkl Trending Error', str(e))
			return None
	result = lists_cache_object(_fetch, 'simkl_trending_%s_today_100' % media_kind, 'dummy_arg', expiration=1)
	return result or []

def clear_simkl_trending_cache(media_kind=None):
	try:
		from caches.lists_cache import lists_cache
		dbcon = lists_cache.manual_connect('lists_db')
		if media_kind: dbcon.execute('DELETE FROM lists WHERE id = ?', ('simkl_trending_%s_today_100' % media_kind,))
		else: dbcon.execute('DELETE FROM lists WHERE id LIKE ?', ('simkl_trending_%',))
		dbcon.execute('VACUUM')
	except: pass

def simkl_trending_today_count(media_kind):
	return len(_simkl_fetch_trending_today(media_kind))

def simkl_trending_today_page(media_kind, page_no, page_size=20):
	full_list = _simkl_fetch_trending_today(media_kind)
	start = (int(page_no) - 1) * page_size
	return full_list[start:start + page_size]
