# -*- coding: utf-8 -*-
import os
import re
import xbmc
import requests
from difflib import SequenceMatcher
from modules import kodi_utils as ku, settings as st

timeout = 20.0
_ALERT_SUB_MAX_REMAINING = 600
# When dialogue ends long before EOF, scan the final window for the first music/SFX cue (credits roll).
# Short tails (<60s) use seconds after last dialogue/cue directly.
_SUBS_UNSUBTITLED_TAIL_SEC = 60
_SUBS_PRE_CREDITS_REMAINING_SEC = 20
_SUBS_FINAL_TAIL_SCAN_SEC = 65
_SUB_EXTS = ('.srt', '.ass', '.ssa', '.sub', '.vtt')
_ACTIVE_SUB_PROP = 'redlight.active_subtitle_path'
_SUBMAKER_SKIP_LANGS = frozenset(('sub toolbox',))
_RELEASE_SOURCE_PATTERNS = (
	('BLURAY', ('bluray', 'blu.ray', 'bdrip', 'bd.rip')),
	('REMUX', ('remux', 'bdremux')),
	('WEB', ('webdl', 'web.dl', 'web-dl', 'webrip', 'web.rip', '.web.')),
	('HDTV', ('hdtv',)),
	('DVD', ('dvdrip', 'dvd.rip')),
	('HDRIP', ('hdrip', 'hd.rip')),
)
_PRIMARY_RELEASE_SOURCES = ('BLURAY', 'REMUX', 'WEB', 'HDTV', 'DVD', 'HDRIP')

def _normalize_release_text(text):
	return re.sub(r'[^a-z0-9.]+', '.', (text or '').lower()).strip('.')

def _release_filename_stem(filename):
	if not filename: return ''
	stem = os.path.splitext(os.path.basename(str(filename).split('|')[0].split('?')[0]))[0]
	return _normalize_release_text(stem)

def _detect_release_source_tags(text):
	norm = _normalize_release_text(text)
	tags = set()
	for tag, patterns in _RELEASE_SOURCE_PATTERNS:
		if any(pattern in norm for pattern in patterns):
			tags.add(tag)
	if 'REMUX' in tags:
		tags.add('BLURAY')
	return tags

def _primary_release_source(tags):
	for tag in _PRIMARY_RELEASE_SOURCES:
		if tag in tags: return tag
	return None

def playback_release_context(playing_filename=None, playing_item=None):
	parts = []
	if playing_item:
		for key in ('name', 'display_name', 'extraInfo', 'quality'):
			val = playing_item.get(key)
			if val: parts.append(str(val))
	if playing_filename: parts.append(str(playing_filename))
	combined = ' '.join(parts)
	stem = _release_filename_stem(playing_filename)
	if not stem and playing_item:
		stem = _release_filename_stem(playing_item.get('name') or playing_item.get('display_name'))
	return {'stem': stem, 'tags': _detect_release_source_tags(combined), 'text': _normalize_release_text(combined)}

def _subtitle_candidate_text(sub_ref):
	if isinstance(sub_ref, dict):
		for key in ('file_name', 'name', 'filename', 'title', 'label', 'url'):
			val = sub_ref.get(key)
			if val: return str(val)
		return ''
	return str(sub_ref or '')

def _score_subtitle_release_match(sub_ref, release_context):
	if not release_context: release_context = playback_release_context()
	sub_text = _subtitle_candidate_text(sub_ref)
	if not sub_text: return 0.0
	sub_norm = _normalize_release_text(sub_text)
	sub_stem = _release_filename_stem(sub_text)
	score = 0.0
	if release_context.get('stem') and sub_stem:
		score += SequenceMatcher(None, release_context['stem'], sub_stem).ratio()
	sub_tags = _detect_release_source_tags(sub_text)
	play_tags = release_context.get('tags') or set()
	play_primary = _primary_release_source(play_tags)
	sub_primary = _primary_release_source(sub_tags)
	if play_primary and sub_primary:
		if play_primary == sub_primary: score += 0.35
		else: score -= 0.35
	if 'proper' in release_context.get('text', '') and 'proper' in sub_norm: score += 0.08
	if 'repack' in release_context.get('text', '') and 'repack' in sub_norm: score += 0.08
	return score

