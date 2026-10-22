# -*- coding: utf-8 -*-
import requests
from caches.main_cache import main_cache

THEINTRODB_URL = 'https://api.theintrodb.org/v3/media'
INTRODB_URL = 'https://api.introdb.app/segments'
_CACHE_HOURS = 168
_API_TIMEOUT = 6
_MIN_SEGMENT_SEC = 10
_MIN_END_SEC = 15
_MAX_SEGMENT_SEC = 600
# #270: an empty answer is rechecked daily while the episode is new (community timings arrive within
# days of air); only an older episode keeps the long life. A failed request is never cached.
_EMPTY_CACHE_HOURS = 24
_OLD_EPISODE_DAYS = 30
# #282: a credits marker whose credits end leaves more than this much file after it belongs to a
# shorter cut of the episode; a short tail (preview, logo, black) stays under it.
_CUT_MISMATCH_GAP_SEC = 120
_status = {}


class _LookupFailed(Exception):
	pass


def _failure(exc):
	if isinstance(exc, requests.exceptions.Timeout): return 'timeout'
	if isinstance(exc, _LookupFailed): return str(exc)
	return 'error %s' % type(exc).__name__


def _get_json(url, params):
	'''One provider request. 404 is a clean "no data"; any other failure raises _LookupFailed.'''
	try:
		response = requests.get(url, params=params, timeout=_API_TIMEOUT)
	except Exception as exc:
		raise _LookupFailed(_failure(exc))
	if response.status_code == 404:
		return None
	if response.status_code != 200:
		raise _LookupFailed('HTTP %s' % response.status_code)
	try:
		return response.json()
	except Exception:
		raise _LookupFailed('bad JSON')


def _empty_cache_hours(premiered=None):
	'''24 h for a new or unknown-age episode, the long life once it is older than _OLD_EPISODE_DAYS.'''
	try:
		import datetime
		aired = datetime.datetime.strptime(str(premiered)[:10], '%Y-%m-%d').date()
		if (datetime.date.today() - aired).days > _OLD_EPISODE_DAYS:
			return _CACHE_HOURS
	except Exception:
		pass
	return _EMPTY_CACHE_HOURS


def lookup_status(tmdb_id, imdb_id, season, episode):
	'''Why the last intro lookup for this episode found nothing, for the "no timing" log line.'''
	try: key = _intro_cache_key(tmdb_id, imdb_id, int(season), int(episode))
	except Exception: return 'unknown'
	return _status.get(key, 'cached miss')
_MAX_INTRO_START_FRAC = 0.5
_CREDITS_INTRO_OVERLAP_MS = 5000


def _intro_cache_key(tmdb_id, imdb_id, season, episode):
	return 'intro_skip_%s_%s_%s_%s' % (tmdb_id, imdb_id or '', season, episode)


def _outro_cache_key(tmdb_id, imdb_id, season, episode):
	return 'outro_credits_%s_%s_%s_%s' % (tmdb_id, imdb_id or '', season, episode)


def peek_intro_segment_cache(tmdb_id, imdb_id, season, episode, duration_sec=None):
	try:
		season, episode = int(season), int(episode)
	except:
		return '__miss__'
	cached = main_cache.get(_intro_cache_key(tmdb_id, imdb_id, season, episode))
	if cached is None:
		return '__miss__'
	if not cached:
		return None
	segment = _valid_segment(cached.get('start_sec'), cached.get('end_sec'), duration_sec)
	if segment:
		segment['source'] = cached.get('source', 'introdb')
		return segment
	return '__miss__'


def prefetch_intro_segment(tmdb_id, imdb_id, season, episode, duration_sec=None):
	try:
		resolve_intro_segment(tmdb_id, imdb_id, season, episode, duration_sec)
	except:
		pass


def prefetch_credits_start(tmdb_id, imdb_id, season, episode, duration_sec=None):
	try:
		resolve_credits_start_sec(tmdb_id, imdb_id, season, episode, duration_sec)
	except:
		pass


def resolve_intro_segment(tmdb_id, imdb_id, season, episode, duration_sec=None, premiered=None):
	try:
		season, episode = int(season), int(episode)
	except:
		return None
	cache_key = _intro_cache_key(tmdb_id, imdb_id, season, episode)
	cached = main_cache.get(cache_key)
	if cached is not None:
		if not cached:
			return None
		segment = _valid_segment(cached.get('start_sec'), cached.get('end_sec'), duration_sec)
		if segment:
			segment['source'] = cached.get('source', 'introdb')
			return segment
		main_cache.set(cache_key, '', expiration=_CACHE_HOURS)
	segment, failures = None, []
	if tmdb_id not in (None, '', 'None', '0000000'):
		segment = _attempt(_fetch_theintrodb_intro, failures, tmdb_id, season, episode, duration_sec)
	if not segment and imdb_id not in (None, '', 'None', 'tt0000000'):
		segment = _attempt(_fetch_introdb_intro, failures, imdb_id, season, episode)
	if segment:
		_status[cache_key] = 'found'
		main_cache.set(cache_key, segment, expiration=_CACHE_HOURS)
	elif failures:
		# #270: a failed request is not "no intro"; leave it uncached so the next play asks again.
		_status[cache_key] = 'api error (%s)' % ', '.join(failures)
	else:
		_status[cache_key] = 'api miss'
		main_cache.set(cache_key, '', expiration=_empty_cache_hours(premiered))
	return segment


