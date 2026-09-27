# -*- coding: utf-8 -*-
"""Next-episode warm-up (#1): read the first few MB of the file the next play will open, before it opens.

For a folders source (zurg's WebDAV share) there is nothing to resolve, so a resolve warms nothing; the
read makes zurg unrestrict the file and pull its first bytes to the CDN edge while nobody is waiting. For
a pre-resolved debrid url it is the same read against a link already paid for, and it doubles as a
liveness check. The read goes through Kodi's own VFS (xbmcvfs), the same path playback uses, so a dav://
path and a url with |headers both work as they will at play time.

Two callers:
- end of episode: Sources._stash_nextep_autoplay_play, in the background prep thread.
- Next Episodes widget: episodes.py asks (request_widget_warm) after a real build, and the service's
  NextEpWidgetWarm thread scrapes the first WIDGET_N items with the folders scrapers only and warms any
  hit. No debrid API call is made from the widget path (owner C341): no cache check, nothing added.

Every candidate logs one summary line, 'NextEpWarm: origin=... top=zurg|cloud|none ...', so how often the
top source is a zurg folder can be counted across days (the data behind the owner's later C342 choice).
"""
import json
import time
from modules import kodi_utils, settings

WARM_BYTES = 2 * 1024 * 1024
WARM_CHUNK = 256 * 1024
WARM_DEADLINE_SEC = 20
WIDGET_N = 3
# A zurg file read within this window is still warm; a widget rebuild inside it does not read it again.
WIDGET_REWARM_SEC = 1800
WIDGET_REQUEST_PROP = 'redlight.nextep_widget_warm_request'


def warm_read(path, max_bytes=WARM_BYTES, deadline_s=WARM_DEADLINE_SEC):
	"""Read up to max_bytes from path. Returns (bytes_read, ms, error). Never raises."""
	started, got, err = time.time(), 0, ''
	handle = None
	try:
		import xbmcvfs
		handle = xbmcvfs.File(path)
		while got < max_bytes and (time.time() - started) < deadline_s:
			chunk = handle.readBytes(min(WARM_CHUNK, max_bytes - got))
			if not chunk: break
			got += len(chunk)
	except Exception as exc:
		err = str(exc)[:80] or exc.__class__.__name__
	finally:
		try:
			if handle is not None: handle.close()
		except Exception: pass
	return got, int((time.time() - started) * 1000), err


def top_kind(results):
	'''zurg when the top playable result is a folders hit, cloud when it is anything else, none when empty.'''
	playable = [i for i in (results or []) if 'Uncached' not in i.get('cache_provider', '')]
	if not playable: return 'none', None
	top = playable[0]
	return ('zurg' if top.get('scrape_provider') == 'folders' else 'cloud'), top


def log_summary(origin, meta, kind, warm, detail=''):
	'''One line per candidate. warm: (bytes, ms, error) or None when no read was made.'''
	try:
		se = 'S%02dE%02d' % (int(meta.get('season') or 0), int(meta.get('episode') or 0))
	except Exception:
		se = 'S??E??'
	if warm is None: outcome = 'warm=skipped'
	else:
		got, ms, err = warm
		outcome = 'warm=%s bytes=%s ms=%s' % ('ok' if got and not err else 'fail', got, ms)
		if err: outcome += ' err=%s' % err
	kodi_utils.logger('Red Light', 'NextEpWarm: origin=%s top=%s %s %s tmdb=%s %s%s' % (
		origin, kind, outcome, meta.get('title', ''), meta.get('tmdb_id', ''), se, (' (%s)' % detail) if detail else ''))


def warm_end_of_episode(results, meta, preresolved=None):
	'''End-of-episode warm-up, called from the background prep after pre-resolve. zurg: read the folders
	path. cloud: read the pre-resolved url when there is one (no new API call); otherwise log only.'''
	kind, top = top_kind(results)
	if not settings.nextep_warm_read():
		return log_summary('episode_end', meta, kind, None, 'setting off')
	if kind == 'zurg':
		return log_summary('episode_end', meta, kind, warm_read(top.get('url_dl')))
	if kind == 'cloud' and preresolved and preresolved.get('url'):
		return log_summary('episode_end', meta, kind, warm_read(preresolved['url']), 'pre-resolved url')
	log_summary('episode_end', meta, kind, None, 'no pre-resolved url' if kind == 'cloud' else 'no playable result')


def request_widget_warm(urls):
	'''Called by the widget build (episodes.py) after a real build. Only records the first WIDGET_N play
	urls in a window property; the service does the work, so the widget render makes no network call.'''
	try:
		if not settings.nextep_widget_warm(): return
		kodi_utils.set_property(WIDGET_REQUEST_PROP, json.dumps(list(urls)[:WIDGET_N]))
	except Exception as exc:
		kodi_utils.logger('Red Light', 'NextEpWarm: widget request failed: %s' % exc)


def take_widget_request():
	raw = kodi_utils.get_property(WIDGET_REQUEST_PROP)
	if not raw: return []
	kodi_utils.clear_property(WIDGET_REQUEST_PROP)
	try: return [u for u in json.loads(raw) if isinstance(u, str)]
	except Exception: return []


def _episode_params(url):
	from urllib.parse import urlparse, parse_qsl
	params = dict(parse_qsl(urlparse(url).query))
	try: return params['tmdb_id'], int(params['season']), int(params['episode'])
	except Exception: return None


def _folders_results(tmdb_id, season, episode):
	'''Folders scrapers only, the same search info a real play builds. Returns (meta, results).'''
	from modules.sources import Sources
	from scrapers.folders import source as folders_source
	s = Sources()
	s.media_type, s.tmdb_id, s.season, s.episode = 'episode', tmdb_id, season, episode
	s.background, s.playcount, s.watch_count = True, 0, 0
	s.custom_season = s.custom_episode = s.custom_title = s.custom_year = None
	s.get_meta()
	s.meta.setdefault('tmdb_id', tmdb_id)
	s.make_search_info()
	results = []
	for name, slot, path in s.get_folderscraper_info():
		try: results.extend(folders_source(slot, name, path).results(dict(s.search_info)) or [])
		except Exception as exc: kodi_utils.logger('Red Light', 'NextEpWarm: folders %s failed: %s' % (slot, exc))
	results.sort(key=lambda i: (i.get('folder_rank', 0), -float(i.get('size') or 0)))
	return s.meta, results


def warm_widget_items(urls, warmed, monitor=None):
	'''Service side. warmed: {episode key: time read}, kept by the caller across requests.'''
	now = time.time()
	for url in urls:
		if monitor is not None and monitor.abortRequested(): return
		ids = _episode_params(url)
		if not ids: continue
		key = '%s_%s_%s' % ids
		if now - warmed.get(key, 0) < WIDGET_REWARM_SEC: continue
		try:
			meta, results = _folders_results(*ids)
		except Exception as exc:
			kodi_utils.logger('Red Light', 'NextEpWarm: widget scrape failed for %s: %s' % (key, exc))
			continue
		meta = dict(meta or {}, tmdb_id=ids[0], season=ids[1], episode=ids[2])
		if not results:
			# Folders only: no hit here means the play would need a cloud source (C342's count).
			log_summary('widget', meta, 'cloud', None, 'no folders hit')
			continue
		log_summary('widget', meta, 'zurg', warm_read(results[0].get('url_dl')))
		warmed[key] = time.time()
