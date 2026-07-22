# -*- coding: utf-8 -*-
from apis.debridlink_api import DebridLink
from modules import source_utils
from threading import Thread
from modules.utils import clean_file_name, normalize
from modules.settings import enabled_debrids_check, filter_by_name

class source:
	def __init__(self):
		self.scrape_provider = 'dl_cloud'
		self.sources = []
		self.extensions = source_utils.supported_video_extensions()

	def results(self, info):
		try:
			if not enabled_debrids_check('dl'): return source_utils.internal_results(self.scrape_provider, self.sources)
			self.folder_results, self.scrape_results = [], []
			filter_title = filter_by_name(self.scrape_provider)
			self.media_type, title, self.tmdb_id = info.get('media_type'), info.get('title'), info.get('tmdb_id')
			self.year, self.season, self.episode = int(info.get('year')), info.get('season'), info.get('episode')
			self.folder_query = source_utils.clean_title(normalize(title))
			self._scrape_cloud()
			if not self.scrape_results: return source_utils.internal_results(self.scrape_provider, self.sources)
			aliases = source_utils.get_aliases_titles(info.get('aliases', []))
			def _process():
				for item in self.scrape_results:
					try:
						file_name = normalize(item['filename'])
						if self.media_type == 'episode':
							if not source_utils.cloud_episode_matches(self.season, self.episode, file_name): continue
							if filter_title and not source_utils.check_title(title, file_name, aliases, self.year, 'pack', self.episode): continue
						elif filter_title and not source_utils.check_title(title, file_name, aliases, self.year, self.season, self.episode): continue
						display_name = clean_file_name(file_name).replace('html', ' ').replace('+', ' ').replace('-', ' ')
						size = round(float(item.get('size', 0))/1073741824, 2)
						video_quality, details = source_utils.get_file_info(name_info=source_utils.release_info_format(file_name))
						url_link = '%s,%s' % (item['torrent_id'], item['file_id'])
						source_item = {'name': file_name, 'display_name': display_name, 'quality': video_quality, 'size': size, 'size_label': '%.2f GB' % size,
									'extraInfo': details, 'url_dl': url_link, 'id': url_link, 'downloads': False, 'direct': True, 'source': self.scrape_provider, 'debrid': self.scrape_provider,
									'scrape_provider': self.scrape_provider, 'direct_debrid_link': False, 'folder_id': item['torrent_id'], 'cache_type': 'torrent'}
						yield source_item
					except: pass
			self.sources = list(_process())
		except Exception as e:
			from modules.kodi_utils import logger
			logger('debrid-link scraper Exception', str(e))
		source_utils.internal_results(self.scrape_provider, self.sources)
		return self.sources

	def _scrape_cloud(self):
		try:
			my_cloud_torrents = DebridLink.user_cloud()
			results_append = self.folder_results.append
			year_query_list = self._year_query_list()
			for item in my_cloud_torrents:
				if not item.get('files'): continue
				normalized = normalize(item.get('name', ''))
				folder_name = source_utils.clean_title(normalized)
				if not folder_name: results_append(item)
				elif not self.folder_query in folder_name: continue
				else:
					if self.media_type == 'movie' and not any(x in normalized for x in year_query_list): continue
					results_append(item)
			if not self.folder_results: return self.sources
			threads = [Thread(target=self._scrape_torrent, args=(i,)) for i in self.folder_results]
			[i.start() for i in threads]
			[i.join() for i in threads]
		except: pass

	def _scrape_torrent(self, torrent_item):
		try:
			scrape_results_append = self.scrape_results.append
			existing = {(d['torrent_id'], d['filename'].lower()) for d in self.scrape_results}
			files = sorted(torrent_item.get('files') or [], key=lambda k: k.get('name', ''))
			for f in files:
				if not DebridLink.is_scrapeable_cloud_file(f, self.extensions): continue
				filename = f.get('name') or 'unknown'
				normalized = normalize(filename)
				if self.media_type == 'episode' and not source_utils.cloud_episode_matches(self.season, self.episode, normalized): continue
				key = (torrent_item['id'], filename.lower())
				if key not in existing:
					scrape_results_append({'torrent_id': torrent_item['id'], 'file_id': f.get('id'), 'filename': filename, 'size': f.get('size', 0)})
		except: pass

	def _year_query_list(self):
		return (str(self.year), str(self.year+1), str(self.year-1))