def resolve_credits_segment(tmdb_id, imdb_id, season, episode, duration_sec=None, premiered=None):
	'''The credits marker as stored: {'start_sec', 'end_sec'} (end_sec None when the provider has none),
	or None. The cache is per episode, not per file, so the fit against the playing file is done by the
	caller through credits_start_for_duration() (#282).'''
	try:
		season, episode = int(season), int(episode)
	except:
		return None
	cache_key = _outro_cache_key(tmdb_id, imdb_id, season, episode)
	cached = main_cache.get(cache_key)
	if cached is not None:
		if not cached:
			return None
		if isinstance(cached, dict):
			return cached
		return {'start_sec': cached, 'end_sec': None}  # value cached before #282
	segment, failures = None, []
	if tmdb_id not in (None, '', 'None', '0000000'):
		segment = _attempt(_fetch_theintrodb_credits, failures, tmdb_id, season, episode, duration_sec)
	if segment is None and imdb_id not in (None, '', 'None', 'tt0000000'):
		segment = _attempt(_fetch_introdb_outro, failures, imdb_id, season, episode, duration_sec)
	if segment is not None:
		main_cache.set(cache_key, segment, expiration=_CACHE_HOURS)
	elif not failures:
		main_cache.set(cache_key, '', expiration=_empty_cache_hours(premiered))
	return segment


def credits_start_for_duration(segment, duration_sec, gap_sec=None):
	'''Fit a credits marker to the file playing. Returns (start_sec, shifted_from).

	#282: introdb.app holds one marker per episode and takes no duration, so a longer cut
	(a Director's Cut, 1562 s against a 1274 s broadcast cut) gets the broadcast marker and the
	next-episode prompt fires minutes early. Credits sit at the end of any cut, so when the
	marker's credits end leaves more than gap_sec of file after it, the marker is re-anchored to
	the end of this file, keeping its credits length. A marker with no end (TheIntroDB credits
	carry end_ms null, and it is already asked with the duration) is returned as is.'''
	if not segment:
		return None, None
	start_sec = segment.get('start_sec')
	if start_sec is None:
		return None, None
	end_sec = segment.get('end_sec')
	if gap_sec is None:
		gap_sec = _CUT_MISMATCH_GAP_SEC
	try:
		start_sec = float(start_sec)
		total = float(duration_sec) if duration_sec else None
		end_sec = float(end_sec) if end_sec is not None else None
	except:
		return start_sec, None
	if total is None or total <= 60 or end_sec is None or end_sec < start_sec:
		return start_sec, None
	if total - end_sec <= gap_sec:
		return start_sec, None
	return total - (end_sec - start_sec), start_sec


def resolve_credits_start_sec(tmdb_id, imdb_id, season, episode, duration_sec=None, premiered=None):
	segment = resolve_credits_segment(tmdb_id, imdb_id, season, episode, duration_sec, premiered)
	start_sec, _ = credits_start_for_duration(segment, duration_sec)
	return start_sec


def _attempt(fetch, failures, *args):
	try:
		return fetch(*args)
	except _LookupFailed as exc:
		failures.append(str(exc))
		return None


def _valid_start_sec(start_sec, duration_sec=None):
	try:
		start_sec = float(start_sec)
	except:
		return None
	if start_sec < 0:
		return None
	if duration_sec:
		try:
			total = float(duration_sec)
			if total > 60 and start_sec > total:
				return None
		except:
			pass
	return start_sec


def _valid_segment(start_sec, end_sec, duration_sec=None):
	try:
		start_sec, end_sec = float(start_sec), float(end_sec)
	except:
		return None
	if end_sec <= start_sec:
		return None
	length = end_sec - start_sec
	if length < _MIN_SEGMENT_SEC or length > _MAX_SEGMENT_SEC:
		return None
	if end_sec < _MIN_END_SEC:
		return None
	if duration_sec:
		try:
			total = float(duration_sec)
			if total > 60:
				if end_sec > total:
					return None
				if start_sec > total * _MAX_INTRO_START_FRAC:
					return None
		except:
			pass
	return {'start_sec': start_sec, 'end_sec': end_sec}