def _submaker_api_url(manifest, params):
	return manifest.replace('manifest', params)

def _submaker_language_matches(candidate_lang, preferred_language):
	if not candidate_lang: return False
	lang = candidate_lang.strip()
	if lang.lower() in _SUBMAKER_SKIP_LANGS: return False
	if lang == preferred_language: return True
	try: pref_iso = xbmc.convertLanguage(preferred_language, xbmc.ISO_639_1)
	except: pref_iso = ''
	if not pref_iso: return False
	if lang.lower() == pref_iso.lower(): return True
	try:
		if xbmc.convertLanguage(lang, xbmc.ENGLISH_NAME).lower() == preferred_language.lower(): return True
	except: pass
	return False

def _submaker_usable_subs(subs):
	results = []
	for item in subs or []:
		if not item.get('url'): continue
		lang = (item.get('lang') or '').strip()
		if lang.lower() in _SUBMAKER_SKIP_LANGS or item.get('id') == 'sub_toolbox': continue
		results.append(item)
	return results

def _submaker_ranked_subs(subs, language, release_context=None):
	usable = _submaker_usable_subs(subs)
	preferred = [i for i in usable if _submaker_language_matches(i.get('lang'), language)]
	other = [i for i in usable if i not in preferred]
	sort_key = lambda item: _score_subtitle_release_match(item, release_context or playback_release_context())
	preferred.sort(key=sort_key, reverse=True)
	other.sort(key=sort_key, reverse=True)
	return preferred + other

def _looks_like_subtitle_content(content):
	if not content: return False
	if not isinstance(content, str):
		try: content = content.decode('utf-8', 'ignore')
		except: return False
	sample = content.lstrip()[:256].lower()
	if sample.startswith('<!doctype') or sample.startswith('<html'): return False
	return bool(re.search(r'\d{1,2}:\d{2}:\d{2}', content))

def _download_submaker_content(download_fn, subs, language, release_context=None):
	for item in _submaker_ranked_subs(subs, language, release_context=release_context):
		response = download_fn(item.get('url'))
		if isinstance(response, str) or not getattr(response, 'ok', False): continue
		try: content = response.text
		except: content = response.content
		if _looks_like_subtitle_content(content): return content
	return None

def _get(url, stream=False, retry=False, quiet=False):
	response = requests.get(url, stream=stream, timeout=timeout)
	if retry and response.status_code in (403, 429):
		if not quiet:
			ku.notification('SubMaker rate limited. Retrying in 10 secs...', 3500)
		ku.sleep(10000)
		return _get(url, stream=stream, quiet=quiet)
	return response

def _normalize_stream_lang_code(code):
	if not code: return code
	if code == 'gre': return 'ell'
	return code

def _find_subtitle_stream_index(player, preferred_languages):
	try: streams = list(player.getAvailableSubtitleStreams() or [])
	except: return None
	if not streams: return None
	normalized = [_normalize_stream_lang_code(code) for code in streams]
	for pref in preferred_languages:
		for idx, code in enumerate(normalized):
			if _submaker_language_matches(code, pref): return idx
	return None

def _find_forced_subtitle_stream_index():
	props = _player_properties(['currentsubtitle', 'subtitles'])
	if not props: return None
	current = props.get('currentsubtitle') or {}
	if current.get('is_forced') and current.get('index') is not None:
		return int(current['index'])
	for item in props.get('subtitles') or []:
		if item.get('is_forced') and item.get('index') is not None:
			return int(item['index'])
	return None

def subtitle_notify_poster(meta, media_type='movie'):
	if not meta: return ku.get_icon('box_office')
	if media_type == 'episode':
		if st.avoid_episode_spoilers() and int(meta.get('playcount', 0) or 0) == 0:
			return meta.get('fanart') or meta.get('poster') or ku.addon_fanart()
		return meta.get('ep_thumb') or meta.get('fanart') or meta.get('poster') or ku.get_icon('box_office')
	return meta.get('poster') or ku.get_icon('box_office')

