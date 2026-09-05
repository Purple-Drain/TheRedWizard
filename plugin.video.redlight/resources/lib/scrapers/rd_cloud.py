# -*- coding: utf-8 -*-
import time
from apis.real_debrid_api import RealDebrid
from modules import source_utils
from threading import Thread
from modules.utils import clean_file_name, normalize
from modules.settings import enabled_debrids_check, filter_by_name
from caches.settings_cache import get_setting
# from modules.kodi_utils import logger

class source:
	def __init__(self):
		self.scrape_provider = 'rd_cloud'
		self.sources = []
		self.extensions = source_utils.supported_video_extensions()

	def results(self, info):
		try:
			if not enabled_debrids_check('rd'): return source_utils.internal_results(self.scrape_provider, self.sources)
			self.folder_results, self.scrape_results = [], []
			filter_title = filter_by_name(self.scrape_provider)
			self.media_type, title, self.tmdb_id = info.get('media_type'), info.get('title'), info.get('tmdb_id')
			self.year, self.season, self.episode = int(info.get('year') or 0), info.get('season'), info.get('episode')
			self.absolute_episode = info.get('absolute_episode')
			self.title_check = source_utils.episode_title_check(info)
			self.aliases = source_utils.get_aliases_titles(info.get('aliases', []))
			self.folder_query = source_utils.clean_title(normalize(title))
			self.folder_queries = source_utils.folder_title_queries(title, self.aliases)
			self.scrape_deadline = time.time() + self._deadline_seconds()
			self._scrape_downloads()
			self._scrape_cloud()
			if not self.scrape_results: return source_utils.internal_results(self.scrape_provider, self.sources)
			aliases = self.aliases
			def _process():
				# Snapshot: a torrent-info thread abandoned at the deadline may still append.
				for item in list(self.scrape_results):
					try:
						file_name = self._get_filename(item['path'])
						if self.media_type == 'episode':
							if not source_utils.cloud_episode_matches(self.season, self.episode, file_name, self.absolute_episode, self.title_check): continue
							if filter_title and not source_utils.check_title(title, file_name, aliases, self.year, 'pack', self.episode): continue
						elif filter_title and not source_utils.check_title(title, file_name, aliases, self.year, self.season, self.episode): continue
						display_name = clean_file_name(file_name).replace('html', ' ').replace('+', ' ').replace('-', ' ')
						file_dl, size = item['url_link'], round(float(item['bytes'])/1073741824, 2)
						video_quality, details = source_utils.get_file_info(name_info=source_utils.release_info_format(file_name))
						direct_debrid_link = item.get('direct_debrid_link', False)
						folder_id, cache_type = item.get('folder_id', ''), item.get('cache_type', '')
						source_item = {'name': file_name, 'display_name': display_name, 'quality': video_quality, 'size': size, 'size_label': '%.2f GB' % size,
									'extraInfo': details, 'url_dl': file_dl, 'id': file_dl, 'downloads': False, 'direct': True, 'source': self.scrape_provider, 'debrid': self.scrape_provider,
									'scrape_provider': self.scrape_provider, 'direct_debrid_link': direct_debrid_link, 'folder_id': folder_id, 'cache_type': cache_type}
						yield source_item
					except: pass
			self.sources = list(_process())
		except Exception as e:
			from modules.kodi_utils import logger
			logger('real-debrid scraper Exception', str(e))
		source_utils.internal_results(self.scrape_provider, self.sources)
		return self.sources

	def _deadline_seconds(self):
		"""Same scrape budget tb_cloud and pm_cloud give themselves (#112)."""
		return min(25, max(10, int(get_setting('redlight.results.timeout', '20'))))

	def _past_deadline(self, stage):
		if time.time() < self.scrape_deadline: return False
		from modules.kodi_utils import logger
		logger('Red Light', 'rd_cloud scrape deadline reached before %s' % stage)
		return True

	def _join_until_deadline(self, threads):
		"""Join the per-torrent info threads against the scrape deadline (#112). Each is a 20 s-capped
		torrents/info call and the join used to wait for all of them, so a slow Real-Debrid overran
		a background next-episode prep with nothing in the log to show why. An abandoned thread
		finishes on its own; whatever it appends after this point is not waited for."""
		for thread in threads:
			remaining = self.scrape_deadline - time.time()
			if remaining <= 0: break
			thread.join(timeout=remaining)
		abandoned = sum(1 for thread in threads if thread.is_alive())
		if abandoned:
			from modules.kodi_utils import logger
			logger('Red Light', 'rd_cloud scrape deadline reached with %d of %d torrent info fetches still running' % (abandoned, len(threads)))
		return abandoned

	def _scrape_cloud(self):
		try:
			if self._past_deadline('the torrent list'): return self.sources
			try:
				my_cloud_files = RealDebrid.user_cloud()
				my_cloud_files = [i for i in my_cloud_files if i['status'] == 'downloaded']
			except: return self.sources
			results_append = self.folder_results.append
			year_query_list = self._year_query_list()
			for item in my_cloud_files:
				normalized = normalize(item['filename'])
				folder_name = source_utils.clean_title(normalized)
				# A name that cleans to nothing cannot match the title; it used to be fetched
				# unconditionally, one torrents/info call and one thread per such torrent (#112).
				if not folder_name: continue
				if not any(q and q in folder_name for q in self.folder_queries): continue
				if self.media_type == 'movie' and not any(x in normalized for x in year_query_list): continue
				results_append(item['id'])
			if not self.folder_results: return self.sources
			if self._past_deadline('the torrent info fetches'): return self.sources
			threads = [Thread(target=self._scrape_folders, args=(i,)) for i in self.folder_results]
			[i.start() for i in threads]
			self._join_until_deadline(threads)
		except: pass

	def _scrape_folders(self, folder_info):
		try:
			if time.time() >= self.scrape_deadline: return
			folder_files = RealDebrid.user_cloud_info(folder_info)
			contents = [i for i in folder_files['files'] if i['selected'] == 1 and i['path'].lower().endswith(tuple(self.extensions))]
			file_urls = folder_files['links']
			scrape_results_append = self.scrape_results.append
			for c, i in enumerate(contents):
				try: i.update({'url_link': file_urls[c]})
				except: pass
			contents.sort(key=lambda k: k['path'])
			for item in contents:
				normalized = normalize(item['path'])
				if self.media_type == 'episode' and not source_utils.cloud_episode_matches(self.season, self.episode, normalized, self.absolute_episode, self.title_check): continue
				if item['path'].replace('/', '').lower() not in [d['path'].replace('/', '').lower() for d in self.scrape_results]:
					item.update({'folder_id': folder_info, 'cache_type': 'torrent'})
					scrape_results_append(item)
		except: pass

	def _scrape_downloads(self):
		try:
			my_downloads = RealDebrid.downloads()
			my_downloads = [i for i in my_downloads if i['download'].lower().endswith(tuple(self.extensions))]
			scrape_results_append = self.scrape_results.append
			year_query_list = self._year_query_list()
			for item in my_downloads:
				normalized = normalize(item['filename'])
				folder_name = source_utils.clean_title(normalized)
				if not any(q and q in folder_name for q in self.folder_queries): continue
				if self.media_type == 'movie':
					if not any(x in normalized for x in year_query_list): continue
				elif not source_utils.seas_ep_filter(self.season, self.episode, normalized): continue
				item = self.make_downloads_item(item)
				if item['path'].replace('/', '').lower() not in [d['path'].replace('/', '').lower() for d in self.scrape_results]: scrape_results_append(item)
		except: pass

	def make_downloads_item(self, item):
		return {'url_link': item['download'], 'bytes': item['filesize'], 'path': item['filename'], 'folder_id': item['id'], 'cache_type': 'download', 'direct_debrid_link': True}

	def _get_filename(self, name):
		if name.startswith('/'): name = name.split('/')[-1]
		return normalize(name)

	def _year_query_list(self):
		return (str(self.year), str(self.year+1), str(self.year-1))
