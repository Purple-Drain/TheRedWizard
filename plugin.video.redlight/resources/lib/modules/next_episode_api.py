# -*- coding: utf-8 -*-
"""External-caller entry points for #92: "what/play the next episode of <show>".

Both routes below reuse the exact function behind the Next Episodes widget
(router.py's mode=build_next_episode -> indexers.episodes.build_single_episode('episode.next', ...))
instead of recomputing "next" independently, so a voice command and the widget can never
disagree about what episode is next -- same watched state, same assigned-episode-group
traversal (#69/#82), same in-progress resume.

build_single_episode() only produces xbmcgui.ListItem rows for xbmcplugin.addDirectoryItems();
it has no "give me one show's answer" mode. _pick_next_episode() below runs it with
kodi_utils.add_items (and the directory-finishing calls) temporarily swapped for no-op/capturing
stand-ins, then picks the one captured row for the requested tmdb_id out of the full list -- the
approach #92 asks for when the reused function only produces list items.

Two consumption paths for the same computation, both documented on the PR:
  - mode=next_episode.info&tmdb_id=<id>   -- JSON via a window property AND the directory
    listing (single item, label = JSON) so both a window-property reader and a plain
    JSON-RPC Files.GetDirectory caller can read the answer without playing anything.
  - mode=playback.next_episode&tmdb_id=<id> -- same computation, handed to the existing
    playback.media route in-process (Sources().playback_prep(), the same call player_check()
    makes for playback.media) rather than issuing a second plugin invocation.

Both accept &query=<show name> instead of &tmdb_id, resolved through the existing TMDb search
(apis.tmdb_api.tmdb_tv_search) -- first result wins. Neither touches player.py or the
play_file/resolve region of sources.py: playback still goes through Sources().playback_prep()
exactly as playback.media does today.
"""
import json
from urllib.parse import parse_qsl

WINDOW_PROPERTY = 'redlight.next_episode_info'


def resolve_tmdb_id(params, search_fn=None):
	"""tmdb_id if given; else the first tmdb_tv_search() result for &query=<show name>.

	search_fn is injectable so the query->id fallback is unit-testable without a live TMDb
	call: pass a stand-in returning the same shape as apis.tmdb_api.tmdb_tv_search(query, page).
	"""
	tmdb_id = params.get('tmdb_id')
	if tmdb_id not in (None, ''):
		try:
			return int(tmdb_id)
		except (TypeError, ValueError):
			return None
	query = params.get('query')
	if not query:
		return None
	if search_fn is None:
		from apis.tmdb_api import tmdb_tv_search

		def search_fn(q):
			return tmdb_tv_search(q, 1)
	try:
		data = search_fn(query) or {}
		results = data.get('results') or []
		if not results:
			return None
		return int(results[0]['id'])
	except Exception:
		return None


def next_episode_payload(season, episode, title, file_url):
	"""The JSON shape #92 asks for. Pure -- no Kodi calls -- so it is unit-tested directly."""
	return {'season': season, 'episode': episode, 'title': title, 'file': file_url}


def _capture_next_episode_rows():
	"""Run the widget's own build_single_episode('episode.next', ...) with the directory-write
	calls swapped for stand-ins, and return the (url, listitem, isFolder) rows it would have
	added -- one per show with a computed next episode. Restores the real functions afterward
	regardless of outcome.
	"""
	from indexers import episodes as episodes_mod
	from modules import kodi_utils

	captured = []
	originals = {name: getattr(kodi_utils, name) for name in
				('add_items', 'set_content', 'set_category', 'end_directory', 'set_view_mode', 'set_sort_method')}
	kodi_utils.add_items = lambda handle, item_list: captured.extend(item_list)
	for name in ('set_content', 'set_category', 'end_directory', 'set_view_mode', 'set_sort_method'):
		setattr(kodi_utils, name, lambda *a, **k: None)
	try:
		episodes_mod.build_single_episode('episode.next', {})
	finally:
		for name, fn in originals.items():
			setattr(kodi_utils, name, fn)
	return captured


def _find_next_episode_row(tmdb_id):
	"""One captured widget row for tmdb_id, as (play_params, listitem) -- or None.

	play_params is the exact plugin:// URL the widget's own row would launch, built by
	build_url() with mode set to playback.<playback_key> (playback.media today) -- reused
	verbatim rather than reconstructed, so this can never drift from what a click plays.
	"""
	for play_params, listitem, _is_folder in _capture_next_episode_rows():
		row_params = dict(parse_qsl(play_params.split('?', 1)[1]))
		try:
			if int(row_params.get('tmdb_id', -1)) == int(tmdb_id):
				return play_params, row_params, listitem
		except (TypeError, ValueError):
			continue
	return None


def _row_title(listitem):
	try:
		return listitem.getVideoInfoTag().getTitle() or listitem.getLabel()
	except Exception:
		try:
			return listitem.getLabel()
		except Exception:
			return ''


def next_episode_info(params):
	"""mode=next_episode.info&tmdb_id=<id>[&query=<show name>].

	Writes the JSON to window property WINDOW_PROPERTY *and* adds it as a single directory
	item (label = the JSON string), so either a window-property reader or a plain JSON-RPC
	Files.GetDirectory caller gets the answer -- documented on the PR.
	"""
	from modules import kodi_utils
	import sys

	handle = int(sys.argv[1])
	tmdb_id = resolve_tmdb_id(params)
	payload, found = {}, False
	if tmdb_id is not None:
		row = _find_next_episode_row(tmdb_id)
		if row:
			play_params, row_params, listitem = row
			payload = next_episode_payload(int(row_params.get('season')), int(row_params.get('episode')),
											_row_title(listitem), play_params)
			found = True
	body = json.dumps(payload)
	kodi_utils.set_property(WINDOW_PROPERTY, body)
	listitem = kodi_utils.make_listitem()
	listitem.setLabel(body)
	kodi_utils.add_item(handle, '', listitem, False)
	kodi_utils.set_content(handle, 'files')
	kodi_utils.end_directory(handle, cacheToDisc=False)
	return found


def playback_next_episode(params):
	"""mode=playback.next_episode&tmdb_id=<id>[&query=<show name>].

	Computes the next episode the same way next_episode_info() does, then hands the widget's
	own play_params straight to Sources().playback_prep() in-process -- the same call
	player_check() makes for mode=playback.media -- instead of a second plugin invocation.
	"""
	from modules import kodi_utils

	tmdb_id = resolve_tmdb_id(params)
	if tmdb_id is None:
		kodi_utils.ok_dialog('Red Light', 'Next episode: no tmdb_id and no matching &query show')
		return False
	row = _find_next_episode_row(tmdb_id)
	if not row:
		kodi_utils.ok_dialog('Red Light', 'Next episode: nothing next for this show')
		return False
	play_params, row_params, _listitem = row
	from modules.sources import Sources
	Sources().playback_prep(row_params)
	return True