def _notify_subtitles_ready(poster=None, local=False, is_episode=False):
	for _ in range(40):
		if ku.get_visibility('Window.IsActive(fullscreenvideo)'): break
		ku.sleep(100)
	settle_ms = 500 if is_episode else 200
	message = 'Local subtitles found' if local else 'Downloaded subtitles found'
	ku.notification(message, icon=poster, settle_ms=settle_ms)

def _enable_forced_local_subtitles(player, poster=None, notify=True, is_episode=False):
	stream_index = _find_forced_subtitle_stream_index()
	if stream_index is None: return False
	try: player.setSubtitleStream(stream_index)
	except: return False
	if st.auto_enable_subs(): player.showSubtitles(True)
	if notify: _notify_subtitles_ready(poster=poster, local=True, is_episode=is_episode)
	return True

def enable_local_subtitles(player, poster=None, notify=True, is_episode=False):
	if st.subs_language_is_forced_local():
		return _enable_forced_local_subtitles(player, poster=poster, notify=notify, is_episode=is_episode)
	preferred_languages = st.subs_language_preferences()
	try: current = player.getSubtitles()
	except: current = ''
	if current:
		for pref in preferred_languages:
			if _submaker_language_matches(current, pref):
				if st.auto_enable_subs(): player.showSubtitles(True)
				if notify: _notify_subtitles_ready(poster=poster, local=True, is_episode=is_episode)
				return True
	stream_index = _find_subtitle_stream_index(player, preferred_languages)
	if stream_index is not None:
		try: player.setSubtitleStream(stream_index)
		except: pass
		if st.auto_enable_subs(): player.showSubtitles(True)
		if notify: _notify_subtitles_ready(poster=poster, local=True, is_episode=is_episode)
		return True
	return False

def _alert_sub_filename(imdb_id, season, episode):
	filename_lang = st.subs_language_for_download().replace(' ', '_')
	if season: sub_filename = 'RedLightSubs_%s_%s_%s' % (imdb_id, season, episode)
	else: sub_filename = 'RedLightSubs_%s' % imdb_id
	return sub_filename + '_%s.srt' % filename_lang

def _opensubs_alert_filename(imdb_id, season, episode):
	filename_lang = st.subs_language_for_download().replace(' ', '_')
	if season: sub_filename = 'RedLightOpenSubs_%s_%s_%s' % (imdb_id, season, episode)
	else: sub_filename = 'RedLightOpenSubs_%s' % imdb_id
	return sub_filename + '_%s.srt' % filename_lang

def _opensubs_alert_path(imdb_id, season, episode):
	return '%s%s' % ('special://temp/', _opensubs_alert_filename(imdb_id, season, episode))

def _looks_like_subtitle_path(value):
	if not value or value.strip() in ('(External)',): return False
	lower = value.lower().strip()
	if any(lower.endswith(ext) for ext in _SUB_EXTS): return True
	if lower.startswith('special://'): return True
	if '://' in lower: return any(ext in lower for ext in _SUB_EXTS)
	if os.path.sep in value or value.startswith('/') or (len(value) > 2 and value[1] == ':'):
		return any(lower.endswith(ext) for ext in _SUB_EXTS)
	return False

def _dedupe_paths(paths):
	seen, results = set(), []
	for path in paths:
		if not path: continue
		try: key = os.path.normcase(os.path.normpath(ku.translate_path(path) if path.startswith('special://') else path))
		except: key = path
		if key in seen: continue
		seen.add(key)
		results.append(path)
	return results

def _player_properties(properties):
	players = ku.get_jsonrpc({'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetActivePlayers', 'params': {}})
	if not players: return None
	player_id = players[0]['playerid']
	return ku.get_jsonrpc({'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetProperties',
		'params': {'playerid': player_id, 'properties': properties}})

def _subs_enabled(player=None):
	try:
		if player is None: player = xbmc.Player()
		if player.getSubtitles(): return True
	except: pass
	try:
		props = _player_properties(['subtitleenabled'])
		return bool(props and props.get('subtitleenabled'))
	except: pass
	return False

