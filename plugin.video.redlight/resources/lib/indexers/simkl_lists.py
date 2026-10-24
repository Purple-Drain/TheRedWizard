# -*- coding: utf-8 -*-
import json
import sys
from threading import Thread
from apis.simkl_api import simkl_search_my_lists, simkl_get_custom_lists, simkl_get_custom_list_contents
from indexers.movies import Movies
from indexers.tvshows import TVShows
from modules import kodi_utils, settings
from modules.utils import paginate_list
from modules.settings import paginate, page_limit, widget_hide_next_page, jump_to_enabled

def in_simkl_lists(params):
	def _builder():
		for item in lists:
			try:
				list_id = item.get('id')
				if list_id in (None, '', 0, '0'): continue
				name = item.get('name') or 'Untitled'
				item_count = item.get('item_count', 0)
				display = '%s | [I](x%s)[/I]' % (name, item_count)
				url = kodi_utils.build_url({
					'mode': 'simkl.list.build_simkl_custom_list',
					'list_id': list_id,
					'list_name': name,
					'name': name
				})
				from modules import list_sort
				cm = [
					list_sort.custom_sort_context(kodi_utils.build_url, 'simkl.list:%s' % list_id),
					('[B]Add to Shortcut Folder[/B]', 'RunPlugin(%s)' % kodi_utils.build_url(
						{'mode': 'menu_editor.shortcut_folder_add_known', 'url': url}))
				]
				listitem = kodi_utils.make_listitem()
				listitem.setLabel(display)
				listitem.setArt({'icon': simkl_icon, 'poster': simkl_icon, 'thumb': simkl_icon, 'fanart': fanart, 'banner': fanart})
				info_tag = listitem.getVideoInfoTag()
				info_tag.setPlot(kodi_utils.list_folder_plot('', '', item_count))
				listitem.addContextMenuItems(cm)
				yield (url, listitem, True)
			except: pass
	handle = int(sys.argv[1])
	simkl_icon, fanart = kodi_utils.get_icon('simkl'), kodi_utils.get_addon_fanart()
	try:
		from apis.simkl_api import simkl_lists_with_media
		if settings.simkl_user_active():
			lists = simkl_lists_with_media(params.get('tmdb_id'), params.get('imdb_id'), params.get('tvdb_id'))
		else:
			lists = []
		kodi_utils.add_items(handle, list(_builder()))
	except: pass
	kodi_utils.set_content(handle, kodi_utils.MENU_FOLDER_CONTENT)
	kodi_utils.set_category(handle, params.get('category_name', 'Simkl Lists'))
	kodi_utils.end_directory(handle)
	kodi_utils.set_view_mode('view.main', kodi_utils.MENU_FOLDER_CONTENT)

def search_simkl_lists(params):
	def _builder():
		for item in results:
			try:
				if item.get('result_type') == 'list':
					list_id = item.get('list_id')
					if list_id in (None, '', 0, '0'): continue
					title = item.get('title') or 'Untitled'
					count = item.get('item_count', 0)
					display = '%s [I](x%s) · My Lists[/I]' % (title, count)
					url = kodi_utils.build_url({
						'mode': 'simkl.list.build_simkl_custom_list',
						'list_id': list_id,
						'list_name': title,
						'name': title
					})
					from modules import list_sort
					cm = [list_sort.custom_sort_context(kodi_utils.build_url, 'simkl.list:%s' % list_id)]
					listitem = kodi_utils.make_listitem()
					listitem.setLabel(display)
					listitem.setArt({'icon': simkl_icon, 'poster': simkl_icon, 'thumb': simkl_icon, 'fanart': fanart, 'banner': fanart})
					info_tag = listitem.getVideoInfoTag()
					info_tag.setPlot(kodi_utils.list_folder_plot(item.get('description'), '', count))
					listitem.addContextMenuItems(cm)
					yield (url, listitem, True)
					continue
				media_ids = item.get('media_ids') or {}
				tmdb_id = media_ids.get('tmdb')
				if not tmdb_id: continue
				title = item.get('title') or 'Unknown'
				status_label = item.get('status_label', '')
				media_kind = item.get('media_kind', 'movies')
				if media_kind == 'anime': display = '%s | [I]%s · Anime[/I]' % (title, status_label)
				else: display = '%s | [I]%s[/I]' % (title, status_label)
				if media_kind == 'movies':
					url = kodi_utils.build_url({'mode': 'extras_menu_choice', 'media_type': 'movie', 'tmdb_id': tmdb_id})
				else:
					url = kodi_utils.build_url({'mode': 'build_season_list', 'tmdb_id': tmdb_id})
				listitem = kodi_utils.make_listitem()
				listitem.setLabel(display)
				listitem.setArt({'icon': simkl_icon, 'poster': simkl_icon, 'thumb': simkl_icon, 'fanart': fanart, 'banner': fanart})
				info_tag = listitem.getVideoInfoTag()
				info_tag.setPlot('Simkl %s' % status_label)
				yield (url, listitem, True)
			except: pass
	handle = int(sys.argv[1])
	simkl_icon, fanart = kodi_utils.get_icon('simkl'), kodi_utils.get_addon_fanart()
	search_title = params.get('key_id') or params.get('query') or ''
	try:
		if not settings.simkl_user_active(): results = []
		else: results = simkl_search_my_lists(search_title)
		kodi_utils.add_items(handle, list(_builder()))
	except: pass
	kodi_utils.set_content(handle, kodi_utils.MENU_FOLDER_CONTENT)
	kodi_utils.set_category(handle, search_title.capitalize())
	kodi_utils.end_directory(handle)
	kodi_utils.set_view_mode('view.main', kodi_utils.MENU_FOLDER_CONTENT)

