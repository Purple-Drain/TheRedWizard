# -*- coding: utf-8 -*-
import time
import requests
from threading import Thread
from urllib.parse import urlencode
from caches.settings_cache import get_setting, set_setting
from caches.main_cache import cache_object
from modules.source_utils import supported_video_extensions, seas_ep_filter, extras
from modules.kodi_utils import ok_dialog, notification, confirm_dialog, sleep, kodi_dialog

base_url = 'https://debrid-link.com/api/v2/'
session = requests.Session()


class DebridLinkAPI:
	'''Debrid-Link auth is a static personal API key (debrid-link.com/token_app), used
	directly as the Bearer token — Debrid-Link has no device-code OAuth polling endpoint
	confirmed for third-party apps, unlike RD/TorBox/AllDebrid/Offcloud.'''

	def __init__(self):
		self.token = get_setting('redlight.dl.token', 'empty_setting')

	def _headers(self):
		return {'Authorization': 'Bearer %s' % self.token}

	def _safe_json(self, response):
		try: return response.json()
		except Exception: return None

	def _get(self, url, data=None, timeout=20):
		if self.token in ('empty_setting', '', None): return None
		try:
			response = session.get(base_url + url, params=data or {}, headers=self._headers(), timeout=timeout)
			return self._safe_json(response)
		except Exception: return None

	def _post(self, url, json=None, data=None, timeout=30):
		if self.token in ('empty_setting', '', None): return None
		try:
			response = session.post(base_url + url, json=json, data=data, headers=self._headers(), timeout=timeout)
			return self._safe_json(response)
		except Exception: return None

	def _delete(self, url, timeout=20):
		if self.token in ('empty_setting', '', None): return None
		try:
			response = session.delete(base_url + url, headers=self._headers(), timeout=timeout)
			return self._safe_json(response)
		except Exception: return None

	# ----------- AUTH -----------
	def account_info(self):
		return self._get('account/infos')

	def auth(self):
		key = kodi_dialog().input('Debrid-Link API Key (from debrid-link.com/token_app)')
		if not key or not key.strip(): return
		self.token = key.strip()
		response = self.account_info()
		if not response or not response.get('success'):
			self.token = 'empty_setting'
			set_setting('dl.token', 'empty_setting')
			set_setting('dl.enabled', 'false')
			return ok_dialog(heading='Debrid-Link', text='Authorisation failed. Check the API key and try again.')
		set_setting('dl.token', self.token)
		set_setting('dl.enabled', 'true')
		ok_dialog(heading='Debrid-Link', text='Account authorised.')

	def revoke(self):
		if not confirm_dialog(): return
		set_setting('dl.token', 'empty_setting')
		set_setting('dl.enabled', 'false')
		notification('Debrid-Link Authorisation Reset', 3000)

	# ----------- CLOUD (SEEDBOX/TORRENTS) -----------
	def user_cloud(self, fresh=False):
		if fresh:
			return self._list_torrents_fresh()
		return cache_object(self._list_torrents_fresh, 'dl_user_cloud', [], False, 0.03)

	def _list_torrents_fresh(self):
		response = self._get('seedbox/list', data={'perPage': 100})
		if not response or not response.get('success'): return []
		return response.get('value') or []

	def torrent_info(self, torrent_id, fresh=True):
		response = self._get('seedbox/list', data={'ids': str(torrent_id)})
		if not response or not response.get('success'): return None
		value = response.get('value') or []
		return value[0] if value else None

	def add_magnet(self, magnet):
		return self._post('seedbox/add', json={'url': magnet, 'wait': True, 'async': False})

	@staticmethod
	def _torrent_from_add(response):
		if not response or not response.get('success'): return None
		value = response.get('value')
		return value if isinstance(value, dict) else None

	def create_transfer(self, magnet_url):
		torrent = self._torrent_from_add(self.add_magnet(magnet_url))
		return str(torrent['id']) if torrent and torrent.get('id') else ''

	def delete_torrent(self, torrent_id):
		return self._delete('seedbox/%s/remove' % torrent_id)

	def _wait_for_torrent_files(self, torrent_id, max_attempts=45):
		for attempt in range(max_attempts):
			if attempt: sleep(1000)
			item = self.torrent_info(torrent_id)
			if not item: continue
			files = item.get('files') or []
			if files: return item, files
		return None, []

	@staticmethod
	def _file_label(file_item):
		return file_item.get('name') or 'unknown'

	@staticmethod
	def is_scrapeable_cloud_file(file_item, extensions, min_confirmed_bytes=1048576):
		label = DebridLinkAPI._file_label(file_item)
		if not label: return False
		lower = label.lower()
		if not lower.endswith(tuple(extensions)): return False
		if any(x in lower for x in extras()): return False
		try: size_bytes = int(file_item.get('size') or 0)
		except Exception: size_bytes = 0
		if size_bytes > 0 and size_bytes < min_confirmed_bytes: return False
		return True

	def resolve_magnet(self, magnet_url, info_hash, store_to_cloud, title, season, episode):
		torrent_id, cleanup_torrent = None, False
		try:
			extensions = supported_video_extensions()
			extras_filter = extras()
			extras_filtering_list = tuple(i for i in extras_filter if i not in (title or '').lower())
			torrent = self._torrent_from_add(self.add_magnet(magnet_url))
			if not torrent or not torrent.get('id'): return None
			torrent_id = torrent['id']
			cleanup_torrent = not store_to_cloud
			files = torrent.get('files') or []
			if not files:
				_item, files = self._wait_for_torrent_files(torrent_id, max_attempts=45)
			if not files: return None
			selected_files = []
			for item in files:
				filename = self._file_label(item)
				download_url = item.get('downloadUrl')
				if not download_url or not filename.lower().endswith(tuple(extensions)): continue
				try: size = int(item.get('size') or 0)
				except Exception: size = 0
				selected_files.append({'url': '%s,%s' % (torrent_id, item.get('id')), 'filename': filename, 'size': size})
			if not selected_files: return None
			if season:
				selected_files = [i for i in selected_files if seas_ep_filter(season, episode, i['filename'])]
			else:
				selected_files = [i for i in selected_files if not any(x in i['filename'] for x in extras_filtering_list)]
				selected_files.sort(key=lambda k: k['size'], reverse=True)
			if not selected_files: return None
			return self.unrestrict_link(selected_files[0]['url'])
		except Exception:
			return None
		finally:
			if cleanup_torrent and torrent_id:
				Thread(target=self.delete_torrent, args=(torrent_id,), daemon=True).start()

	def unrestrict_link(self, file_id):
		'''file_id is "torrent_id,file_id" — always re-fetch, downloadUrl can go stale between scrape and play.'''
		try:
			torrent_id, inner_file_id = str(file_id).split(',', 1)
			item = self.torrent_info(torrent_id)
			if not item: return None
			for f in item.get('files') or []:
				if str(f.get('id')) == inner_file_id:
					return f.get('downloadUrl')
			return None
		except Exception:
			return None

	def parse_magnet_pack(self, magnet_url, info_hash):
		torrent_id, keep_transfer = None, False
		try:
			extensions = supported_video_extensions()
			torrent = self._torrent_from_add(self.add_magnet(magnet_url))
			if not torrent or not torrent.get('id'): return None
			torrent_id = torrent['id']
			files = torrent.get('files') or []
			if not files:
				_item, files = self._wait_for_torrent_files(torrent_id, max_attempts=12)
			if not files: return None
			pack_files = []
			for item in files:
				filename = self._file_label(item)
				if not filename.lower().endswith(tuple(extensions)): continue
				pack_files.append({'link': '%s,%s' % (torrent_id, item.get('id')), 'filename': filename, 'size': item.get('size', 0), 'torrent_id': torrent_id})
			keep_transfer = bool(pack_files)
			return pack_files or None
		except Exception:
			return None
		finally:
			if torrent_id and not keep_transfer:
				try: self.delete_torrent(torrent_id)
				except: pass

	def display_magnet_pack(self, magnet_url, info_hash):
		return self.parse_magnet_pack(magnet_url, info_hash)

	def add_headers_to_url(self, url):
		return url

	def clear_cache(self, clear_hashes=True):
		try:
			from caches.debrid_cache import debrid_cache
			from caches.base_cache import connect_database
			dbcon = connect_database('maincache_db')
			try:
				dbcon.execute("""DELETE FROM maincache WHERE id=?""", ('dl_user_cloud',))
				user_cloud_success = True
			except Exception:
				user_cloud_success = False
			if clear_hashes:
				try:
					debrid_cache.clear_debrid_results('dl')
					hash_cache_status_success = True
				except Exception:
					hash_cache_status_success = False
			else:
				hash_cache_status_success = True
		except Exception:
			return False
		if False in (user_cloud_success, hash_cache_status_success): return False
		return True


DebridLink = DebridLinkAPI()