def _active_subtitle_paths_from_player():
	props = _player_properties(['subtitleenabled', 'currentsubtitle', 'subtitles'])
	if not props or not props.get('subtitleenabled'): return []
	paths, current = [], props.get('currentsubtitle') or {}
	current_index = current.get('index')
	for item in props.get('subtitles') or []:
		if current_index is not None and item.get('index') != current_index: continue
		for key in ('filename', 'path', 'name'):
			val = (item.get(key) or '').strip()
			if _looks_like_subtitle_path(val): paths.append(val)
	for key in ('filename', 'path', 'name'):
		val = (current.get(key) or '').strip()
		if _looks_like_subtitle_path(val): paths.append(val)
	return _dedupe_paths(paths)

def _addon_temp_subtitle_dirs():
	return (
		'special://temp/',
		'special://profile/addon_data/service.subtitles.a4ksubtitles/temp/',
	)

def _recent_subtitles_in_dir(directory, since_ts=None):
	try:
		native = ku.translate_path(directory.rstrip('/') + '/')
		if not os.path.isdir(native): return []
		found = []
		for name in os.listdir(native):
			lower = name.lower()
			if lower == 'sub.zip' or lower.endswith('.translated'): continue
			if not lower.endswith(_SUB_EXTS): continue
			full = os.path.join(native, name)
			if since_ts and os.path.getmtime(full) < (since_ts - 5): continue
			found.append((os.path.getmtime(full), full))
		found.sort(reverse=True)
		return [path for _, path in found]
	except: return []

def _sidecar_subtitle_paths(playing_filename=None, playing_url=None):
	paths = []
	for raw in (playing_url, playing_filename):
		if not raw: continue
		base_url = raw.split('|')[0].split('?')[0]
		if base_url.startswith(('http://', 'https://', 'plugin://')): continue
		translated = ku.translate_path(base_url) if base_url.startswith('special://') else base_url
		if not os.path.isfile(translated): continue
		folder, stem = os.path.dirname(translated), os.path.splitext(os.path.basename(translated))[0]
		try:
			for name in os.listdir(folder):
				lower = name.lower()
				if not lower.endswith(_SUB_EXTS): continue
				if lower.startswith(stem.lower()) or stem.lower() in lower:
					paths.append(os.path.join(folder, name))
		except: pass
	return _dedupe_paths(paths)

def _alert_temp_paths(imdb_id, season, episode):
	if not imdb_id: return []
	paths = []
	if st.submaker_manifest_configured():
		paths.append('%s%s' % ('special://temp/', _alert_sub_filename(imdb_id, season, episode)))
	if st.opensubs_configured():
		paths.append('%s%s' % ('special://temp/', _opensubs_alert_filename(imdb_id, season, episode)))
	return paths

def _collect_subtitle_candidates(player, playing_filename, imdb_id, season, episode, playback_started_at=None):
	paths, seen = [], set()
	def add(path, require_episode_match=False):
		if not path: return
		if require_episode_match and not _subtitle_path_matches_episode(path, imdb_id, season, episode):
			return
		try: key = os.path.normcase(os.path.normpath(ku.translate_path(path) if path.startswith('special://') else path))
		except: key = path
		if key in seen: return
		seen.add(key)
		paths.append(path)
	for path in _alert_temp_paths(imdb_id, season, episode): add(path)
	for path in _active_subtitle_paths_from_player(): add(path, require_episode_match=True)
	active_prop = ku.get_property(_ACTIVE_SUB_PROP)
	if active_prop: add(active_prop, require_episode_match=True)
	if playback_started_at:
		for directory in _addon_temp_subtitle_dirs():
			for path in _recent_subtitles_in_dir(directory, playback_started_at):
				add(path, require_episode_match=True)
	try: playing_url = player.getPlayingFile() if player else None
	except: playing_url = None
	for path in _sidecar_subtitle_paths(playing_filename, playing_url): add(path)
	return paths

def _time_part_to_seconds(part):
	part = part.replace(',', '.')
	chunks = part.split(':')
	if len(chunks) == 3:
		h, m, s = chunks
		return int(h) * 3600 + int(m) * 60 + float(s)
	if len(chunks) == 2:
		m, s = chunks
		return int(m) * 60 + float(s)
	return float(part)

