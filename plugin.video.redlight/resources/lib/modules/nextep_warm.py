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

Every candidate logs one summary line, 'NextEpWarm: origin=... top=...', also kept in the addon profile's
nextep_warm.log, so how often the top source is a zurg folder can be counted across days (the data behind the
owner's later C342 choice). top=zurg|cloud|none at episode end (the real top result); top=zurg|nozurg on the
widget, where only the folders scrapers run, so nozurg means the play would need a cloud source. The widget's
pick is the first folders hit, without the play's quality and size filters.
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
LOG_FILE, LOG_KEEP = 'nextep_warm.log', 2000


NETWORK_SCHEMES = ('dav://', 'davs://', 'smb://', 'nfs://', 'http://', 'https://', 'ftp://', 'ftps://', 'sftp://', 'upnp://')


def is_network_path(path):
	'''A warm read only helps a file reached over the network (a WebDAV folder such as zurg, a share, a
	debrid url). A local disk path has nothing to warm, so it is skipped (owner, 28.09.26).'''
	return isinstance(path, str) and path.lower().startswith(NETWORK_SCHEMES)


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
	line = 'NextEpWarm: origin=%s top=%s %s %s tmdb=%s %s%s' % (
		origin, kind, outcome, meta.get('title', ''), meta.get('tmdb_id', ''), se, (' (%s)' % detail) if detail else '')
	kodi_utils.logger('Red Light', line)
	_append_count_log(line)


def _append_count_log(line):
	'''kodi.log rotates at every Kodi start, so the lines are also kept in a small file of their own
	(addon profile, last LOG_KEEP lines) and one read covers days.'''
	try:
		import os
		path = os.path.join(kodi_utils.addon_profile(), LOG_FILE)
		lines = []
		if os.path.isfile(path):
			with open(path, 'r', encoding='utf-8') as handle: lines = handle.read().splitlines()[-(LOG_KEEP - 1):]
		lines.append('%s %s' % (time.strftime('%Y-%m-%dT%H:%M:%S'), line))
		with open(path, 'w', encoding='utf-8') as handle: handle.write('\n'.join(lines) + '\n')
	except Exception: pass


def warm_end_of_episode(results, meta, preresolved=None):
	'''End-of-episode warm-up, called from the background prep after pre-resolve. zurg: read the folders
	path. cloud: read the pre-resolved url when there is one (no new API call); otherwise log only.'''
	kind, top = top_kind(results)
	if not settings.nextep_warm_read():
		return log_summary('episode_end', meta, kind, None, 'setting off')
	if kind == 'zurg':
		if not is_network_path(top.get('url_dl')): return log_summary('episode_end', meta, kind, None, 'local folder, nothing to warm')
		return log_summary('episode_end', meta, kind, warm_read(top.get('url_dl')))
	if kind == 'cloud' and preresolved and preresolved.get('url'):
		warm = warm_read(preresolved['url'])
		dead = not warm[0]
		log_summary('episode_end', meta, kind, warm, 'pre-resolved url, dropped' if dead else 'pre-resolved url')
		return 'preresolved_dead' if dead else None
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


def warm_widget_items(urls, warmed, monitor=None, idle=None):
	'''Service side. warmed: {episode key: time read}, kept by the caller across requests. idle(): False
	once a play or scrape has started; checked before every item and every read.'''
	now = time.time()
	still_idle = lambda: (monitor is None or not monitor.abortRequested()) and (idle is None or idle())
	for url in urls:
		if not still_idle(): return
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
			log_summary('widget', meta, 'nozurg', None, 'no folders hit')
			continue
		if not still_idle(): return
		if not is_network_path(results[0].get('url_dl')):
			log_summary('widget', meta, 'zurg', None, 'local folder, nothing to warm')
			warmed[key] = time.time()
			continue
		log_summary('widget', meta, 'zurg', warm_read(results[0].get('url_dl')))
		warmed[key] = time.time()