def _simkl_premium_item(payload, simkl_icon, fanart):
	item = (payload or {}).get('item') or {}
	title = item.get('title') or 'Simkl PRO/VIP required'
	plot = (payload or {}).get('message') or item.get('description') or 'Custom lists need a Simkl PRO or VIP account.'
	poster = item.get('poster') or simkl_icon
	listitem = kodi_utils.make_listitem()
	listitem.setLabel(title)
	listitem.setArt({'icon': poster, 'poster': poster, 'thumb': poster, 'fanart': fanart, 'banner': fanart})
	info_tag = listitem.getVideoInfoTag()
	info_tag.setPlot(plot)
	return (kodi_utils.build_url({'mode': 'navigator.simkl_lists'}), listitem, False)

def get_simkl_custom_lists(params):
	def _builder():
		for item in lists:
			try:
				list_id = item.get('id')
				if list_id in (None, '', 0, '0'): continue
				name = item.get('name') or 'Untitled'
				counts = item.get('counts') or {}
				item_count = counts.get('items', 0)
				user = ((item.get('user') or {}).get('name')) or ''
				description = ((item.get('description') or {}).get('short')
					or (item.get('description') or {}).get('full') or '')
				display = '%s [I](x%s)[/I]' % (name, item_count)
				url_params = {
					'mode': 'simkl.list.build_simkl_custom_list',
					'list_id': list_id,
					'list_name': name,
					'name': name
				}
				url = kodi_utils.build_url(url_params)
				from modules import list_sort
				cm = [
					list_sort.custom_sort_context(kodi_utils.build_url, 'simkl.list:%s' % list_id),
					('[B]Add to Shortcut Folder[/B]', 'RunPlugin(%s)' % kodi_utils.build_url(
					{'mode': 'menu_editor.shortcut_folder_add_known', 'url': url}))
				]
				listitem = kodi_utils.make_listitem()
				listitem.setLabel(display)
				listitem.setArt({'icon': simkl_icon, 'poster': simkl_icon, 'thumb': simkl_icon, 'fanart': fanart, 'banner': fanart})
				info_tag = listitem.getVideoInfoTag()
				info_tag.setPlot(kodi_utils.list_folder_plot(description, user, item_count, counts.get('likes')))
				listitem.addContextMenuItems(cm)
				yield (url, listitem, True)
			except: pass
	handle = int(sys.argv[1])
	simkl_icon, fanart = kodi_utils.get_icon('simkl'), kodi_utils.get_addon_fanart()
	try:
		if not kodi_utils.external():
			kodi_utils.set_property('redlight.exit_params', kodi_utils.build_folder_url({'mode': 'navigator.simkl_lists'}))
		if not settings.simkl_auth_v2():
			kodi_utils.notification('Simkl custom lists need AUTH V2. Revoke and Authorise again.', 4000)
			result = []
		else:
			payload = simkl_get_custom_lists()
			if payload.get('premium_only'):
				kodi_utils.notification((payload['premium_only'].get('message') or 'Simkl PRO/VIP required for custom lists.'), 4000)
				result = [_simkl_premium_item(payload['premium_only'], simkl_icon, fanart)]
			else:
				lists = payload.get('lists') or []
				result = list(_builder())
		kodi_utils.add_items(handle, result)
	except Exception as e:
		try: kodi_utils.logger('Simkl Lists', str(e))
		except: pass
	kodi_utils.set_content(handle, kodi_utils.MENU_FOLDER_CONTENT)
	kodi_utils.set_category(handle, params.get('category_name') or 'My Lists')
	kodi_utils.end_directory(handle)
	kodi_utils.set_view_mode('view.main', kodi_utils.MENU_FOLDER_CONTENT)