def _subtitle_last_end_seconds(content):
	times = re.findall(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})', content)
	if not times:
		times = [(h, m, s, '000') for h, m, s in re.findall(r'(\d{2}):(\d{2}):(\d{2})', content)]
	end_seconds = 0.0
	for h, m, s, ms in times:
		end_seconds = max(end_seconds, int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0)
	if end_seconds > 0: return end_seconds
	ass_times = re.findall(r'Dialogue:\s*\d+,(\d+:\d+:\d+[\.,]\d+),(\d+:\d+:\d+[\.,]\d+)', content, re.I)
	for _, end in ass_times:
		end_seconds = max(end_seconds, _time_part_to_seconds(end))
	return end_seconds if end_seconds > 0 else None

_SUBS_CREDITS_JUNK_RE = re.compile(
	r'addic7ed|opensubtitles|subscene|sub\s*toolbox|sync\s*&|corrections?\s*by|translated\s*by|subtitle\s*team|www\.|http',
	re.I)
_SUBS_CREDITS_ROLL_RE = re.compile(
	r'^(cast|crew|credits|starring|directed by|written by|created by|developed by|executive producers?|producers?|music by)\b',
	re.I)

def _subtitle_path_matches_episode(path, imdb_id, season, episode):
	if not path or season is None or episode is None:
		return True
	try:
		name = os.path.basename(ku.translate_path(path) if str(path).startswith('special://') else path).lower()
	except:
		name = os.path.basename(str(path)).lower()
	if 'redlightsubs_' not in name and 'redlightopensubs_' not in name:
		return True
	imdb = str(imdb_id or '').lower()
	if imdb and imdb not in name:
		return False
	s, e = int(season), int(episode)
	return ('_%s_%s_' % (s, e)) in name or ('_%s_%s.' % (s, e)) in name

def _subtitle_cue_text_is_junk(text):
	text = re.sub(r'<[^>]+>', '', text or '').strip()
	if not text: return True
	if _SUBS_CREDITS_JUNK_RE.search(text): return True
	if re.search(r'[♪♫]', text): return True
	if re.search(r'\b(music|instrumental|orchestral)\b', text, re.I): return True
	if len(text) < 80 and _SUBS_CREDITS_ROLL_RE.search(text): return True
	if re.fullmatch(r'[\s♪♫\(\)\[\]\-\*\.!]+', text): return True
	if re.fullmatch(r'\([^)]+\)', text): return True
	if re.fullmatch(r'\[[^\]]+\]', text): return True
	return False

def _subtitle_cue_end_from_time_line(line):
	match = re.search(r'-->\s*(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})', line)
	if not match: return None
	h, m, s, ms = match.groups()
	return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

def _subtitle_cue_start_from_time_line(line):
	match = re.search(r'(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})\s*-->', line)
	if not match: return None
	h, m, s, ms = match.groups()
	return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

def _subtitle_first_junk_start_after(content, min_start_seconds):
	first_start = None
	for block in re.split(r'\n\s*\n', content.strip()):
		lines = [line.strip() for line in block.splitlines() if line.strip()]
		if len(lines) < 2: continue
		start_seconds = None
		text_lines = []
		for line in lines:
			if '-->' in line:
				start_seconds = _subtitle_cue_start_from_time_line(line)
			elif not line.isdigit():
				text_lines.append(line)
		if start_seconds is None or start_seconds < min_start_seconds: continue
		if not _subtitle_cue_text_is_junk(' '.join(text_lines)): continue
		if first_start is None or start_seconds < first_start:
			first_start = start_seconds
	return first_start

def _subtitle_credits_entry_remaining_seconds(total_time, content):
	dialogue_end = _subtitle_last_dialogue_end_seconds(content)
	if dialogue_end is None:
		end_seconds = _subtitle_last_end_seconds(content)
		if end_seconds is None: return None
		return _bounded_alert_remaining(float(total_time) - float(end_seconds))
	gap = float(total_time) - float(dialogue_end)
	return _bounded_alert_remaining(gap)

