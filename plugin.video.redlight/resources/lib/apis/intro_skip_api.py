# -*- coding: utf-8 -*-
import requests
from caches.main_cache import main_cache

THEINTRODB_URL = 'https://api.theintrodb.org/v4/media'
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
# #283: TheIntroDB v4 matches the closest cut within 5 minutes of duration_ms. Cached markers are keyed
# by the file's length to the minute, so two cuts of one episode do not share an entry.
_CUT_MATCH_SEC = 300
_DURATION_BUCKET_SEC = 60
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


def _duration_bucket(duration_sec):
	try:
		duration_sec = float(duration_sec)
		if duration_sec > 0: return int(round(duration_sec / _DURATION_BUCKET_SEC))
	except: pass
	return None


def _intro_cache_key(tmdb_id, imdb_id, season, episode, duration_sec=None):
	key = 'intro_skip_%s_%s_%s_%s' % (tmdb_id, imdb_id or '', season, episode)
	bucket = _duration_bucket(duration_sec)
	return key if bucket is None else '%s_d%s' % (key, bucket)


def _outro_cache_key(tmdb_id, imdb_id, season, episode, duration_sec=None):
	key = 'outro_credits_%s_%s_%s_%s' % (tmdb_id, imdb_id or '', season, episode)
	bucket = _duration_bucket(duration_sec)
	return key if bucket is None else '%s_d%s' % (key, bucket)


def _cached_for_duration(key_fn, tmdb_id, imdb_id, season, episode, duration_sec):
	'''#283: the entry for this file's length, else the length-free entry (a prefetch made before the
	file was known) when its cut is within TheIntroDB's match window of the file. None means ask again.'''
	cached = main_cache.get(key_fn(tmdb_id, imdb_id, season, episode, duration_sec))
	if cached is not None or _duration_bucket(duration_sec) is None:
		return cached
	cached = main_cache.get(key_fn(tmdb_id, imdb_id, season, episode))
	if isinstance(cached, dict):
		cut_sec = cached.get('cut_duration_sec')
		if cut_sec is not None and abs(float(cut_sec) - float(duration_sec)) > _CUT_MATCH_SEC:
			return None
	return cached


def peek_intro_segment_cache(tmdb_id, imdb_id, season, episode, duration_sec=None):
	try:
		season, episode = int(season), int(episode)
	except:
		return '__miss__'
	cached = _cached_for_duration(_intro_cache_key, tmdb_id, imdb_id, season, episode, duration_sec)
	if cached is None:
		return '__miss__'
	if not cached:
		return None
	segment = _valid_segment(cached.get('start_sec'), cached.get('end_sec'), duration_sec)
	if segment:
		segment['source'] = cached.get('source', 'introdb')
		return segment
	return None


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
	cache_key = _intro_cache_key(tmdb_id, imdb_id, season, episode, duration_sec)
	status_key = _intro_cache_key(tmdb_id, imdb_id, season, episode)
	cached = _cached_for_duration(_intro_cache_key, tmdb_id, imdb_id, season, episode, duration_sec)
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
		_status[status_key] = 'found'
		main_cache.set(cache_key, segment, expiration=_CACHE_HOURS)
	elif failures:
		# #270: a failed request is not "no intro"; leave it uncached so the next play asks again.
		_status[status_key] = 'api error (%s)' % ', '.join(failures)
	else:
		_status[status_key] = 'api miss'
		main_cache.set(cache_key, '', expiration=_empty_cache_hours(premiered))
	return segment


def resolve_credits_segment(tmdb_id, imdb_id, season, episode, duration_sec=None, premiered=None):
	'''The credits marker as stored: {'start_sec', 'end_sec'} (end_sec None when the provider has none),
	or None. TheIntroDB is asked for the cut nearest the file's length (#283); introdb.app has no cuts, so
	the caller still fits the marker to the playing file through credits_start_for_duration() (#282).'''
	try:
		season, episode = int(season), int(episode)
	except:
		return None
	cache_key = _outro_cache_key(tmdb_id, imdb_id, season, episode, duration_sec)
	cached = _cached_for_duration(_outro_cache_key, tmdb_id, imdb_id, season, episode, duration_sec)
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
			if total > 60 and end_sec > total:
				return None
		except:
			pass
	return {'start_sec': start_sec, 'end_sec': end_sec}


def _ms_segment(start_ms, end_ms, duration_sec=None):
	try:
		start_ms, end_ms = int(start_ms), int(end_ms)
	except:
		return None
	if end_ms <= start_ms:
		return None
	return _valid_segment(start_ms / 1000.0, end_ms / 1000.0, duration_sec)


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
		params = {'tmdb_id': str(tmdb_id), 'season': season, 'episode': episode, 'details': 'true'}
		if duration_sec:
			try:
				params['duration_ms'] = int(float(duration_sec) * 1000)
			except:
				pass
		data = _get_json(THEINTRODB_URL, params)
		if not data:
			return None
		intro_list = data.get('intro') or []
		if not intro_list:
			return None
		entry = intro_list[0]
		segment = _ms_segment(entry.get('start_ms'), entry.get('end_ms'), duration_sec)
		if segment:
			segment['source'] = 'theintrodb'
			segment['cut_duration_sec'] = _cut_duration_sec(data)
		return segment
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
		params = {'tmdb_id': str(tmdb_id), 'season': season, 'episode': episode, 'details': 'true'}
		if duration_sec:
			try:
				params['duration_ms'] = int(float(duration_sec) * 1000)
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
		return {'start_sec': start_sec, 'end_sec': end_sec, 'cut_duration_sec': _cut_duration_sec(data)}
	except _LookupFailed:
		raise
	except:
		return None


def _cut_duration_sec(data):
	'''Length of the cut TheIntroDB matched (details=true), or None.'''
	try: return int(data['group_duration_ms']) / 1000.0
	except: return None
