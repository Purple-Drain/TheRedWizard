# -*- coding: utf-8 -*-
import os
from urllib.parse import urlparse
import time
from caches.main_cache import main_cache
from caches.settings_cache import get_setting
from modules import source_utils
from modules.kodi_utils import list_dirs, open_file
from modules.utils import clean_file_name, normalize, make_thread_list
from modules.settings import filter_by_name, max_threads
# from modules.kodi_utils import logger

class source:
	def __init__(self, scrape_provider, scraper_name, folder_path):
		self.scrape_provider = scrape_provider
		self.scraper_name = scraper_name
		# 'folder2' -> 2: the slot's number, the sort's last tie-break (#141).
		self.folder_rank = int(''.join(c for c in str(scrape_provider) if c.isdigit()) or 0)
		self.folder_path = folder_path
		self.sources, self.scrape_results = [], []
		self.extensions = source_utils.supported_video_extensions()

	def results(self, info):
		try:
			if not self.folder_path: return source_utils.internal_results(self.scraper_name, self.sources)
			self.scrape_deadline = time.time() + self._deadline_seconds()
			filter_title = filter_by_name('folders')
			self.media_type, title, self.year = info.get('media_type'), info.get('title'), int(info.get('year'))
			self.season, self.episode = info.get('season'), info.get('episode')
			self.tmdb_id = info.get('tmdb_id')
			self.title_check = source_utils.episode_title_check(info)
			self.title_query = source_utils.clean_title(normalize(title))
			self.folder_query = self._season_query_list() if self.media_type == 'episode' else self._year_query_list()
			self.title, self.filter_title = title, filter_title
			self.aliases = source_utils.get_aliases_titles(info.get('aliases', []))
			root = self._as_dir(self.folder_path)
			self._scrape_directory(root, first_run=True, below_title=self._names_title(root))
			if not self.scrape_results: return source_utils.internal_results(self.scraper_name, self.sources)
			aliases = self.aliases
			def _process():
				for item in self.scrape_results:
					try:
						file_name = normalize(item[0])
						if filter_title and not source_utils.check_title(title, file_name, aliases, self.year, self.season, self.episode): continue
						display_name = clean_file_name(file_name).replace('html', ' ').replace('+', ' ').replace('-', ' ')
						file_dl = item[1]
						try: size = item[2]
						except: size = self._get_size(file_dl)
						video_quality, details = source_utils.get_file_info(name_info=source_utils.release_info_format(file_name))
						source_item = {'name': file_name, 'display_name': display_name, 'quality': video_quality, 'size': size, 'size_label': '%.2f GB' % size, 'debrid': 'folders',
									'extraInfo': details, 'url_dl': file_dl, 'id': file_dl, self.scrape_provider : True, 'direct': True, 'source': self.scraper_name,
									'scrape_provider': 'folders', 'folder_rank': self.folder_rank}
						yield source_item
					except: pass
			self.sources = list(_process())
		except Exception as e:
			from modules.kodi_utils import logger
			logger('RedLight folders scraper Exception', str(e))
		source_utils.internal_results(self.scraper_name, self.sources)
		return self.sources

	def _episode_file_matches(self, normalized):
		"""Title in the file name decides when it can (#89); otherwise the S/E regex as before. An extra
		filed with the episodes (an "Inside Look", deleted scenes) is never the episode (#165)."""
		title_check = getattr(self, 'title_check', None)
		if source_utils.extra_file(normalized, getattr(title_check, 'target', ''), getattr(title_check, 'show', ''), getattr(self, 'title', '')): return False
		if title_check is not None:
			try: verdict = title_check(normalized, self.episode)
			except Exception: verdict = None
			if verdict is not None: return verdict
		return source_utils.seas_ep_filter(self.season, self.episode, normalized)

	def _make_dirs(self, folder_name):
		folder_files = []
		folder_files_append = folder_files.append
		dirs, files =  list_dirs(folder_name)
		for i in dirs: folder_files_append((i, 'folder'))
		for i in files: folder_files_append((i, 'file'))
		return folder_files

	def _cached_listing(self, folder_name):
		"""Listing cached for four hours, except an empty one (#150). xbmcvfs.listdir returns empty
		lists for a failed SMB/WebDAV listing as well as for an empty folder, and caching that hid a
		whole show from this scraper for four hours after one transient Broken pipe. An empty folder
		is cheap to list again. A cached empty entry left by an older version counts as a miss. The
		key is unchanged, so delete_all_folderscrapers still clears it."""
		string = 'FOLDERSCRAPER_%s_%s' % (self.scrape_provider, folder_name)
		cached = main_cache.get(string)
		if cached: return cached
		folder_files = self._make_dirs(folder_name)
		if folder_files: main_cache.set(string, folder_files, expiration=4)
		return folder_files

	def _scrape_directory(self, folder_name, first_run=False, below_title=False):
		"""below_title: an enclosing folder (or the configured path itself) carries the title, so a
		folder named only by the year or season ("1994 Remaster", "Season 03") belongs to it. At the
		top of a whole-library path it doesn't (#172): a Clerks (1994) search went into Friends
		1994-2004 and Muriel's Wedding (1994) and opened every video file there."""
		if not first_run and time.time() >= self.scrape_deadline:
			from modules.kodi_utils import logger
			logger('Red Light', 'folders scrape deadline reached before listing %s' % folder_name)
			return
		def _process(item):
			file_type = item[1]
			normalized = normalize(item[0])
			item_name = source_utils.clean_title(normalized)
			if file_type == 'file':
				ext = os.path.splitext(urlparse(item[0]).path)[-1].lower()
				if ext in self.extensions:
					if self.media_type == 'episode':
						if not self._episode_file_matches(normalized): return
					elif not self._film_file_matches(normalized): return
					url_path = self.url_path(folder_name, item[0])
					size = self._file_size(url_path)
					if size is None: return
					scrape_results_append((item[0], url_path, size))
			elif self.title_query in item_name or (below_title and any(x in item_name for x in self.folder_query)):
					folder_results_append((self._as_dir(os.path.join(folder_name, item[0])), True))
		folder_results = []
		scrape_results_append = self.scrape_results.append
		folder_results_append = folder_results.append
		folder_files = self._cached_listing(folder_name)
		folder_threads = list(make_thread_list(_process, folder_files))
		self._join_until_deadline(folder_threads, 'listing')
		if not folder_results: return
		return self._scraper_worker(folder_results)

	def _deadline_seconds(self):
		"""Same scrape budget the cloud scrapers give themselves (#112); this scraper never had
		one at all, worse than any of them, since a hung WebDAV/Zurg mount has no HTTP-level
		timeout to fall back on the way requests-based scrapers do."""
		return min(25, max(10, int(get_setting('redlight.results.timeout', '20'))))

	def _join_until_deadline(self, threads, label):
		"""Join against the scrape deadline instead of waiting for every thread unconditionally.
		A thread left running after this finishes on its own; whatever it appends past this point
		is not waited for."""
		for thread in threads:
			remaining = self.scrape_deadline - time.time()
			if remaining <= 0: break
			thread.join(timeout=remaining)
		abandoned = sum(1 for thread in threads if thread.is_alive())
		if abandoned:
			from modules.kodi_utils import logger
			logger('Red Light', 'folders scrape deadline reached with %d of %d %s threads still running' % (abandoned, len(threads), label))
		return abandoned

	def _scraper_worker(self, folder_results):
		scraper_threads = list(make_thread_list(lambda entry: self._scrape_directory(entry[0], below_title=entry[1]), folder_results))
		self._join_until_deadline(scraper_threads, 'subfolder')

	def _as_dir(self, path):
		"""Folder paths end with a slash (#172). zurg answers a WebDAV folder with the folder itself
		first, slash included; asked for the path without one, Kodi lists that entry as a subfolder of
		the same name, and the scraper went into <folder>/<same name> for a 404 on every folder."""
		return path if path.endswith(('/', '\\')) else path + '/'

	def _names_title(self, path):
		"""True when the configured path is the show's or film's own folder (.../Seinfeld/)."""
		return self.title_query in source_utils.clean_title(normalize(os.path.basename(path.rstrip('/\\'))))

	def _film_file_matches(self, normalized):
		"""The title check results() applies anyway, made before the file is opened for its size (#172),
		so a folder of other films costs no opens. Skipped when Filter by name is off, as there."""
		if not self.filter_title: return True
		return source_utils.check_title(self.title, normalized, self.aliases, self.year, self.season, self.episode)

	def _file_size(self, url_path):
		"""The size, or None with a log line: an exception here used to drop the file without a trace."""
		try: return self._get_size(url_path)
		except Exception as e:
			from modules.kodi_utils import logger
			logger('Red Light', 'folders: dropped %s, its size could not be read (%s)' % (url_path, e))
			return None

	def url_path(self, folder, file):
		return os.path.join(folder, file)

	def _get_size(self, file):
		if file.endswith('.strm'): return 'strm'
		with open_file(file) as f: s = f.size()
		return round(float(s)/1073741824, 2)

	def _year_query_list(self):
		return (str(self.year), str(self.year+1), str(self.year-1))

	def _season_query_list(self):
		return ('season%02d' % int(self.season), 'season%s' % self.season)