def _subtitle_alert_remaining_seconds(total_time, content):
	dialogue_end = _subtitle_last_dialogue_end_seconds(content)
	if dialogue_end is None:
		end_seconds = _subtitle_last_end_seconds(content)
		if end_seconds is None: return None
		return _alert_remaining_from_last_cue(total_time, end_seconds)
	gap = float(total_time) - float(dialogue_end)
	if gap < _SUBS_UNSUBTITLED_TAIL_SEC:
		return _bounded_alert_remaining(gap)
	junk_start = _subtitle_first_junk_start_after(content, float(dialogue_end))
	if junk_start is not None:
		remaining = float(total_time) - junk_start
		if remaining < 0 or remaining > _ALERT_SUB_MAX_REMAINING: return None
		return int(remaining)
	return _bounded_alert_remaining(gap)

def _bounded_alert_remaining(remaining):
	try: remaining = float(remaining)
	except: return None
	if remaining < 0 or remaining > _ALERT_SUB_MAX_REMAINING: return None
	return int(remaining)

def _subtitle_last_dialogue_end_seconds(content):
	blocks = re.split(r'\n\s*\n', content.strip())
	for block in reversed(blocks):
		lines = [line.strip() for line in block.splitlines() if line.strip()]
		if len(lines) < 2: continue
		end_seconds = None
		text_lines = []
		for line in lines:
			if '-->' in line:
				end_seconds = _subtitle_cue_end_from_time_line(line)
			elif not line.isdigit():
				text_lines.append(line)
		if end_seconds is None: continue
		if not _subtitle_cue_text_is_junk(' '.join(text_lines)): return end_seconds
	return None

def _subs_alert_remaining_before_eof(remaining):
	try: remaining = float(remaining)
	except: return None
	if remaining < 0 or remaining > _ALERT_SUB_MAX_REMAINING: return None
	if remaining >= _SUBS_UNSUBTITLED_TAIL_SEC:
		remaining = _SUBS_PRE_CREDITS_REMAINING_SEC
	return int(remaining)

def _raw_remaining_from_last_cue(total_time, last_cue_end):
	remaining = float(total_time) - float(last_cue_end)
	if remaining < 0 or remaining > _ALERT_SUB_MAX_REMAINING: return None
	return int(remaining)

def _alert_remaining_from_last_cue(total_time, last_cue_end):
	return _subs_alert_remaining_before_eof(float(total_time) - float(last_cue_end))

def _seconds_remaining_before_end(sub_path, total_time, for_alert=False, credits_entry=False):
	try:
		with ku.open_file(sub_path) as file: content = file.read()
		if not _looks_like_subtitle_content(content): return None
		if credits_entry:
			remaining = _subtitle_credits_entry_remaining_seconds(total_time, content)
			if remaining is not None: return remaining
			return None
		if for_alert:
			remaining = _subtitle_alert_remaining_seconds(total_time, content)
			if remaining is not None: return remaining
		end_seconds = _subtitle_last_end_seconds(content)
		if end_seconds is None: return None
		if for_alert: return _alert_remaining_from_last_cue(total_time, end_seconds)
		return _raw_remaining_from_last_cue(total_time, end_seconds)
	except: return None

def fetch_subtitle_for_alert_timing(imdb_id, season=None, episode=None, year=None, playing_filename=None, playing_item=None):
	if not st.subs_alert_fetch_configured(): return None
	for fetcher in _subs_alert_fetch_order():
		path = fetcher(imdb_id, season, episode, year, playing_filename, playing_item)
		if path: return path
	return None

def _subs_alert_fetch_order():
	return [_fetch_submaker_alert_subtitle, _fetch_opensubs_alert_subtitle]

