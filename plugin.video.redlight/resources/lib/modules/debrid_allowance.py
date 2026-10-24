# -*- coding: utf-8 -*-
"""Debrid daily allowance cooldown (#293).

When TorBox or Real-Debrid says the account's daily allowance is spent, retrying cannot help
until the provider resets. The error is recorded here as a cooldown in a window property, so
every resolve in this Kodi session skips that provider at once and the failure reason says
when to try again, instead of each queue entry retrying for its full deadline.

TorBox answers requestdl with HTTP 429 and DAILY_BANDWIDTH_LIMIT_EXCEEDED (media-stack#134).
Its reset is assumed to be 00:00 UTC, the same assumption zurg-pd#6 makes. RD's code for a
spent allowance has not been seen live, so RD is matched on its error text, never on code 34
(too_many_requests, a short rate limit that does clear on retry).
"""
import time
from datetime import datetime, timedelta, timezone

PROP_PREFIX = 'redlight.allowance_spent.'
MAX_COOLDOWN_S = 3600

_TORBOX_MARKERS = ('daily_bandwidth_limit_exceeded', 'bandwidth_limit', 'daily limit')
_RD_MARKERS = ('traffic_exhausted', 'fair_usage_limit', 'traffic exhausted', 'fair usage', 'bandwidth')
_RD_RATE_LIMIT = 34


def _text(value):
	if value is None: return ''
	if isinstance(value, dict):
		return ' '.join(str(value.get(k) or '') for k in ('error', 'detail', 'message')).lower()
	return str(value).lower()


def is_allowance_error(provider, response):
	"""True when the body (dict or text) says the provider's daily allowance is spent."""
	text = _text(response)
	if not text: return False
	if provider == 'TorBox':
		return any(m in text for m in _TORBOX_MARKERS)
	if provider == 'Real-Debrid':
		if isinstance(response, dict) and str(response.get('error_code')) == str(_RD_RATE_LIMIT): return False
		return any(m in text for m in _RD_MARKERS)
	return False


def next_reset(now=None):
	"""Epoch seconds of the next 00:00 UTC after `now`."""
	current = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc)
	midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
	return midnight.timestamp()


def cooldown_until(now=None):
	"""The reset, capped at an hour so a wrong reset guess costs at most an hour (zurg-pd#6)."""
	now = now if now is not None else time.time()
	return min(next_reset(now), now + MAX_COOLDOWN_S)


def _props():
	from modules.kodi_utils import get_property, set_property, clear_property
	return get_property, set_property, clear_property


def mark_spent(provider, now=None, props=None):
	"""Start the cooldown and return its end (epoch seconds)."""
	get_property, set_property, _clear = props or _props()
	until = cooldown_until(now)
	set_property(PROP_PREFIX + provider, str(int(until)))
	try:
		from modules.kodi_utils import logger
		logger('Red Light', '%s daily allowance spent, skipping it until %s' % (provider, clock(until)))
	except Exception: pass
	return until


def spent_until(provider, now=None, props=None):
	"""End of the provider's cooldown, or None when it is not cooling (expired ones are cleared)."""
	get_property, _set, clear_property = props or _props()
	try: until = float(get_property(PROP_PREFIX + provider) or 0)
	except (TypeError, ValueError): until = 0
	if not until: return None
	if (now if now is not None else time.time()) >= until:
		clear_property(PROP_PREFIX + provider)
		return None
	return until


def clock(epoch):
	"""Local HH:MM, which is what the viewer reads in the dialog."""
	return time.strftime('%H:%M', time.localtime(epoch))


def spent_error(provider, until):
	"""The error dict a client keeps in last_unrestrict_error, read by describe_debrid_error."""
	return {'error': 'daily allowance spent, retry after %s' % clock(until)}
