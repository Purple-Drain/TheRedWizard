# -*- coding: utf-8 -*-
import os
import re
import xbmc
import requests
from caches.settings_cache import get_setting, set_setting
from modules import kodi_utils as ku, settings as st

BASE_URL = 'https://api.opensubtitles.com/api/v1'
TIMEOUT = 20.0
_DEFAULT_API_KEY = 'GpubxF50wjXZXtRlq83Heh9serfjCFyI'


def effective_api_key():
	key = get_setting('redlight.playback.opensubs_api_key', 'empty_setting')
	if key not in (None, '', '0', 'empty_setting'):
		return str(key).strip()
	return _DEFAULT_API_KEY


def _api_key():
	return effective_api_key()


def _username():
	value = get_setting('redlight.playback.opensubs_username', 'empty_setting')
	return '' if value in (None, '', '0', 'empty_setting') else str(value).strip()


def _password():
	value = get_setting('redlight.playback.opensubs_password', 'empty_setting')
	return '' if value in (None, '', '0', 'empty_setting') else str(value).strip()


def _token():
	value = get_setting('redlight.playback.opensubs_token', '0')
	return '' if value in (None, '', '0', 'empty_setting') else str(value).strip()


def _headers(token=None):
	headers = {
		'Content-Type': 'application/json',
		'Api-Key': _api_key(),
		'User-Agent': 'RedLight/%s' % ku.addon_version(),
	}
	if token: headers['Authorization'] = token
	return headers


def _normalize_imdb(imdb_id):
	if not imdb_id: return ''
	imdb_id = str(imdb_id).strip()
	return imdb_id if imdb_id.startswith('tt') else 'tt%s' % imdb_id


def _subtitle_language_code():
	try: return xbmc.convertLanguage(st.subs_language_for_download(), xbmc.ISO_639_1)
	except: return 'en'


def _save_token(token):
	if token: set_setting('playback.opensubs_token', token)


def authorized():
	if not st.opensubs_configured(): return False
	token = _token()
	if token:
		try:
			response = requests.get('%s/infos/user' % BASE_URL, headers=_headers(token), timeout=TIMEOUT)
			if response.status_code == 200: return True
		except: pass
	try:
		response = requests.post('%s/login' % BASE_URL, headers=_headers(), json={'username': _username(), 'password': _password()}, timeout=TIMEOUT)
		if response.status_code != 200: return False
		token = response.json().get('token')
		if not token: return False
		_save_token(token)
		return True
	except: return False


def _search_subtitles(imdb_id, year, season, episode, language):
	token = _token()
	if not token and not authorized(): return []
	params = {'imdb_id': _normalize_imdb(imdb_id), 'languages': language or 'en'}
	if season not in (None, ''):
		params['season_number'] = int(season)
		params['episode_number'] = int(episode)
	elif year:
		params['year'] = int(year)
	try:
		response = requests.get('%s/subtitles' % BASE_URL, headers=_headers(token or _token()), params=params, timeout=TIMEOUT)
		if response.status_code == 401 and authorized():
			response = requests.get('%s/subtitles' % BASE_URL, headers=_headers(_token()), params=params, timeout=TIMEOUT)
		if response.status_code != 200: return []
		results = []
		for item in response.json().get('data') or []:
			files = (item.get('attributes') or {}).get('files') or []
			if not files: continue
			file_info = files[0]
			file_id, file_name = file_info.get('file_id'), file_info.get('file_name')
			if file_id and file_name: results.append({'file_id': file_id, 'file_name': file_name})
		return results
	except: return []


def _episode_in_filename(season, episode, filename):
	if not filename: return False
	lower = filename.lower()
	patterns = (
		r's%02de%02d' % (int(season), int(episode)),
		r's%d[eexx]%d' % (int(season), int(episode)),
		r'%dx%d' % (int(season), int(episode)),
		r'season[\.\s_-]*%d[\.\s_-]*episode[\.\s_-]*%d' % (int(season), int(episode)),
	)
	return any(re.search(pattern, lower) for pattern in patterns)