def _fetch_submaker_alert_subtitle(imdb_id, season, episode, year=None, playing_filename=None, playing_item=None):
	if not st.submaker_manifest_configured(): return None
	search_filename = _alert_sub_filename(imdb_id, season, episode)
	final_path = '%s%s' % ('special://temp/', search_filename)
	if season: params = 'subtitles/series/%s:%s:%s' % (imdb_id, season, episode)
	else: params = 'subtitles/movie/%s' % imdb_id
	try: response = _get(_submaker_api_url(st.submaker_manifest(), params), retry=True, quiet=True)
	except requests.RequestException: return None
	if not response.ok: return None
	subs = response.json().get('subtitles', [])
	release_context = playback_release_context(playing_filename, playing_item)
	content = _download_submaker_content(lambda url: _get(url, stream=True, retry=True, quiet=True), subs, st.subs_language_for_download(), release_context=release_context)
	if not content: return None
	try:
		with ku.open_file(final_path, 'w') as file: file.write(content)
		ku.set_property(_ACTIVE_SUB_PROP, final_path)
	except: return None
	return final_path

def _fetch_opensubs_alert_subtitle(imdb_id, season, episode, year=None, playing_filename=None, playing_item=None):
	try:
		from apis.opensubs_api import fetch_alert_subtitle
		return fetch_alert_subtitle(imdb_id, season, episode, year, playing_filename, playing_item)
	except: return None

def _fetch_alert_subtitle(imdb_id, season, episode):
	return _fetch_submaker_alert_subtitle(imdb_id, season, episode)

def subtitle_seconds_remaining_before_end(total_time, imdb_id, season=None, episode=None, fetch=False, player=None,
		playing_filename=None, playing_item=None, playback_started_at=None, year=None, for_alert=False, credits_entry=False, quiet=False):
	if not total_time: return None
	log_label = 'Subtitle credits entry' if credits_entry else 'Subtitle alert timing'
	for sub_path in _collect_subtitle_candidates(player, playing_filename, imdb_id, season, episode, playback_started_at):
		remaining = _seconds_remaining_before_end(sub_path, total_time, for_alert=for_alert, credits_entry=credits_entry)
		if remaining is not None:
			if not quiet:
				try: label = os.path.basename(ku.translate_path(sub_path) if sub_path.startswith('special://') else sub_path)
				except: label = sub_path or 'unknown'
				ku.logger('Red Light', '%s (local): %s remaining=%ss' % (log_label, label, remaining))
			return remaining
	if not fetch or not imdb_id or not st.subs_alert_fetch_configured(): return None
	fetched = fetch_subtitle_for_alert_timing(imdb_id, season, episode, year, playing_filename, playing_item)
	if not fetched: return None
	remaining = _seconds_remaining_before_end(fetched, total_time, for_alert=for_alert, credits_entry=credits_entry)
	if remaining is not None and not quiet:
		try: label = os.path.basename(ku.translate_path(fetched) if fetched.startswith('special://') else fetched)
		except: label = fetched or 'unknown'
		ku.logger('Red Light', '%s (fetched): %s remaining=%ss' % (log_label, label, remaining))
	return remaining

def remember_active_subtitle_path(path):
	if path: ku.set_property(_ACTIVE_SUB_PROP, path)

def clear_active_subtitle_path():
	ku.clear_property(_ACTIVE_SUB_PROP)

def clear_subtitles_cache():
	temp_path = ku.translate_path('special://temp/')
	removed = 0
	if os.path.isdir(temp_path):
		for name in os.listdir(temp_path):
			if name.startswith('RedLightSubs_') or name.startswith('RedLightOpenSubs_'):
				try:
					os.remove(os.path.join(temp_path, name))
					removed += 1
				except: pass
	clear_active_subtitle_path()
	return removed

def _apply_external_subtitle(player, path, poster=None, notify=True, is_episode=False):
	if not path: return False
	try: player.setSubtitles(path)
	except: return False
	if st.auto_enable_subs():
		try: player.showSubtitles(True)
		except: pass
	if notify: _notify_subtitles_ready(poster=poster, local=False, is_episode=is_episode)
	return True