def _ms_segment(start_ms, end_ms, duration_sec=None):
	if start_ms is None:
		start_ms = 0
	try:
		start_ms, end_ms = int(start_ms), int(end_ms)
	except:
		return None
	if end_ms <= start_ms:
		return None
	return _valid_segment(start_ms / 1000.0, end_ms / 1000.0, duration_sec)


def _credits_start_ms_list(credits_list):
	starts = []
	for entry in credits_list or []:
		ms = entry.get('start_ms') if isinstance(entry, dict) else None
		if ms is None:
			continue
		try:
			starts.append(int(ms))
		except:
			pass
	return starts


def _intro_is_credits_duplicate(start_ms, credits_starts):
	if not credits_starts:
		return False
	try:
		start_ms = 0 if start_ms is None else int(start_ms)
	except:
		return False
	for credit_ms in credits_starts:
		if abs(start_ms - credit_ms) <= _CREDITS_INTRO_OVERLAP_MS:
			return True
		if start_ms >= credit_ms:
			return True
	return False


def _parse_start_value(start_val, end_val=None):
	if start_val is None:
		return None
	try:
		start_sec = float(start_val)
		if start_sec > 10000:
			start_sec = start_sec / 1000.0
		return start_sec
	except:
		return None


def _fetch_theintrodb_intro(tmdb_id, season, episode, duration_sec=None):
	try:
		params = {'tmdb_id': str(tmdb_id), 'season': season, 'episode': episode}
		if duration_sec:
			try:
				params['durationMs'] = int(float(duration_sec) * 1000)
			except:
				pass
		data = _get_json(THEINTRODB_URL, params)
		if not data:
			return None
		intro_list = data.get('intro') or []
		if not intro_list:
			return None
		credits_starts = _credits_start_ms_list(data.get('credits') or [])
		for entry in intro_list:
			if not isinstance(entry, dict):
				continue
			start_ms = entry.get('start_ms')
			if _intro_is_credits_duplicate(start_ms, credits_starts):
				continue
			segment = _ms_segment(start_ms, entry.get('end_ms'), duration_sec)
			if segment:
				segment['source'] = 'theintrodb'
				return segment
		return None
	except _LookupFailed:
		raise
	except:
		return None


def _fetch_introdb_intro(imdb_id, season, episode):
	try:
		params = {'imdb_id': str(imdb_id), 'season': season, 'episode': episode, 'segment_type': 'intro'}
		data = _get_json(INTRODB_URL, params)
		if not data:
			return None
		intro = data.get('intro')
		if not intro or not isinstance(intro, dict):
			return None
		start_sec = _parse_start_value(intro.get('start_sec', intro.get('start_ms')))
		end_sec = _parse_start_value(intro.get('end_sec', intro.get('end_ms')))
		if start_sec is None:
			start_sec = 0.0
		segment = _valid_segment(start_sec, end_sec)
		if segment:
			segment['source'] = 'introdb'
		return segment
	except _LookupFailed:
		raise
	except:
		return None


def _fetch_introdb_outro(imdb_id, season, episode, duration_sec=None):
	try:
		params = {'imdb_id': str(imdb_id), 'season': season, 'episode': episode}
		data = _get_json(INTRODB_URL, params)
		if not data:
			return None
		outro = data.get('outro') or {}
		start_sec = _parse_start_value(outro.get('start_sec', outro.get('start_ms')))
		start_sec = _valid_start_sec(start_sec, duration_sec)
		if start_sec is None:
			return None
		end_sec = _parse_start_value(outro.get('end_sec', outro.get('end_ms')))
		if end_sec is not None and end_sec < start_sec:
			end_sec = None
		return {'start_sec': start_sec, 'end_sec': end_sec}
	except _LookupFailed:
		raise
	except:
		return None


def _fetch_theintrodb_credits(tmdb_id, season, episode, duration_sec=None):
	try:
		params = {'tmdb_id': str(tmdb_id), 'season': season, 'episode': episode}
		if duration_sec:
			try:
				params['durationMs'] = int(float(duration_sec) * 1000)
			except:
				pass
		data = _get_json(THEINTRODB_URL, params)
		if not data:
			return None
		credits_list = data.get('credits') or []
		if not credits_list:
			return None
		entry = credits_list[0]
		if entry.get('start_ms') is None:
			return None
		start_sec = _valid_start_sec(int(entry['start_ms']) / 1000.0, duration_sec)
		if start_sec is None:
			return None
		end_sec = None
		if entry.get('end_ms') is not None:
			try: end_sec = int(entry['end_ms']) / 1000.0
			except: end_sec = None
		return {'start_sec': start_sec, 'end_sec': end_sec}
	except _LookupFailed:
		raise
	except:
		return None
