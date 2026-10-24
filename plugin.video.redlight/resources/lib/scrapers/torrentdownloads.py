# -*- coding: utf-8 -*-
from apis import torrentdownloads_api
from modules import source_utils
from modules.native_torrents import filter_and_build_sources, merge_name_searches, name_search_queries, scrape_expiry, scrape_timeout
from modules.settings import torrentdownloads_scrape_active
from modules.utils import clean_file_name
from modules.kodi_utils import logger


class source:
	def __init__(self):
		self.scrape_provider = 'torrentdownloads'
		self.sources = []

	def results(self, info):
		try:
			if not torrentdownloads_scrape_active():
				return source_utils.internal_results(self.scrape_provider, self.sources)
			category = '8' if info.get('media_type') == 'episode' else '4'
			queries = name_search_queries(info)
			info_queries = set(queries[:1])
			if info.get('media_type') == 'episode' and info.get('season_packs', True):
				title = clean_file_name(info.get('title') or '').replace('&', 'and')
				try:
					season_i = int(info.get('season'))
					for season_query in (
						'%s S%02d' % (title, season_i),
						'%s Season %s' % (title, season_i),
					):
						if season_query not in queries:
							queries.append(season_query)
						info_queries.add(season_query)
				except Exception:
					pass
			def _search(query, timeout=10, expiration=24):
				return torrentdownloads_api.search(
					query, timeout=timeout, expiration=expiration, category=category,
					include_info=query in info_queries)
			files = merge_name_searches(
				_search, queries, scrape_timeout(info), scrape_expiry(info),
				info.get('scrape_deadline'))
			self.sources = filter_and_build_sources(self.scrape_provider, files, info)
		except Exception as e:
			logger('torrentdownloads scraper Exception', str(e))
		source_utils.internal_results(self.scrape_provider, self.sources)
		return self.sources
