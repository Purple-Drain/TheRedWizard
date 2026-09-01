# -*- coding: utf-8 -*-
from operator import itemgetter
from caches.episode_groups_cache import episode_groups_cache
from caches.meta_cache import meta_cache
from apis.tmdb_api import movie_details, tvshow_details, season_episodes_details, movie_set_details, movie_external_id, tvshow_external_id, \
								episode_groups_data, episode_group_details
from modules import settings
from modules.utils import jsondate_to_datetime, subtract_dates
# from modules.kodi_utils import logger

_ID_EMPTY = (None, '', 'None', 'empty_setting', 0, '0')

def _media_id_candidates(id_type, media_id, keys=('tmdb', 'imdb', 'tvdb')):
	"""Ordered (id_type, id) lookups for trakt_dict maps. Prefer TMDb, then IMDb/TVDb."""
	key_map = {'tmdb': 'tmdb_id', 'imdb': 'imdb_id', 'tvdb': 'tvdb_id'}
	if id_type == 'trakt_dict':
		if not isinstance(media_id, dict): return []
		candidates = []
		for key in keys:
			value = media_id.get(key, None)
			if value in _ID_EMPTY: continue
			candidates.append((key_map[key], value))
		return candidates
	if media_id in _ID_EMPTY: return []
	return [(id_type, media_id)]

