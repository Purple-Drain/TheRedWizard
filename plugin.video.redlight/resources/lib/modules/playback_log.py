# -*- coding: utf-8 -*-
"""Opt-in record of what actually played, written to its own file.

Deliberately not kodi.log. That file is rotated to kodi.old.log on every Kodi start, so
playback history does not survive a reboot -- which is exactly the case this is for -- and
it is the file users attach to bug reports, while resolved debrid links routinely carry an
API token or account-scoped id in the path or query.

Volume is not a concern either way: one short line per playback, so even a few hundred plays
a day is tens of KB and a few hundred small appends. The file is size-rotated regardless so
it cannot grow without bound.

Module-level imports are stdlib-only and kodi_utils/settings are imported lazily inside the
functions that need them, so redact_link() can be unit-tested without the Kodi runtime (#70).
"""
import os
import time
from threading import Lock

_LOG_NAME = 'playback_log.tsv'
_MAX_BYTES = 1048576
_COLUMNS = ('when', 'media_type', 'title', 'season', 'episode', 'tmdb_id',
			'release', 'provider', 'quality', 'size', 'link')
_lock = Lock()

def redact_link(url):
	"""Reduce a resolved link to scheme://host/.../filename.

	Keeps what is useful for diagnosis -- which provider served it, and which file -- while
	dropping what is secret. Debrid links carry credentials in the middle path segments or the
	query string (e.g. .../d/<TOKEN>/Release.mkv), so every middle segment and the entire query
	are discarded. The last segment survives only when it looks like a filename rather than an
	opaque id, since a token can sit in that position too.
	"""
	if not url: return ''
	text = str(url)
	if '://' not in text:
		# Local path or a plugin:// route: keep the last segment, drop the directory tree.
		return text.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
	scheme, rest = text.split('://', 1)
	rest = rest.split('?', 1)[0].split('#', 1)[0]
	parts = [i for i in rest.split('/') if i != '']
	if not parts: return '%s://' % scheme
	host = parts[0]
	tail = parts[-1] if len(parts) > 1 else ''
	if tail and '.' in tail and len(tail) <= 160:
		return '%s://%s/.../%s' % (scheme, host, tail)
	return '%s://%s/...' % (scheme, host)

def _clean(value):
	"""One field, safe for a tab-separated line."""
	if value is None: return ''
	return str(value).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()

def _log_path():
	from modules import kodi_utils
	folder = kodi_utils.translate_path('special://profile/addon_data/plugin.video.redlight/')
	if not os.path.exists(folder): os.makedirs(folder)
	return os.path.join(folder, _LOG_NAME)

def _rotate(path):
	"""Keep one previous file. Cheap: a stat per write, a rename per megabyte."""
	try:
		if os.path.getsize(path) < _MAX_BYTES: return
		backup = '%s.1' % path
		if os.path.exists(backup): os.remove(backup)
		os.rename(path, backup)
	except OSError:
		pass

def _write(line):
	path = _log_path()
	with _lock:
		if os.path.exists(path): _rotate(path)
		write_header = not os.path.exists(path)
		# Opened and closed per event on purpose -- addon processes are reloaded and killed
		# freely, and a long-lived handle would lose buffered lines.
		with open(path, 'a', encoding='utf-8') as handle:
			if write_header: handle.write('%s\n' % '\t'.join(_COLUMNS))
			handle.write('%s\n' % line)

def log_playback(player):
	"""Append one row for a confirmed playback. Silent no-op unless the setting is enabled.

	Never raises: logging must not be able to break playback.
	"""
	try:
		from modules import settings
		if not settings.playback_log_enabled(): return
		item = getattr(player, 'playing_item', None) or {}
		link = getattr(player, 'url', '') or ''
		if not settings.playback_log_include_links(): link = redact_link(link)
		row = (
			time.strftime('%Y-%m-%dT%H:%M:%S'),
			getattr(player, 'media_type', ''),
			getattr(player, 'title', ''),
			getattr(player, 'season', ''),
			getattr(player, 'episode', ''),
			getattr(player, 'tmdb_id', ''),
			# playing_filename, never the subs.player_filename property: that runs through
			# _best_play_filename()'s subtitle-matching heuristic and can name a different file
			# than the one actually playing (see player.py's own note, and #56).
			getattr(player, 'playing_filename', ''),
			item.get('scrape_provider', ''),
			item.get('quality', ''),
			item.get('size', ''),
			link)
		_write('\t'.join(_clean(i) for i in row))
	except Exception:
		pass
