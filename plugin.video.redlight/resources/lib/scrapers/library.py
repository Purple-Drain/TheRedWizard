# -*- coding: utf-8 -*-
"""Kodi library rung (#312): a film or episode already in the shared Kodi library plays from the LAN
before any debrid folder or cloud copy of it. Looked up by tmdb id over JSON-RPC; the library's own
SMB path is mapped to the T5 WebDAV share, because a seek over SMB ends playback on the Shield
(kodi-shield-config docs/playback-paths.md). A file whose path can't be mapped off SMB is skipped."""
import json
from urllib.parse import quote, unquote
from caches.settings_cache import get_setting
from modules import source_utils
from modules.utils import clean_file_name, normalize
# from modules.kodi_utils import logger

DEFAULT_PATH_MAP = 'smb://10.1.1.22/media/=dav://10.1.1.22:9998/dav/'
LIBRARY_RANK = 0 # folder slots are 1-5, so the library ties ahead of all of them

def _jsonrpc(method, params):
	import xbmc
	response = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}))
	return json.loads(response).get('result') or {}

def path_map():
	"""'from=to' pairs, ';' separated, from redlight.library.path_map."""
	value = get_setting('redlight.library.path_map', DEFAULT_PATH_MAP) or DEFAULT_PATH_MAP
	pairs = []
	for item in value.split(';'):
		if '=' not in item: continue
		src, dst = item.split('=', 1)
		if src.strip() and dst.strip(): pairs.append((src.strip(), dst.strip()))
	return pairs

def playable_path(file_path, pairs):
	"""The library path as it should be played, or None when it would still be SMB (or is a stack).
	The library keeps names raw; over dav curl refuses a space or bracket, so the mapped rest is
	percent-encoded the way a folders listing returns it."""
	if not file_path or file_path.startswith('stack://'): return None
	for src, dst in pairs:
		if file_path.lower().startswith(src.lower()): file_path = dst + quote(unquote(file_path[len(src):]), safe='/'); break
	if file_path.lower().startswith('smb://'): return None
	return file_path

def _tmdb_matches(item, tmdb_id):
	return str((item.get('uniqueid') or {}).get('tmdb', '')) == str(tmdb_id)

def library_files(media_type, tmdb_id, year=None, season=None, episode=None):
	"""File paths the Kodi library holds for this tmdb id (movie, or show plus season/episode)."""
	if not tmdb_id: return []
	if media_type == 'movie':
		params = {'properties': ['uniqueid', 'file']}
		if year: params['filter'] = {'field': 'year', 'operator': 'is', 'value': str(year)}
		movies = _jsonrpc('VideoLibrary.GetMovies', params).get('movies') or []
		return [i['file'] for i in movies if _tmdb_matches(i, tmdb_id) and i.get('file')]
	shows = _jsonrpc('VideoLibrary.GetTVShows', {'properties': ['uniqueid']}).get('tvshows') or []
	files = []
	for show in [i for i in shows if _tmdb_matches(i, tmdb_id)]:
		params = {'tvshowid': show['tvshowid'], 'season': int(season), 'properties': ['episode', 'file']}
		episodes = _jsonrpc('VideoLibrary.GetEpisodes', params).get('episodes') or []
		files.extend(i['file'] for i in episodes if int(i.get('episode', -1)) == int(episode) and i.get('file'))
	return files

def _size_gb(file_path):
	try: return int(_jsonrpc('Files.GetFileDetails', {'file': file_path, 'properties': ['size']}).get('filedetails', {}).get('size', 0)) / 1073741824.0
	except: return 0.0

class source:
	def __init__(self, scrape_provider, scraper_name, _folder_path=None):
		self.scrape_provider = scrape_provider
		self.scraper_name = scraper_name
		self.sources = []

	def results(self, info):
		try:
			media_type = info.get('media_type')
			files = library_files(media_type, info.get('tmdb_id'), info.get('year'), info.get('season'), info.get('episode'))
			pairs = path_map()
			for library_path in files:
				file_dl = playable_path(library_path, pairs)
				if not file_dl: continue
				file_name = normalize(library_path.rstrip('/').split('/')[-1])
				size = _size_gb(library_path)
				video_quality, details = source_utils.get_file_info(name_info=source_utils.release_info_format(file_name))
				self.sources.append({'name': file_name, 'display_name': clean_file_name(file_name), 'quality': video_quality, 'size': size,
							'size_label': '%.2f GB' % size, 'debrid': 'folders', 'extraInfo': details, 'url_dl': file_dl, 'id': file_dl,
							self.scrape_provider: True, 'direct': True, 'source': self.scraper_name, 'scrape_provider': 'folders',
							'folder_rank': LIBRARY_RANK, 'library': True})
		except Exception as e:
			from modules.kodi_utils import logger
			logger('RedLight library scraper Exception', str(e))
		source_utils.internal_results(self.scraper_name, self.sources)
		return self.sources
