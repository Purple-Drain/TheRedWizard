# -*- coding: utf-8 -*-
import json
from datetime import datetime
from caches.main_cache import main_cache
from caches.settings_cache import get_setting, set_setting
from modules.utils import datetime_workaround, jsondate_to_datetime
from modules import kodi_utils

CACHE_HOURS = 12
_CACHE_PREFIX = 'service_expiry_'
_ALERT_STATE_SETTING = 'services.expiry_alert_state'

SERVICE_META = (
	('ad', 'All Debrid', 'alldebrid'),
	('easynews', 'EasyNews', 'easynews'),
	('oc', 'Offcloud', 'offcloud'),
	('pm', 'Premiumize', 'premiumize'),
	('rd', 'Real Debrid', 'realdebrid'),
	('tb', 'TorBox', 'torbox'),
	('dl', 'Debrid-Link', 'debridlink'),
)


def _cache_key(service_id):
	return '%s%s' % (_CACHE_PREFIX, service_id)


def _load_alert_state():
	try:
		raw = get_setting('redlight.%s' % _ALERT_STATE_SETTING, '{}')
		state = json.loads(raw or '{}')
		return state if isinstance(state, dict) else {}
	except:
		return {}


def _save_alert_state(state):
	set_setting(_ALERT_STATE_SETTING, json.dumps(state or {}))


def expiry_alert_days():
	try: return max(0, int(get_setting('redlight.services.expiry_alert_days', '7')))
	except: return 7


def parse_expiry(raw):
	if raw in (None, '', 'empty_setting'): return None
	if isinstance(raw, datetime): return raw
	if isinstance(raw, (int, float)):
		try: return datetime.fromtimestamp(raw)
		except: return None
	raw = str(raw).strip()
	if not raw: return None
	for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
		try: return datetime_workaround(raw, fmt)
		except: pass
	try: return jsondate_to_datetime(raw, '%Y-%m-%d')
	except: return None


def menu_suffix(days):
	if days is None: return ''
	if days < 0: return ' · expired'
	if days == 0: return ' · today'
	if days == 1: return ' · 1 day'
	return ' · %d days' % days


def summary_from_expiry(expires_dt):
	if not expires_dt: return None
	days = (expires_dt - datetime.today()).days
	date_str = expires_dt.strftime('%d %b %Y')
	return {
		'days': days,
		'date_str': date_str,
		'expires_line': '[B]Expires:[/B] %s' % date_str,
		'days_line': '[B]Days Remaining:[/B] %s' % days,
		'menu_suffix': menu_suffix(days),
	}


def append_expiry_lines(body, summary):
	if not summary: return
	append = body.append
	if summary.get('expires_line'): append(summary['expires_line'])
	if summary.get('days_line'): append(summary['days_line'])


def _service_authorized(service_id):
	from modules import settings as s
	if service_id == 'easynews': return s.easynews_authorized()
	return s.authorized_debrid_check(service_id)


def _fetch_raw_expiry(service_id):
	try:
		if service_id == 'rd':
			from apis.real_debrid_api import RealDebrid
			return (RealDebrid.account_info() or {}).get('expiration')
		if service_id == 'pm':
			from apis.premiumize_api import Premiumize
			return (Premiumize.account_info() or {}).get('premium_until')
		if service_id == 'ad':
			from apis.alldebrid_api import AllDebrid
			user = (AllDebrid.account_info() or {}).get('user') or {}
			return user.get('premiumUntil')
		if service_id == 'easynews':
			from apis.easynews_api import EasyNews
			account_info = EasyNews.account_info()
			if not account_info or len(account_info) < 3: return None
			return account_info[2]
		if service_id == 'tb':
			from apis.torbox_api import TorBox
			response = TorBox.account_info() or {}
			if not response.get('success'): return None
			return (response.get('data') or {}).get('premium_expires_at')
		if service_id == 'dl':
			from apis.debridlink_api import DebridLink
			response = DebridLink.account_info() or {}
			if not response.get('success'): return None
			premium_left = (response.get('value') or {}).get('premiumLeft')
			if not premium_left: return None
			import time
			return time.time() + int(premium_left)
		if service_id == 'oc':
			from apis.offcloud_api import Offcloud
			info = Offcloud.account_info() or {}
			return info.get('expiration_date') or info.get('expirationDate')
	except: pass
	return None


def fetch_expiry_summary(service_id):
	expires_dt = parse_expiry(_fetch_raw_expiry(service_id))
	return summary_from_expiry(expires_dt)


def _serialize_summary(summary):
	if not summary: return None
	return {
		'days': summary['days'],
		'date_str': summary['date_str'],
		'menu_suffix': summary['menu_suffix'],
	}


def _deserialize_summary(data):
	if not data: return None
	return {
		'days': data.get('days'),
		'date_str': data.get('date_str'),
		'expires_line': '[B]Expires:[/B] %s' % data.get('date_str', ''),
		'days_line': '[B]Days Remaining:[/B] %s' % data.get('days', ''),
		'menu_suffix': data.get('menu_suffix', ''),
	}


def get_cached_expiry_summary(service_id, refresh=False):
	if not _service_authorized(service_id): return None
	key = _cache_key(service_id)
	if not refresh:
		cached = main_cache.get(key)
		if cached is not None: return _deserialize_summary(cached)
	summary = fetch_expiry_summary(service_id)
	payload = _serialize_summary(summary)
	if payload is not None: main_cache.set(key, payload, expiration=CACHE_HOURS)
	return summary


def premium_menu_label(service_id, base_name):
	summary = get_cached_expiry_summary(service_id)
	if not summary or not summary.get('menu_suffix'): return base_name
	return '%s%s' % (base_name, summary['menu_suffix'])


def _should_alert(service_id, days):
	threshold = expiry_alert_days()
	if threshold <= 0: return False
	if days is None: return False
	last = _load_alert_state().get(service_id, '')
	if days < 0:
		return last != 'expired'
	if days > threshold: return False
	if not last: return True
	if last == 'expired': return True
	try: last_days = int(last)
	except: return True
	return days < last_days


def _mark_alerted(service_id, days):
	state = _load_alert_state()
	if days is not None and days < 0: state[service_id] = 'expired'
	else: state[service_id] = str(days)
	_save_alert_state(state)


def _alert_message(display_name, summary):
	days = summary.get('days')
	if days is None: return None
	if days < 0: return '%s subscription has expired' % display_name
	if days == 0: return '%s subscription expires today' % display_name
	if days == 1: return '%s subscription expires in 1 day' % display_name
	return '%s subscription expires in %d days' % (display_name, days)


def run_expiry_alerts():
	threshold = expiry_alert_days()
	if threshold <= 0: return
	for service_id, display_name, icon_name in SERVICE_META:
		if not _service_authorized(service_id): continue
		summary = get_cached_expiry_summary(service_id, refresh=True)
		if not summary: continue
		days = summary.get('days')
		if not _should_alert(service_id, days): continue
		message = _alert_message(display_name, summary)
		if not message: continue
		kodi_utils.notification(message, 8000, kodi_utils.get_icon(icon_name))
		_mark_alerted(service_id, days)
