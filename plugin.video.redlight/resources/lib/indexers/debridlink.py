# -*- coding: utf-8 -*-
import sys
from apis.debridlink_api import DebridLink
from modules import kodi_utils
from modules.source_utils import supported_video_extensions
from modules.utils import clean_file_name, normalize
logger = kodi_utils.logger

def dl_cloud():
	def _builder():
		for count, item in enumerate(cloud_torrents, 1):
			try:
				cm = []
				cm_append = cm.append
				folder_name, torrent_id = item.get('name', 'unknown'), item['id']
				clean_folder_name = clean_file_name(normalize(folder_name)).upper()
				display = '%02d | [B]FOLDER[/B] | [I]%s [/I]' % (count, clean_folder_name)
				url_params = {'mode': 'debridlink.browse_dl_cloud', 'id': torrent_id}
				delete_params = {'mode': 'debridlink.delete', 'id': torrent_id}
				cm_append(('[B]Delete Torrent[/B]', 'RunPlugin(%s)' % kodi_utils.build_url(delete_params)))
				url = kodi_utils.build_url(url_params)
				listitem = kodi_utils.make_listitem()
				listitem.setLabel(display)
				listitem.addContextMenuItems(cm)
				listitem.setArt({'icon': icon, 'poster': icon, 'thumb': icon, 'fanart': fanart, 'banner': icon})
				info_tag = listitem.getVideoInfoTag(True)
				info_tag.setPlot(' ')
				yield (url, listitem, True)
			except: pass
	try:
		cloud_torrents = DebridLink.user_cloud(fresh=True)
		cloud_torrents = [i for i in cloud_torrents if i.get('files')]
	except: cloud_torrents = []
	icon, fanart = kodi_utils.get_icon('debridlink'), kodi_utils.get_addon_fanart()
	handle = int(sys.argv[1])
	kodi_utils.add_items(handle, list(_builder()))
	kodi_utils.set_content(handle, kodi_utils.MENU_FOLDER_CONTENT)
	kodi_utils.end_directory(handle, cacheToDisc=False)
	kodi_utils.set_view_mode('view.premium', kodi_utils.MENU_FOLDER_CONTENT)

def browse_dl_cloud(torrent_id):
	def _builder():
		for count, item in enumerate(pack_info, 1):
			try:
				cm = []
				name, size = item.get('name') or 'unknown', float(int(item.get('size', 0)))/1073741824
				name = clean_file_name(name).upper()
				display = '%02d | [B]FILE[/B] | %.2f GB | [I]%s [/I]' % (count, size, name)
				file_key = '%s,%s' % (torrent_id, item.get('id'))
				url_params = {'mode': 'debridlink.resolve_dl', 'id': file_key, 'play': 'true'}
				url = kodi_utils.build_url(url_params)
				down_file_params = {'mode': 'downloader.runner', 'name': name, 'url': file_key, 'action': 'cloud.debridlink', 'image': icon}
				cm.append(('[B]Download File[/B]', 'RunPlugin(%s)' % kodi_utils.build_url(down_file_params)))
				listitem = kodi_utils.make_listitem()
				listitem.setLabel(display)
				listitem.addContextMenuItems(cm)
				listitem.setArt({'icon': icon, 'poster': icon, 'thumb': icon, 'fanart': fanart, 'banner': icon})
				info_tag = listitem.getVideoInfoTag(True)
				info_tag.setPlot(' ')
				yield (url, listitem, False)
			except: pass
	icon, fanart = kodi_utils.get_icon('debridlink'), kodi_utils.get_addon_fanart()
	handle = int(sys.argv[1])
	torrent = DebridLink.torrent_info(torrent_id)
	extensions = supported_video_extensions()
	files = (torrent or {}).get('files') or []
	pack_info = sorted([i for i in files if (i.get('name') or '').lower().endswith(tuple(extensions))], key=lambda k: k.get('name', ''))
	kodi_utils.add_items(handle, list(_builder()))
	kodi_utils.set_content(handle, kodi_utils.MENU_FOLDER_CONTENT)
	kodi_utils.end_directory(handle, cacheToDisc=False)
	kodi_utils.set_view_mode('view.premium', kodi_utils.MENU_FOLDER_CONTENT)

def dl_delete(torrent_id):
	if not kodi_utils.confirm_dialog(): return
	DebridLink.delete_torrent(torrent_id)
	DebridLink.clear_cache()
	kodi_utils.execute_builtin('Container.Refresh')

def resolve_dl(params):
	file_id = params['id']
	resolved_link = DebridLink.unrestrict_link(file_id)
	if not resolved_link:
		kodi_utils.ok_dialog(heading='Debrid-Link', text='Unable to resolve this cloud link. It may be expired or no longer available.')
		return None
	if params.get('play', 'false') != 'true': return resolved_link
	from modules.player import RedLightPlayer
	RedLightPlayer().run(resolved_link, 'video')

def dl_account_info():
	try:
		from modules.service_expiry import append_expiry_lines, fetch_expiry_summary
		kodi_utils.show_busy_dialog()
		response = DebridLink.account_info()
		account_info = (response or {}).get('value') or {}
		body = []
		append = body.append
		append('[B]Account:[/B] %s' % account_info.get('email', 'Unknown'))
		append('[B]Username:[/B] %s' % account_info.get('username', 'Unknown'))
		account_type = account_info.get('accountType')
		append('[B]Status:[/B] %s' % ('Premium' if account_type else 'Free'))
		append_expiry_lines(body, fetch_expiry_summary('dl'))
		kodi_utils.hide_busy_dialog()
		return kodi_utils.show_text('DEBRID-LINK', '\n\n'.join(body), font_size='large')
	except: kodi_utils.hide_busy_dialog()