class Subtitles(xbmc.Player):
	def subtitles_download(self, url):
		response = _get(url, stream=True, retry=True)
		return response if response.ok else response.reason

	def subtitles_search(self):
		if self.season: params = 'subtitles/series/%s:%s:%s' % (self.imdb_id, self.season, self.episode)
		else: params = 'subtitles/movie/%s' % self.imdb_id
		try: response = _get(_submaker_api_url(self.manifest, params), retry=True)
		except requests.RequestException as e: return str(e)
		return response.json().get('subtitles', []) if response.ok else response.reason

	def _video_file_subs(self):
		return enable_local_subtitles(self._player, poster=self.poster, is_episode=self.is_episode)

	def _downloaded_subs(self):
		files = ku.list_dirs(self.subtitle_path)[1]
		final_match = next((i for i in files if i == self.search_filename), None)
		if not final_match: return False
		subtitle = '%s%s' % (self.subtitle_path, final_match)
		try:
			with ku.open_file(subtitle) as file: content = file.read()
			if not _looks_like_subtitle_content(content): return False
		except: return False
		return subtitle

	def _searched_subs(self):
		subs = self.subtitles_search()
		if isinstance(subs, str):
			return ku.notification('SubMaker error: %s' % subs, settle_ms=150)
		if not subs:
			return ku.notification('No subtitles found', icon=self.poster, settle_ms=150)
		release_context = playback_release_context(self.playing_filename, self.playing_item)
		content = _download_submaker_content(self.subtitles_download, subs, self.language, release_context=release_context)
		if not content:
			return ku.notification('No subtitles found', icon=self.poster, settle_ms=150)
		final_path = '%s%s' % (self.subtitle_path, self.search_filename)
		with ku.open_file(final_path, 'w') as file: file.write(content)
		ku.sleep(1000)
		return final_path

	def run(self, imdb_id, season, episode, poster, playing_filename=None, playing_item=None, active_player=None):
		self.manifest = st.submaker_manifest()
		if not self.manifest or 'manifest' not in self.manifest: return
		self.imdb_id, self.season, self.episode, self.poster = imdb_id, season, episode, poster
		self.playing_filename, self.playing_item = playing_filename, playing_item
		self.is_episode = season is not None and episode is not None
		self._player = active_player or self
		self.language = st.subs_language_for_download()
		filename_lang = self.language.replace(' ', '_')
		self.subtitle_path = 'special://temp/'
		if season: self.sub_filename = 'RedLightSubs_%s_%s_%s' % (self.imdb_id, self.season, self.episode)
		else: self.sub_filename = 'RedLightSubs_%s' % self.imdb_id
		self.search_filename = self.sub_filename + '_%s.srt' % filename_lang
		ku.sleep(2500)
		if st.submaker_prefer_local():
			subtitle = self._video_file_subs()
			if subtitle: return
		subtitle = self._downloaded_subs()
		if subtitle:
			remember_active_subtitle_path(subtitle)
			return _apply_external_subtitle(self._player, subtitle, poster=self.poster, is_episode=self.is_episode)
		subtitle = self._searched_subs()
		if subtitle:
			remember_active_subtitle_path(subtitle)
			return _apply_external_subtitle(self._player, subtitle, poster=self.poster, is_episode=self.is_episode)

class OpenSubtitlesSubs(xbmc.Player):
	def _video_file_subs(self):
		return enable_local_subtitles(self._player, poster=self.poster, is_episode=self.is_episode)

	def run(self, imdb_id, season, episode, poster, year=None, playing_filename=None, playing_item=None, active_player=None):
		self.poster = poster
		self.playing_filename, self.playing_item = playing_filename, playing_item
		self.is_episode = season is not None and episode is not None
		self._player = active_player or self
		ku.sleep(2500)
		if st.submaker_prefer_local():
			if self._video_file_subs(): return
		if not st.opensubs_configured():
			return ku.notification('OpenSubtitles username and password required', icon=poster, settle_ms=500 if self.is_episode else 200)
		try:
			from apis.opensubs_api import fetch_alert_subtitle
			path = fetch_alert_subtitle(imdb_id, season, episode, year, playing_filename, playing_item)
		except: path = None
		if not path:
			return ku.notification('No subtitles found', icon=poster, settle_ms=500 if self.is_episode else 200)
		remember_active_subtitle_path(path)
		return _apply_external_subtitle(self._player, path, poster=poster, is_episode=self.is_episode)
