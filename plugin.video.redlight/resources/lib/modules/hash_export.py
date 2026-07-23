# -*- coding: utf-8 -*-
# Builds a DMM (debridmediamanager.com)-style hash list [{hash, filename}, ...]
# scoped to a show / movie / the football bucket, using each provider's
# lightweight torrent-listing endpoint rather than strm_library.py's WebDAV
# walk. Deliberately a SEPARATE account read, not folded into the WebDAV walk:
# RD/TorBox/Debrid-Link's native listings hand back a hash for free (no
# per-file unrestrict/requestdl call needed, unlike resolving a playable
# link), so this stays cheap even though it duplicates the account walk.
#
# Grouping reuses strm_library's classify functions (parse_episode,
# parse_movie, looks_like_football) so a hash list is scoped the same way the
# strm library is -- "every hash currently backing my copy of <Show>". This
# does NOT do update_wc.py's team-pair+stage fixture grouping for football
# (that needs its TEAM_ALIASES/SCHEDULE payload, deliberately not vendored
# here for the same reason update_library.py doesn't import it) -- football
# is exported as one undivided bucket for now.
import json
import os

from modules.kodi_utils import notification, kodi_dialog, ok_dialog, addon_profile
from modules.utils import copy2clip
from modules import strm_library as lib


def _rd_hashes():
	from apis.real_debrid_api import RealDebrid
	items = RealDebrid.user_cloud()
	if not isinstance(items, list):
		return []
	out = []
	for t in items:
		h = t.get('hash')
		name = t.get('filename') or t.get('original_filename') or ''
		if h and name:
			out.append({'hash': h.lower(), 'filename': name, 'provider': 'Real-Debrid'})
	return out


def _tb_hashes():
	from apis.torbox_api import TorBox
	_err, items = TorBox.mylist_items('torrent', fresh=False)
	out = []
	for t in items or []:
		h = t.get('hash')
		if not h:
			continue
		files = t.get('files') or [{'name': t.get('name')}]
		for f in files:
			name = f.get('name') or f.get('short_name') or t.get('name') or ''
			if name:
				out.append({'hash': h.lower(), 'filename': name, 'provider': 'TorBox'})
	return out


def _dl_hashes():
	from apis.debridlink_api import DebridLink
	items = DebridLink.user_cloud()
	out = []
	for t in items or []:
		h = t.get('hashString') or t.get('hash')
		if not h:
			continue
		files = t.get('files') or [{'name': t.get('name')}]
		for f in files:
			name = f.get('name') or t.get('name') or ''
			if name:
				out.append({'hash': h.lower(), 'filename': name, 'provider': 'Debrid-Link'})
	return out


def _all_hashes():
	out = []
	for fn in (_rd_hashes, _tb_hashes, _dl_hashes):
		try:
			out.extend(fn())
		except Exception:
			pass
	return out


def _scope_key(item):
	"""(kind, key) for grouping, or None to drop this file entirely.
	kind is 'show' / 'movie' / 'football'."""
	stem = item['filename'].rpartition('.')[0] or item['filename']
	if lib.is_junk(stem):
		return None
	episode = lib.parse_episode(stem)
	if episode:
		show, _season, _ep = episode
		return ('show', show.title())
	if lib.looks_like_football(item['filename']):
		return ('football', None)
	film = lib.parse_movie(stem, item['filename'])
	if film:
		title, year = film
		return ('movie', '%s (%d)' % (title.title(), year))
	return None


def build_hashlist(scope_kind=None, scope_value=None):
	"""scope_kind in (None, 'show', 'movie', 'football'); scope_value narrows
	to titles containing it (case-insensitive). Returns a deduped list of
	{'hash', 'filename', 'provider'}."""
	results = []
	for item in _all_hashes():
		grouping = _scope_key(item)
		if not grouping:
			continue
		kind, value = grouping
		if scope_kind and kind != scope_kind:
			continue
		if scope_value and (not value or scope_value.lower() not in value.lower()):
			continue
		results.append(item)
	seen, deduped = set(), []
	for r in results:
		key = (r['hash'], r['filename'])
		if key in seen:
			continue
		seen.add(key)
		deduped.append(r)
	return deduped


def _write_and_share(items, label):
	if not items:
		return ok_dialog(heading='Hash List', text='No matching hashes found for "%s".' % label)
	payload = [{'hash': i['hash'], 'filename': i['filename']} for i in items]
	directory = os.path.join(addon_profile(), 'hashlists')
	os.makedirs(directory, exist_ok=True)
	safe_label = ''.join(c for c in label if c not in '<>:"/\\|?*').strip() or 'hashlist'
	path = os.path.join(directory, '%s.json' % safe_label)
	with open(path, 'w', encoding='utf-8') as fh:
		json.dump(payload, fh, indent=2)
	copy2clip(json.dumps([p['hash'] for p in payload]))
	notification('Hash List: %d hashes -> %s (hashes copied to clipboard)' % (len(payload), path), 6000)


# ---------------------------------------------------------------------------
# Router entry points -- mode=hash_export.<name>, wired in modules/router.py
# ---------------------------------------------------------------------------

def export_show(params=None):
	params = params or {}
	show = params.get('show')
	if show is None:
		show = kodi_dialog().input('Show name (blank = ALL TV shows)')
	if show is None:
		return
	items = build_hashlist('show', show.strip() or None)
	_write_and_share(items, show.strip() or 'All TV Shows')


def export_movie(params=None):
	params = params or {}
	title = params.get('title')
	if title is None:
		title = kodi_dialog().input('Movie title (blank = ALL movies)')
	if title is None:
		return
	items = build_hashlist('movie', title.strip() or None)
	_write_and_share(items, title.strip() or 'All Movies')


def export_football(params=None):
	items = build_hashlist('football', None)
	_write_and_share(items, 'Football')


def export_all(params=None):
	items = build_hashlist(None, None)
	_write_and_share(items, 'Full Library')