def build_simkl_custom_list(params):
	def _process(function, _list, _type):
		if not _list['list']: return
		item_list_extend(function(_list).worker())

	handle, is_external, content = int(sys.argv[1]), kodi_utils.external(), 'movies'
	list_name = params.get('list_name') or params.get('name') or 'My Lists'
	hide_next_page = is_external and widget_hide_next_page()
	simkl_icon, fanart = kodi_utils.get_icon('simkl'), kodi_utils.get_addon_fanart()
	try:
		threads, item_list = [], []
		item_list_extend = item_list.extend
		paginate_enabled = paginate(is_external)
		page_no, paginate_start = int(params.get('new_page', '1')), int(params.get('paginate_start', '0'))
		list_id = params.get('list_id')
		if page_no == 1 and not is_external:
			kodi_utils.set_property('redlight.exit_params', kodi_utils.build_folder_url(
				{'mode': 'simkl.list.get_simkl_custom_lists', 'category_name': 'My Lists'}))
		payload = simkl_get_custom_list_contents(list_id)
		if payload.get('premium_only'):
			kodi_utils.notification((payload['premium_only'].get('message') or 'Simkl PRO/VIP required for custom lists.'), 4000)
			kodi_utils.add_items(handle, [_simkl_premium_item(payload['premium_only'], simkl_icon, fanart)])
			kodi_utils.set_content(handle, kodi_utils.MENU_FOLDER_CONTENT)
			kodi_utils.set_category(handle, list_name)
			kodi_utils.end_directory(handle)
			kodi_utils.set_view_mode('view.main', kodi_utils.MENU_FOLDER_CONTENT)
			return
		result = payload.get('items') or []
		if params.get('shuffle', 'false') == 'true':
			from random import shuffle
			shuffle(result)
			for c, i in enumerate(result):
				i['order'] = c
		else:
			from modules import list_sort
			result = list_sort.sort_user_list(result, 'simkl.list:%s' % list_id)
		if paginate_enabled:
			limit = page_limit(is_external)
			process_list, total_pages = paginate_list(result, page_no, limit, paginate_start)
			if is_external: paginate_start = limit
		else:
			process_list, total_pages = result, 1
		all_movies = [i for i in process_list if i.get('type') == 'movie']
		all_tvshows = [i for i in process_list if i.get('type') == 'show']
		def _id_rows(rows):
			out = []
			for c, i in enumerate(rows):
				media_ids = i.get('media_ids') or {}
				if media_ids.get('tmdb') or media_ids.get('imdb') or media_ids.get('tvdb'):
					out.append((i.get('order', c), media_ids))
			return out
		movie_list = {'list': _id_rows(all_movies), 'id_type': 'trakt_dict', 'custom_order': 'true'}
		tvshow_list = {'list': _id_rows(all_tvshows), 'id_type': 'trakt_dict', 'custom_order': 'true'}
		content = max([('movies', len(all_movies)), ('tvshows', len(all_tvshows))], key=lambda k: k[1])[0]
		for item in ((Movies, movie_list, 'movies'), (TVShows, tvshow_list, 'tvshows')):
			threaded_object = Thread(target=_process, args=item)
			threaded_object.start()
			threads.append(threaded_object)
		[i.join() for i in threads]
		item_list.sort(key=lambda k: k[1])
		new_params = {
			'mode': 'simkl.list.build_simkl_custom_list',
			'list_id': list_id,
			'list_name': list_name,
			'paginate_start': paginate_start
		}
		kodi_utils.add_items(handle, [i[0] for i in item_list])
		if total_pages > 2 and jump_to_enabled() and not is_external:
			kodi_utils.add_dir(
				handle,
				{'mode': 'navigate_to_page_choice', 'current_page': page_no, 'total_pages': total_pages, 'url_params': json.dumps(new_params)},
				'Jump To...', 'item_jump', kodi_utils.get_icon('item_jump_landscape'), isFolder=False
			)
		if total_pages > page_no and not hide_next_page:
			new_page = str(page_no + 1)
			new_params['new_page'] = new_page
			kodi_utils.add_dir(handle, new_params, 'Next Page (%s) >>' % new_page, 'nextpage', kodi_utils.get_icon('nextpage_landscape'))
	except Exception as e:
		try: kodi_utils.logger('Simkl List Error', 'build_simkl_custom_list: %s' % e)
		except: pass
	kodi_utils.set_content(handle, content)
	kodi_utils.set_category(handle, list_name)
	no_disc_cache = is_external or params.get('shuffle', 'false') == 'true'
	kodi_utils.end_directory(handle, cacheToDisc=not no_disc_cache)
	kodi_utils.set_view_mode('view.%s' % content, content)
