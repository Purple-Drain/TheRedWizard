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
			'release', 'provider', 'quality', 'size', 'link', 'event', 'outcome', 'position', 'total')
_HEADER = '\t'.join(_COLUMNS)
# A file written before the event/outcome columns existed is moved here once, so no file ever
# mixes two headers (readers key columns by header name).
_LEGACY_NAME = 'playback_log.pre-outcome.tsv'
_lock = Lock()

def strip_userinfo(url):
	"""Drop a user:password@ prefix from the host of scheme://user:pass@host/path.

	Folder sources on an SMB share (the zurg mount) carry the share's login in the URL, and
	that is never needed to tell where a play came from, so even the full-link mode drops it
	(#72). Only the authority is touched: an @ further along the path is left alone.
	"""
	if not url: return ''
	text = str(url)
	if '://' not in text: return text
	scheme, rest = text.split('://', 1)
	cut = len(rest)
	for sep in '/?#':
		pos = rest.find(sep)
		if pos != -1 and pos < cut: cut = pos
	authority, tail = rest[:cut], rest[cut:]
	if '@' in authority: authority = authority.rsplit('@', 1)[1]
	return '%s://%s%s' % (scheme, authority, tail)

def redact_link(url):
	"""Reduce a resolved link to scheme://host/.../filename.

	Keeps what is useful for diagnosis -- which provider served it, and which file -- while
	dropping what is secret. Debrid links carry credentials in the middle path segments or the
	query string (e.g. .../d/<TOKEN>/Release.mkv), so every middle segment and the entire query
	are discarded. The last segment survives only when it looks like a filename rather than an
	opaque id, since a token can sit in that position too.
	"""
	if not url: return ''
	text = strip_userinfo(url)
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

def _retire_old_header(path):
	"""Move aside a file whose header is not the current one. The first such file goes to
	_LEGACY_NAME and is kept; after that an old-header file just takes the .1 slot."""
	try:
		with open(path, 'r', encoding='utf-8') as handle: header = handle.readline().rstrip('\r\n')
	except OSError:
		return
	if header == _HEADER: return
	legacy = os.path.join(os.path.dirname(path), _LEGACY_NAME)
	target = legacy if not os.path.exists(legacy) else '%s.1' % path
	try:
		if os.path.exists(target) and target != legacy: os.remove(target)
		os.rename(path, target)
	except OSError:
		pass

def _write(line):
	path = _log_path()
	with _lock:
		if os.path.exists(path): _retire_old_header(path)
		if os.path.exists(path): _rotate(path)
		write_header = not os.path.exists(path)
		# Opened and closed per event on purpose -- addon processes are reloaded and killed
		# freely, and a long-lived handle would lose buffered lines.
		with open(path, 'a', encoding='utf-8') as handle:
			if write_header: handle.write('%s\n' % _HEADER)
			handle.write('%s\n' % line)

def _seconds(value):
	try: return '%d' % float(value)
	except (TypeError, ValueError): return ''

def log_playback(player):
	"""Append the start row for a confirmed playback. Silent no-op unless the setting is enabled.

	Never raises: logging must not be able to break playback.
	"""
	_log_event(player, 'start', '', '', '')

def log_playback_end(player):
	"""Append the end row: how the play closed (player.end_outcome, set by _note_abnormal_end:
	ended, stopped, next_episode, superseded, stall, seek_end, no_callback, monitor_error) and
	where, so stalls can be counted per provider (#141). Same identity columns as the start row."""
	_log_event(player, 'end', getattr(player, 'end_outcome', None) or 'unknown',
			_seconds(getattr(player, 'curr_time', None)), _seconds(getattr(player, 'total_time', None)))

def _log_event(player, event, outcome, position, total):
	try:
		from modules import settings
		if not settings.playback_log_enabled(): return
		item = getattr(player, 'playing_item', None) or {}
		link = getattr(player, 'url', '') or ''
		link = strip_userinfo(link) if settings.playback_log_include_links() else redact_link(link)
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
			link, event, outcome, position, total)
		_write('\t'.join(_clean(i) for i in row))
	except Exception:
		pass
