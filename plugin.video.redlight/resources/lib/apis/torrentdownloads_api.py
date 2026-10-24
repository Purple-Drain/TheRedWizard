# -*- coding: utf-8 -*-
import re
from html import unescape
from urllib.parse import quote, quote_plus, unquote_plus
from caches.main_cache import main_cache
from modules.kodi_utils import make_session, logger
from modules.native_torrents import USER_AGENT, bytes_to_gb, instance_base_url, normalize_info_hash, parse_size_gb

TORRENTDOWNLOADS_URLS = (
	'https://www.torrentdownloads.pro',
)
TORRENTDOWNLOAD_INFO = 'https://www.torrentdownload.info'
_ITEM = re.compile(r'<item>(.*?)</item>', re.I | re.S)
_TITLE = re.compile(r'<title>(.*?)</title>', re.I | re.S)
_HASH = re.compile(r'<info_hash>(.*?)</info_hash>', re.I | re.S)
_SIZE = re.compile(r'<size>(.*?)</size>', re.I | re.S)
_SEEDERS = re.compile(r'<seeders>(.*?)</seeders>', re.I | re.S)
_CDATA = re.compile(r'<!\[CDATA\[(.*?)\]\]>', re.S)
_TR = re.compile(r'<tr.*?>(.*?)</tr>', re.I | re.S)
_TD = re.compile(r'<td.*?>(.+?)</td>', re.I | re.S)
_HREF = re.compile(r'''href\s*=\s*["']/([^"']+)["']''', re.I)
_TAG = re.compile(r'<[^>]+>')
_session = None


def _http():
	global _session
	if _session is None:
		_session = make_session('https://')
		_session.headers.update({'User-Agent': USER_AGENT, 'Accept': 'application/rss+xml, application/xml, text/xml, text/html, */*'})
	return _session


def torrentdownloads_base_url():
	return instance_base_url('torrentdownloads.url', TORRENTDOWNLOADS_URLS, 'torrentdownloads.custom_url', 1) or TORRENTDOWNLOADS_URLS[0]


def clear_torrentdownloads_cache():
	try:
		main_cache.delete_like('TORRENTDOWNLOADS_%')
		return True
	except Exception:
		return False


def _text(match):
	if not match:
		return ''
	value = match.group(1).strip()
	cdata = _CDATA.search(value)
	if cdata:
		value = cdata.group(1).strip()
	return unescape(value.replace('&amp;', '&'))


def _size(value):
	text = (value or '').strip()
	if text.isdigit():
		return bytes_to_gb(text)
	return parse_size_gb(text)


def _merge_rows(*groups):
	by_hash = {}
	for group in groups:
		for item in group or []:
			info_hash = item.get('hash')
			if not info_hash:
				continue
			old = by_hash.get(info_hash)
			if not old or (item.get('size') or 0) > (old.get('size') or 0):
				by_hash[info_hash] = item
	return list(by_hash.values())


def _parse_items(xml_text):
	# Per <item> block. Hash is last in each item; do not scan hash-then-title across items.
	results, seen = [], set()
	for block in _ITEM.findall(xml_text or ''):
		info_hash = normalize_info_hash(_text(_HASH.search(block)))
		name = _text(_TITLE.search(block))
		if not info_hash or not name or info_hash in seen:
			continue
		seen.add(info_hash)
		try:
			seeders = int(_text(_SEEDERS.search(block)) or 0)
		except Exception:
			seeders = 0
		results.append({
			'hash': info_hash,
			'name': name,
			'size': _size(_text(_SIZE.search(block))),
			'seeders': seeders,
		})
	return results


def _parse_info_rows(html_text):
	results, seen = [], set()
	for row in _TR.findall(html_text or ''):
		low = row.lower()
		if '<th' in low or 'nofollow' in low:
			continue
		cols = _TD.findall(row)
		if len(cols) < 3:
			continue
		href = _HREF.search(cols[0])
		if not href:
			continue
		parts = href.group(1).split('/')
		if len(parts) < 2:
			continue
		info_hash = normalize_info_hash(parts[0])
		name = unescape(unquote_plus(parts[1]).replace('&amp;', '&')).replace('-', ' ').replace('_', ' ').strip()
		if not info_hash or not name or info_hash in seen:
			continue
		seen.add(info_hash)
		try:
			seeders = int(re.sub(r'[^\d]', '', _TAG.sub('', cols[3] if len(cols) > 3 else '') or '0') or 0)
		except Exception:
			seeders = 0
		results.append({
			'hash': info_hash,
			'name': name,
			'size': parse_size_gb(_TAG.sub('', cols[2])),
			'seeders': seeders,
		})
	return results


def _cached_get(url, timeout, expiration, parser, log_query):
	cache_key = 'TORRENTDOWNLOADS_%s' % url
	cached = main_cache.get(cache_key)
	if cached is not None:
		return cached
	try:
		response = _http().get(url, timeout=max(1, int(timeout)))
		response.raise_for_status()
		results = parser(response.text)
	except Exception as e:
		logger('torrentdownloads api', '%s (%s)' % (type(e).__name__, log_query))
		return []
	main_cache.set(cache_key, results, expiration=expiration)
	return results


def _search_rss(query, timeout, expiration, category):
	base = torrentdownloads_base_url().rstrip('/')
	if not base:
		return []
	cid = '8' if str(category) == '8' else '4'
	url = '%s/rss.xml?new=1&type=search&cid=%s&search=%s' % (base, cid, quote(query))
	return _cached_get(url, timeout, expiration, _parse_items, query)


def _search_info(query, timeout, expiration):
	results = []
	encoded = quote_plus(query)
	for page in (1, 2):
		url = '%s/search?q=%s' % (TORRENTDOWNLOAD_INFO, encoded)
		if page == 2:
			url = '%s&p=2' % url
		results = _merge_rows(results, _cached_get(url, timeout, expiration, _parse_info_rows, query))
	return results


def search(query, timeout=10, expiration=24, category='4', include_info=True):
	query = (query or '').strip()
	if not query:
		return []
	rss = _search_rss(query, timeout, expiration, category)
	if not include_info:
		return rss
	return _merge_rows(rss, _search_info(query, timeout, expiration))