def _pick_best_subtitle(results, playing_filename=None, playing_item=None, season=None, episode=None):
	if not results: return None
	from indexers.subtitles import playback_release_context, _score_subtitle_release_match
	release_context = playback_release_context(playing_filename, playing_item)
	filtered = []
	for item in results:
		file_name = item.get('file_name') or ''
		if season not in (None, '') and episode not in (None, '') and not _episode_in_filename(season, episode, file_name):
			continue
		filtered.append(item)
	pool = filtered or list(results)
	pool.sort(key=lambda item: _score_subtitle_release_match(item, release_context), reverse=True)
	return pool[0]


def _download_subtitle_content(file_id):
	token = _token()
	if not token and not authorized(): return None
	try:
		response = requests.post('%s/download' % BASE_URL, headers=_headers(token or _token()), json={'file_id': file_id}, timeout=TIMEOUT)
		if response.status_code == 401 and authorized():
			response = requests.post('%s/download' % BASE_URL, headers=_headers(_token()), json={'file_id': file_id}, timeout=TIMEOUT)
		if response.status_code != 200: return None
		link = response.json().get('link')
		if not link: return None
		file_response = requests.get(link, timeout=TIMEOUT)
		if file_response.status_code != 200: return None
		try: content = file_response.text
		except: content = file_response.content
		if isinstance(content, bytes):
			try: content = content.decode('utf-8', 'ignore')
			except: return None
		return content
	except: return None


def fetch_alert_subtitle(imdb_id, season=None, episode=None, year=None, playing_filename=None, playing_item=None):
	if not st.opensubs_configured(): return None
	from indexers.subtitles import _looks_like_subtitle_content, _opensubs_alert_path
	results = _search_subtitles(imdb_id, year, season, episode, _subtitle_language_code())
	match = _pick_best_subtitle(results, playing_filename, playing_item, season, episode)
	if not match: return None
	content = _download_subtitle_content(match.get('file_id'))
	if not _looks_like_subtitle_content(content): return None
	final_path = _opensubs_alert_path(imdb_id, season, episode)
	try:
		with ku.open_file(final_path, 'w') as file: file.write(content)
		ku.set_property('redlight.active_subtitle_path', final_path)
	except: return None
	return final_path


def check_account():
	if not st.opensubs_configured():
		return ku.ok_dialog(heading='OpenSubtitles', text='Enter your OpenSubtitles username and password first.')
	try:
		response = requests.post('%s/login' % BASE_URL, headers=_headers(), json={'username': _username(), 'password': _password()}, timeout=TIMEOUT)
		if response.status_code != 200:
			return ku.ok_dialog(heading='OpenSubtitles', text='Login failed. Check your OpenSubtitles username and password.')
		data = response.json()
		token = data.get('token')
		if token: _save_token(token)
		user = data.get('user') or {}
		allowed = user.get('allowed_downloads')
		if allowed is None: allowed = 'unknown'
		return ku.ok_dialog(heading='OpenSubtitles', text='Account: %s[CR][CR]Allowed downloads (24h): %s' % (_username(), allowed))
	except:
		return ku.ok_dialog(heading='OpenSubtitles', text='Error checking OpenSubtitles account. Check your username and password.')


def revoke_access():
	for setting_id, value in (
		('playback.opensubs_username', 'empty_setting'),
		('playback.opensubs_password', 'empty_setting'),
		('playback.opensubs_token', '0'),
	):
		set_setting(setting_id, value)
	try:
		from caches.settings_cache import refresh_settings_manager_properties
		refresh_settings_manager_properties()
	except: pass
	try:
		from modules.settings import refresh_playback_subs_source, refresh_alert_timing_settings
		refresh_playback_subs_source()
		refresh_alert_timing_settings()
	except: pass
	return ku.ok_dialog(heading='OpenSubtitles', text='OpenSubtitles username, password, and saved login cleared.')


def test_login():
	return check_account()