def movie_meta(id_type, media_id, api_key, mpaa_region, current_date, current_time=None, dbcon=None, _alt_ids=None):
	if id_type == 'trakt_dict':
		candidates = _media_id_candidates('trakt_dict', media_id, keys=('tmdb', 'imdb'))
		if not candidates: return None
		id_type, media_id = candidates[0]
		_alt_ids = candidates[1:]
	elif _alt_ids is None:
		_alt_ids = []
	if media_id == None: return None
	meta = meta_cache.get('movie', id_type, media_id, current_time, dbcon=dbcon)
	if meta:
		if meta.get('blank_entry') and _alt_ids:
			nxt_type, nxt_id = _alt_ids[0]
			return movie_meta(nxt_type, nxt_id, api_key, mpaa_region, current_date, current_time, dbcon, _alt_ids=_alt_ids[1:])
		return meta
	try:
		if id_type in ('tmdb_id', 'imdb_id'): data = movie_details(media_id, api_key)
		else:
			external_result = movie_external_id(id_type, media_id, api_key)
			if not external_result: data = None
			else: data = movie_details(external_result['id'], api_key)
		if not data: return None
		elif data.get('status_code') in (6, 34, 37):
			if id_type == 'tmdb_id': meta = {'tmdb_id': media_id, 'imdb_id': 'tt0000000', 'tvdb_id': '0000000', 'blank_entry': True}
			else: meta = {'tmdb_id': '0000000', 'imdb_id': media_id, 'tvdb_id': '0000000', 'blank_entry': True}
			meta_cache.set('movie', id_type, meta, 24, current_time, dbcon=dbcon)
			if _alt_ids:
				nxt_type, nxt_id = _alt_ids[0]
				return movie_meta(nxt_type, nxt_id, api_key, mpaa_region, current_date, current_time, dbcon, _alt_ids=_alt_ids[1:])
			return meta
		tmdb_image_url, youtube_url = 'https://image.tmdb.org/t/p/%s%s', 'plugin://plugin.video.youtube/play/?video_id=%s'
		data_get = data.get
		cast, short_cast, writer, director, all_trailers, country, country_codes, studio, stinger_keys = [], [], [], [], [], [], [], [], []
		mpaa, trailer = '', ''
		tmdb_id, imdb_id = data_get('id', ''), data_get('imdb_id', '')
		rating, votes = data_get('vote_average', ''), data_get('vote_count', '')
		plot, tagline, premiered = data_get('overview', ''), data_get('tagline', ''), data_get('release_date', '')
		rpdb_poster = 'https://api.ratingposterdb.com/%s/tmdb/poster-default/movie-%s.jpg?fallback=true' % ('%s', tmdb_id)
		poster_path = data_get('poster_path', '')
		if poster_path: poster = tmdb_image_url % ('w780', poster_path)
		else: poster = ''
		backdrop_path = data_get('backdrop_path', '')
		if backdrop_path: fanart = tmdb_image_url % ('w1280', backdrop_path)
		else: fanart = ''
		images = data_get('images', {})
		if images:
			try:
				clearlogo = next((tmdb_image_url % ('original', i['file_path']) for i in images['logos']), '')
				if not clearlogo.endswith('png'): clearlogo = clearlogo.replace(clearlogo.split('.')[-1], 'png')
			except: clearlogo = ''
			try:
				logo_path = images.get('logos')[0].get('file_path')
				if logo_path.endswith('png'): clearlogo = tmdb_image_url % ('original', logo_path)
				else: clearlogo = tmdb_image_url % ('original', logo_path.replace(logo_path.split('.')[-1], 'png'))
			except: clearlogo = ''
			try: landscape = next((tmdb_image_url % ('w1280', i['file_path']) for i in images['backdrops'] if i['iso_639_1'] == 'en'), '')
			except: landscape = ''
			if not poster: poster = next((tmdb_image_url % ('w780', i['file_path']) for i in images['posters'] if i['iso_639_1'] == 'en'), '')
			if not fanart: fanart = next((tmdb_image_url % ('w1280', i['file_path']) for i in images['backdrops'] if i['iso_639_1'] in (None, 'xx')), '')
		else: clearlogo, landscape = '', ''
		title, original_title = data_get('title'), data_get('original_title')
		try:
			translations = data_get('translations')['translations']
			english_title = next((i['data']['title'] for i in translations if i['iso_639_1'] == 'en'), None)
		except: english_title = None
		try: year = str(data_get('release_date').split('-')[0])
		except: year = ''
		try: duration = int(data_get('runtime', '90') * 60)
		except: duration = 0
		try:
			genres = data_get('genres')
			genre = [i['name'] for i in genres]
		except: genre = []
		rootname = '%s (%s)' % (title, year)
		companies = data_get('production_companies')
		if companies:
			if len(companies) == 1: studio = [companies[0]['name']]
			else:
				try: studio = [next(i['name'] for i in companies if i['logo_path'] not in ('', 'None', None)) or next(i['name'] for i in companies)]
				except: pass
		production_countries = data_get('production_countries', None)
		if production_countries:
			country = [i['name'] for i in production_countries]
			country_codes = [i['iso_3166_1'] for i in production_countries]
		release_dates = data_get('release_dates')
		if release_dates:
			release_results = release_dates['results']
			try: mpaa = next(x['certification'] for i in release_results for x in i['release_dates'] if i['iso_3166_1'] == mpaa_region and x['certification'] != '')
			except: pass
		credits = data_get('credits')
		if credits:
			all_cast = credits.get('cast', None)
			if all_cast:
				try:
					cast = [{'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_url % ('h632', i['profile_path'])if i['profile_path'] else ''} for i in all_cast]
					short_cast = cast[:10]
				except: pass
			crew = credits.get('crew', None)
			if crew:
				try: writer = [i['name'] for i in crew if i['job'] in ('Author', 'Writer', 'Screenplay', 'Characters')]
				except: pass
				try: director = [i['name'] for i in crew if i['job'] == 'Director']
				except: pass
		alternative_titles = data_get('alternative_titles', [])
		if alternative_titles:
			from modules.source_utils import filter_alternative_titles
			alternative_titles = filter_alternative_titles(alternative_titles, titles_key='titles')
		else: alternative_titles = []
		videos = data_get('videos', None)
		if videos:
			try:
				vid_results = videos['results']
				all_trailers = sorted([i for i in vid_results if i['site'] == 'YouTube'], key=lambda x: x['name'])
				if all_trailers:
					trailer = \
					next((youtube_url % i['key'] for i in all_trailers if i['official'] and i['type'] == 'Trailer' and 'official trailer' in i['name'].lower()), None) or \
					next((youtube_url % i['key'] for i in all_trailers if i['official'] and i['type'] == 'Trailer'), None) or \
					next((youtube_url % i['key'] for i in all_trailers if i['type'] == 'Trailer'), None) or \
					next((youtube_url % i['key'] for i in all_trailers if 'trailer' in i['name'].lower()), None) or \
					next((youtube_url % i['key'] for i in all_trailers), None) or ''
				else: trailler = ''
			except: pass
		keywords = data_get('keywords', None)
		if keywords: stinger_keys = [i['name'] for i in keywords['keywords'] if i['name'] in ('duringcreditsstinger', 'aftercreditsstinger')]
		status, homepage = data_get('status', 'N/A'), data_get('homepage', 'N/A')
		belongs_to_collection = data_get('belongs_to_collection')
		if belongs_to_collection: ei_collection_name, ei_collection_id = belongs_to_collection['name'], belongs_to_collection['id']
		else: ei_collection_name, ei_collection_id = None, None
		try: ei_budget = '${:,}'.format(data_get('budget'))
		except: ei_budget = '$0'
		try: ei_revenue = '${:,}'.format(data_get('revenue'))
		except: ei_revenue = '$0'
		extra_info = {'status': status, 'budget': ei_budget, 'revenue': ei_revenue, 'homepage': homepage, 'collection_name': ei_collection_name, 'collection_id': ei_collection_id}
		meta = {'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'rating': rating, 'tagline': tagline, 'votes': votes, 'premiered': premiered, 'imdbnumber': imdb_id, 'trailer': trailer,
				'poster': poster, 'fanart': fanart, 'genre': genre, 'title': title, 'original_title': original_title, 'english_title': english_title, 'year': year, 'cast': cast,
				'duration': duration, 'rootname': rootname, 'country': country, 'country_codes': country_codes, 'mpaa': mpaa,'writer': writer, 'all_trailers': all_trailers,
				'director': director, 'alternative_titles': alternative_titles, 'plot': plot, 'studio': studio, 'extra_info': extra_info, 'mediatype': 'movie', 'tvdb_id': 'None',
				'clearlogo': clearlogo, 'landscape': landscape, 'keywords': keywords, 'rpdb_poster': rpdb_poster, 'short_cast': short_cast, 'stinger_keys': stinger_keys}
		meta_cache.set('movie', id_type, meta, movie_expiry(current_date, meta), current_time, dbcon=dbcon)
	except: pass
	return meta

def tvshow_meta(id_type, media_id, api_key, mpaa_region, current_date, current_time=None, is_anime_list=None, dbcon=None, _alt_ids=None):
	# Simkl (and others) sometimes attach a movie TMDb id / dead TV id to season-split anime.
	# Keep TMDb first, but fall through to IMDb/TVDb so Plan to Watch still resolves.
	if id_type == 'trakt_dict':
		candidates = _media_id_candidates('trakt_dict', media_id)
		if not candidates: return None
		id_type, media_id = candidates[0]
		_alt_ids = candidates[1:]
	elif _alt_ids is None:
		_alt_ids = []
	if media_id == None: return None
	meta = meta_cache.get('tvshow', id_type, media_id, current_time, dbcon=dbcon)
	if meta:
		if meta.get('blank_entry') and _alt_ids:
			nxt_type, nxt_id = _alt_ids[0]
			return tvshow_meta(nxt_type, nxt_id, api_key, mpaa_region, current_date, current_time, is_anime_list, dbcon, _alt_ids=_alt_ids[1:])
		return meta_valid_check(meta, is_anime_list)
	try:
		if id_type == 'tmdb_id': data = tvshow_details(media_id, api_key)
		else:
			external_result = tvshow_external_id(id_type, media_id, api_key)
			if not external_result: data = None
			else: data = tvshow_details(external_result['id'], api_key)
		if not data or data.get('status_code', '') in (6, 34, 37):
			if id_type == 'tmdb_id': meta = {'tmdb_id': media_id, 'imdb_id': 'tt0000000', 'tvdb_id': '0000000', 'blank_entry': True}
			elif id_type == 'imdb_id': meta = {'tmdb_id': '0000000', 'imdb_id': media_id, 'tvdb_id': '0000000', 'blank_entry': True}
			else: meta = {'tmdb_id': '0000000', 'imdb_id': 'tt0000000', 'tvdb_id': media_id, 'blank_entry': True}
			meta_cache.set('tvshow', id_type, meta, 24, current_time, dbcon=dbcon)
			if _alt_ids:
				nxt_type, nxt_id = _alt_ids[0]
				return tvshow_meta(nxt_type, nxt_id, api_key, mpaa_region, current_date, current_time, is_anime_list, dbcon, _alt_ids=_alt_ids[1:])
			return meta
		tmdb_image_url, youtube_url = 'https://image.tmdb.org/t/p/%s%s', 'plugin://plugin.video.youtube/play/?video_id=%s'
		data_get = data.get
		cast, short_cast, writer, director, studio, all_trailers, country, country_codes = [], [], [], [], [], [], [], []
		mpaa, trailer = '', ''
		external_ids = data_get('external_ids')
		tmdb_id, imdb_id, tvdb_id = data_get('id', ''), external_ids.get('imdb_id', ''), external_ids.get('tvdb_id', 'None')
		rating, votes = data_get('vote_average', ''), data_get('vote_count', '')
		plot, tagline, premiered = data_get('overview', ''), data_get('tagline', ''), data_get('first_air_date', '')
		season_data, total_seasons = data_get('seasons'), data_get('number_of_seasons')
		rpdb_poster = 'https://api.ratingposterdb.com/%s/tmdb/poster-default/series-%s.jpg?fallback=true' % ('%s', tmdb_id)
		poster_path = data_get('poster_path', '')
		if poster_path: poster = tmdb_image_url % ('w780', poster_path)
		else: poster = ''
		backdrop_path = data_get('backdrop_path', '')
		if backdrop_path: fanart = tmdb_image_url % ('w1280', backdrop_path)
		else: fanart = ''
		images = data_get('images', {})
		if images:
			try:
				clearlogo = next((tmdb_image_url % ('original', i['file_path']) for i in images['logos']), '')
				if not clearlogo.endswith('png'): clearlogo = clearlogo.replace(clearlogo.split('.')[-1], 'png')
			except: clearlogo = ''
			try:
				logo_path = images.get('logos')[0].get('file_path')
				if logo_path.endswith('png'): clearlogo = tmdb_image_url % ('original', logo_path)
				else: clearlogo = tmdb_image_url % ('original', logo_path.replace(logo_path.split('.')[-1], 'png'))
			except: clearlogo = ''
			try: landscape = next((tmdb_image_url % ('w1280', i['file_path']) for i in images['backdrops'] if i['iso_639_1'] == 'en'), '')
			except: landscape = ''
			if not poster: poster = next((tmdb_image_url % ('w780', i['file_path']) for i in images['posters'] if i['iso_639_1'] == 'en'), '')
			if not fanart: fanart = next((tmdb_image_url % ('w1280', i['file_path']) for i in images['backdrops'] if i['iso_639_1'] == 'xx'), '')
		else: clearlogo, landscape = '', ''
		title, original_title = data_get('name'), data_get('original_name')
		try:
			translations = data_get('translations')['translations']
			english_title = [i['data']['name'] for i in translations if i['iso_639_1'] == 'en'][0]
		except: english_title = None
		try: year = str(data_get('first_air_date').split('-')[0]) or ''
		except: year = ''
		try: duration = min(data_get('episode_run_time'))*60
		except: duration = 0
		try:
			genres = data_get('genres')
			genre = [i['name'] for i in genres]
		except: genre = []
		rootname = '%s (%s)' % (title, year)
		networks = data_get('networks', None)
		if networks:
			if len(networks) == 1: studio = [networks[0]['name']]
			else:
				try: studio = [next(i['name'] for i in networks if i['logo_path'] not in ('', 'None', None)) or next(i['name'] for i in networks)]
				except: pass
		production_countries = data_get('production_countries', None)
		if production_countries:
			country = [i['name'] for i in production_countries]
			country_codes = [i['iso_3166_1'] for i in production_countries]
		content_ratings = data_get('content_ratings', None)
		if content_ratings:
			try: mpaa = next((i['rating'] for i in content_ratings['results'] if i['iso_3166_1'] == mpaa_region), '')
			except: pass
		credits = data_get('credits')
		if credits:
			all_cast = credits.get('cast', None)
			if all_cast:
				try:
					cast = [{'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_url % ('h632', i['profile_path']) if i['profile_path'] else ''} for i in all_cast]
					short_cast = cast[:10]
				except: pass
			crew = credits.get('crew', None)
			if crew:
				try: writer = [i['name'] for i in crew if i['job'] in ('Author', 'Writer', 'Screenplay', 'Characters')]
				except: pass
				try: director = [i['name'] for i in crew if i['job'] == 'Director']
				except: pass
		alternative_titles = data_get('alternative_titles', [])
		if alternative_titles:
			from modules.source_utils import filter_alternative_titles
			alternative_titles = filter_alternative_titles(alternative_titles, titles_key='results')
		else: alternative_titles = []
		videos = data_get('videos', None)
		if videos:
			try:
				vid_results = videos['results']
				all_trailers = sorted([i for i in vid_results if i['site'] == 'YouTube'], key=lambda x: x['name'])
				if all_trailers:
					trailer = \
					next((youtube_url % i['key'] for i in all_trailers if i['official'] and i['type'] == 'Trailer' and 'official trailer' in i['name'].lower()), None) or \
					next((youtube_url % i['key'] for i in all_trailers if i['official'] and i['type'] == 'Trailer'), None) or \
					next((youtube_url % i['key'] for i in all_trailers if i['type'] == 'Trailer'), None) or \
					next((youtube_url % i['key'] for i in all_trailers if 'trailer' in i['name'].lower()), None) or \
					next((youtube_url % i['key'] for i in all_trailers), None) or ''
				else: trailler = ''
			except: pass
		keywords = data_get('keywords', None)
		status, _type, homepage = data_get('status', 'N/A'), data_get('type', 'N/A'), data_get('homepage', 'N/A')
		created_by = data_get('created_by', None)
		if created_by:
			try: ei_created_by = ', '.join([i['name'] for i in created_by])
			except: ei_created_by = 'N/A'
		else: ei_created_by = 'N/A'
		ei_next_ep, ei_last_ep = data_get('next_episode_to_air', None), data_get('last_episode_to_air', None)
		if ei_last_ep and not status in ('Ended', 'Canceled'):
			total_aired_eps = sum([i['episode_count'] for i in season_data if i['season_number'] < ei_last_ep['season_number'] \
																		and i['season_number'] != 0]) + ei_last_ep['episode_number']
		elif ei_last_ep and status in ('Ended', 'Canceled'):
			# Count through last aired only — TMDb number_of_episodes can include unaired placeholder
			# seasons (e.g. S2E1 with no air_date after an Ended S1 finale).
			last_s, last_e = ei_last_ep['season_number'], ei_last_ep['episode_number']
			prior = sum(i['episode_count'] for i in season_data if 0 < i['season_number'] < last_s)
			cur = next((i for i in season_data if i['season_number'] == last_s), None)
			cur_count = (cur or {}).get('episode_count') or 0
			if last_e <= cur_count: total_aired_eps = prior + last_e
			else: total_aired_eps = prior + cur_count if (prior + cur_count) else data_get('number_of_episodes')
		else: total_aired_eps = data_get('number_of_episodes')
		extra_info = {'status': status, 'type': _type, 'homepage': homepage, 'created_by': ei_created_by, 'next_episode_to_air': ei_next_ep, 'last_episode_to_air': ei_last_ep}
		meta = {'tmdb_id': tmdb_id, 'tvdb_id': tvdb_id, 'imdb_id': imdb_id, 'rating': rating, 'plot': plot, 'tagline': tagline, 'votes': votes, 'premiered': premiered, 'year': year,
				'poster': poster, 'fanart': fanart, 'genre': genre, 'title': title, 'original_title': original_title, 'english_title': english_title, 'season_data': season_data,
				'alternative_titles': alternative_titles, 'duration': duration, 'rootname': rootname, 'imdbnumber': imdb_id, 'country': country, 'mpaa': mpaa, 'trailer': trailer,
				'country_codes': country_codes, 'writer': writer, 'director': director, 'all_trailers': all_trailers, 'cast': cast, 'studio': studio, 'extra_info': extra_info,
				'total_aired_eps': total_aired_eps, 'mediatype': 'tvshow', 'total_seasons': total_seasons, 'tvshowtitle': title, 'status': status, 'clearlogo': clearlogo,
				'landscape': landscape, 'keywords': keywords, 'rpdb_poster': rpdb_poster, 'short_cast': short_cast}
		meta_cache.set('tvshow', id_type, meta, tvshow_expiry(current_date, meta), current_time, dbcon=dbcon)
	except: pass
	return meta_valid_check(meta, is_anime_list)

def movieset_meta(media_id, api_key, current_time=None):
	if media_id == None: return None
	id_type = 'tmdb_id'
	meta = meta_cache.get('movie_set', id_type, media_id, current_time)
	if meta: return meta
	try:
		data = movie_set_details(media_id, api_key)
		if not data: return None
		elif 'status_code' in data and data.get('status_code') in (6, 34, 37):
			meta = {'tmdb_id': media_id, 'fanart_added': True, 'blank_entry': True}
			meta_cache.set('movie_set', id_type, meta, 24, current_time)
			return meta
		data_get = data.get
		tmdb_image_url = 'https://image.tmdb.org/t/p/%s%s'
		title, tmdb_id, plot = data_get('name'), data_get('id'), data_get('overview', '')
		poster_path = data_get('poster_path', None)
		if poster_path: poster = tmdb_image_url % ('w780', poster_path)
		else: poster = ''
		backdrop_path = data_get('backdrop_path', None)
		if backdrop_path: fanart = tmdb_image_url % ('w1280', backdrop_path)
		else: fanart = ''
		parts = data_get('parts')
		meta = {'tmdb_id': tmdb_id, 'title': title, 'plot': plot, 'poster': poster, 'fanart': fanart, 'parts': parts, 'imdb_id': 'None', 'tvdb_id': 'None'}
		meta_cache.set('movie_set', id_type, meta, 720, current_time)
	except: pass
	return meta

def episodes_meta(season, meta, force_refresh=False):
	def _process():
		midseason_premiere = False
		for ep_data in details:
			writer, director, guest_stars = [], [], []
			ep_data_get = ep_data.get
			title, plot, premiered = ep_data_get('name'), ep_data_get('overview'), ep_data_get('air_date')
			season, episode, episode_id = ep_data_get('season_number'), ep_data_get('episode_number'), ep_data_get('id')
			try:
				if episode == 1:
					if 'premiere' in season_type: episode_type = 'series_premiere'
					else: episode_type = 'season_premiere'
				elif midseason_premiere: episode_type, midseason_premiere = 'mid_season_premiere', False
				else:
					episode_type = ep_data_get('episode_type')
					if episode_type == 'mid_season': episode_type, midseason_premiere = 'mid_season_finale', True
					elif episode_type == 'finale':
						if 'finale' in season_type: episode_type = 'series_finale'
						else: episode_type = 'season_finale'
					else: episode_type = ''
			except: episode_type = ''
			try: duration = ep_data_get('runtime')*60
			except: duration = 30*60
			rating, votes, still_path = ep_data_get('vote_average'), ep_data_get('vote_count'), ep_data_get('still_path', None)
			if still_path: thumb = tmdb_image_url % ('original', still_path)
			else: thumb = None
			cast = ep_data_get('guest_stars', [])
			if cast:
				try: guest_stars = [{'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_url % ('h632', i['profile_path']) if i['profile_path'] else ''}\
									for i in cast]
				except: pass
			crew = ep_data_get('crew', None)
			if crew:
				try: writer = [i['name'] for i in crew if i['job'] in ('Author', 'Writer', 'Screenplay', 'Characters')]
				except: pass
				try: director = [i['name'] for i in crew if i['job'] == 'Director']
				except: pass
			yield {'writer': writer, 'director': director, 'mediatype': 'episode', 'episode_type': episode_type, 'episode_id': episode_id, 'title': title, 'plot': plot,
					'duration': duration, 'premiered': premiered, 'season': season, 'episode': episode, 'rating': rating, 'votes': votes, 'thumb': thumb, 'guest_stars': guest_stars}
	media_id, data = meta['tmdb_id'], None
	prop_string = '%s_%s' % (media_id, season)
	if force_refresh:
		try: meta_cache.delete_season(prop_string)
		except: pass
	else:
		data = meta_cache.get_season(prop_string)
		if data is not None: return data
	try:
		tmdb_image_url = 'https://image.tmdb.org/t/p/%s%s'
		season, tvshow_status, total_seasons = int(season), meta['status'], meta['total_seasons']
		if season == 1: season_type = 'premiere_finale' if (total_seasons == season and tvshow_status in ('Ended', 'Canceled')) else 'premiere'
		else: season_type = 'finale' if (total_seasons == season and tvshow_status in ('Ended', 'Canceled')) else ''
		# Airing current seasons need frequent refresh — weekly anime often lands mid-window.
		if tvshow_status in ('Ended', 'Canceled') or total_seasons > int(season): expiration = 4368
		else: expiration = 12
		details = season_episodes_details(media_id, season)['episodes']
		total_episodes = len(details)
		data = list(_process())
	except:
		# Do not cache [] on fetch failure — that poisons Next/In Progress for hours.
		return []
	meta_cache.set_season(prop_string, data, expiration)
	return data

def refresh_airing_show_meta(tmdb_id, season=None):
	"""Drop cached show + season episode lists so newly aired eps can appear in Next/In Progress."""
	try:
		if not tmdb_id: return
		meta_cache.delete('tvshow', 'tmdb_id', str(tmdb_id))
		if season not in (None, '', 'None'):
			meta_cache.delete_season('%s_%s' % (tmdb_id, int(season)))
		else:
			meta_cache.delete_all_seasons(str(tmdb_id))
	except: pass

def all_episodes_meta(meta, include_specials=False):
	from threading import Thread
	def _get_tmdb_episodes(season):
		try: data.extend(episodes_meta(season, meta))
		except: pass
	try:
		data = []
		season_data = meta['season_data']
		seasons = [i['season_number'] for i in season_data]
		if not include_specials: seasons = [i for i in seasons if not i == 0]
		threads = [Thread(target=_get_tmdb_episodes, args=(i,)) for i in seasons]
		[i.start() for i in threads]
		[i.join() for i in threads]
	except: pass
	return data

def episode_groups(media_id):
	try: groups = episode_groups_data(media_id)['results']
	except: groups = None
	return groups or None

def preferred_episode_group(groups, prefer_name=None):
	"""Pick one TMDb episode group for Auto/fallback scrape remaps.

	Order: optional exact name (e.g. anime "Seasons") → Original Air Date (type 1)
	→ first group. Absolute and other types are not preferred.
	"""
	if not groups:
		return None
	if prefer_name:
		named = next((g for g in groups if (g.get('name') or '').lower() == prefer_name.lower()), None)
		if named:
			return named
	def _type(group):
		try: return int(group.get('type'))
		except: return 0
	aired = next((g for g in groups if _type(g) == 1), None)
	if aired:
		return aired
	return groups[0]

def group_details(group_id):
	return episode_group_details(group_id)

def resolve_assigned_episode_group(tmdb_id):
	"""A show's active TMDb episode group: an explicit assignment, or (opted in) the anime
	Seasons-order fallback -- the same resolution sources.check_episode_group() uses for
	scrape remapping. Returns TMDb group details, or None if neither applies."""
	group_info = episode_groups_cache.get(tmdb_id)
	if not group_info and settings.anime_seasons_episode_group_fallback() and is_anime_check(tmdb_id=tmdb_id):
		groups = episode_groups(tmdb_id)
		if groups:
			group_info = preferred_episode_group(groups, prefer_name='seasons')
	if not group_info:
		return None
	return group_details(group_info['id'])

def group_traversal_reachable(details, season_data):
	"""Whether a group's buckets can safely replace raw seasons when browsing.

	Season rows come from raw season_data, so a bucket whose order has no matching raw season
	number is unreachable and its episodes would vanish. Groups that restructure seasons -- anime
	"Seasons"-order over one long raw season being the usual case -- fail this and keep the raw path.
	"""
	try:
		raw_seasons = {int(i.get('season_number')) for i in (season_data or [])
						if i.get('season_number') is not None}
		if not raw_seasons: return False
		orders = set()
		for group in (details or {}).get('groups', []):
			order = group.get('order')
			if order is None: return False
			orders.add(int(order))
		return bool(orders) and orders.issubset(raw_seasons)
	except Exception:
		return False

def group_season_counts(details, season_data):
	"""Episodes per season as the season list actually shows them under group-native traversal:
	the group's bucket for that season, plus raw episodes of that season the group never placed in
	any bucket (Seinfeld's DVD group omits 4 specials outright, and those stay in Specials).

	Derived from counts alone -- no per-episode metadata -- so it is safe to call while building the
	season list. Empty dict when the group cannot be traversed safely, so callers keep raw totals.
	"""
	try:
		if not group_traversal_reachable(details, season_data): return {}
		bucket_sizes, bucketed_per_season = {}, {}
		for group in details.get('groups', []):
			order = int(group.get('order'))
			episodes = group.get('episodes', []) or []
			bucket_sizes[order] = len(episodes)
			for entry in episodes:
				raw_season = entry.get('season_number')
				if raw_season is None: continue
				raw_season = int(raw_season)
				bucketed_per_season[raw_season] = bucketed_per_season.get(raw_season, 0) + 1
		counts = {}
		for item in season_data:
			number = item.get('season_number')
			if number is None: continue
			number = int(number)
			orphans = max(int(item.get('episode_count') or 0) - bucketed_per_season.get(number, 0), 0)
			counts[number] = bucket_sizes.get(number, 0) + orphans
		return counts
	except Exception:
		return {}

def group_episode_season_map(details):
	"""Raw (season, episode) -> the group order (bucket season) that claims it, across every
	bucket including Specials (order 0). A raw pair absent from this map belongs to no bucket at
	all and stays under its own raw season -- the same "orphan" rule group_season_counts() uses
	for its denominator, so a numerator built from this map (see watched_status.
	watched_info_group_season()) lines up with that denominator instead of staying raw-keyed (#75).
	"""
	mapping = {}
	try:
		for group in (details or {}).get('groups', []):
			order = group.get('order')
			if order is None: continue
			order = int(order)
			for entry in group.get('episodes', []) or []:
				raw_season, raw_episode = entry.get('season_number'), entry.get('episode_number')
				if raw_season is None or raw_episode is None: continue
				mapping[(int(raw_season), int(raw_episode))] = order
	except Exception:
		return {}
	return mapping

def group_season_bucket_episodes(details, season_number, meta):
	"""Raw episode metadata dicts for one group-native season list: the group's own bucket for
	that order (in bucket order), plus raw episodes of that raw season the group places in no
	bucket at all (orphans, appended after, sorted by raw episode number) -- exactly the set
	group_season_counts() sizes and build_episode_list() renders for this season.

	A bucket can span raw seasons (a group can move an episode between regular seasons, e.g.
	Seinfeld's DVD-order group folding raw S03E10 into group season 2), so each raw season it
	touches is fetched once via episodes_meta() and looked up by (season, episode).

	Returns (items, raw_seasons), or None if the season has no usable bucket. Shared by
	episodes.py (rendering) and watched_status.py's count_aired_group_season() (#77) so both
	agree on exactly which episodes a group-native season contains.
	"""
	try:
		wanted = int(season_number)
	except Exception:
		return None
	try:
		groups = (details or {}).get('groups', [])
		bucket = next((g for g in groups if g.get('order') == wanted), None)
		entries = sorted((bucket or {}).get('episodes', []) or [], key=lambda e: e.get('order', 0))
		raw_seasons = {wanted}
		for entry in entries:
			raw_season = entry.get('season_number')
			if raw_season is not None: raw_seasons.add(int(raw_season))
		by_key = {}
		for raw_season in raw_seasons:
			for item in (episodes_meta(raw_season, meta) or []):
				try: by_key[(raw_season, int(item['episode']))] = item
				except Exception: pass
		items = []
		for entry in entries:
			raw_season, raw_episode = entry.get('season_number'), entry.get('episode_number')
			if raw_season is None or raw_episode is None: continue
			item = by_key.get((int(raw_season), int(raw_episode)))
			if item: items.append(item)
		bucketed = set()
		for group in groups:
			for e in group.get('episodes', []) or []:
				s, ep = e.get('season_number'), e.get('episode_number')
				if s is None or ep is None: continue
				bucketed.add((int(s), int(ep)))
		orphans = [(raw_episode, item) for (raw_season, raw_episode), item in by_key.items()
					if raw_season == wanted and (raw_season, raw_episode) not in bucketed]
		items.extend(item for _, item in sorted(orphans))
		if not items: return None
		return items, raw_seasons
	except Exception:
		return None

def group_episode_data(details, episode_id=None, season_number=None, episode_number=None):
	def _comparer(episode_item):
		if episode_id: return episode_item['id'] == int(episode_id)
		else: return episode_item['season_number'] == int(season_number) and episode_item['episode_number'] == int(episode_number)
	groups = details['groups']
	episode_data = next(({'season': item['order'], 'episode': i['order'] + 1} for item in groups for i in item['episodes'] if _comparer(i)), None)
	return episode_data

def movie_meta_external_id(external_source, external_id, api_key):
	return movie_external_id(external_source, external_id, api_key)

def tvshow_meta_external_id(external_source, external_id, api_key):
	return tvshow_external_id(external_source, external_id, api_key)

def movie_expiry(current_date, meta):
	try:
		difference = subtract_dates(current_date, jsondate_to_datetime(meta['premiered'], '%Y-%m-%d', remove_time=True))
		if difference < 0: expiration = abs(difference) + 1
		elif difference <= 14: expiration = 168
		elif difference <= 30: expiration = 336
		elif difference <= 180: expiration = 720
		else: expiration = 4368
	except: return 720
	return max(expiration, 168)

def tvshow_expiry(current_date, meta):
	try:
		if meta['status'] in ('Ended', 'Canceled'): expiration = 4368
		else:
			try:
				next_air = meta['extra_info']['next_episode_to_air']['air_date']
				data = subtract_dates(jsondate_to_datetime(next_air, '%Y-%m-%d', remove_time=True), current_date) - 24
				if data <= 1: expiration = 24
				else: expiration = min(data * 24, 72)
			except:
				# Still-airing but TMDb has no next_episode_to_air yet — keep short for weekly anime.
				expiration = 12
	except: expiration = 12
	return expiration

def meta_valid_check(meta, is_anime_list):
	if is_anime_list == None: return meta
	if is_anime_check(meta) != is_anime_list: meta = {}
	return meta

def is_anime_check(meta=None, tmdb_id=None):
	if not meta: meta = meta_cache.get('tvshow', 'tmdb_id', tmdb_id)
	try:
		list(map(itemgetter('id'), meta.get('keywords').get('results', []))).index(210024)
		return True
	except: return False
