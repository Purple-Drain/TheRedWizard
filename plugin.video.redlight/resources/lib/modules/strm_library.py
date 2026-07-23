# -*- coding: utf-8 -*-
# Native port of kodi-strm-pipeline's scripts/update_library.py: walks the
# TorBox / Debrid-Link / Real-Debrid WebDAV endpoints (kept separate from
# Red Light's OAuth debrid accounts on purpose -- see WHY WEBDAV below) and
# reconciles a browseable .strm library under a settings-configured root.
#
# WHY WEBDAV, NOT RealDebrid/TorBox/DebridLink's native cloud APIs: those
# APIs need a per-file unrestrict/requestdl call to mint a playable link
# (RD: user_cloud_info() then unrestrict_link(); TorBox: requestdl(), with
# up to 12 retries), and those links are short-lived -- baking one into a
# .strm meant to be replayed for weeks would go stale. A WebDAV PROPFIND
# returns a stable, long-lived davs:// URL with credentials embedded, for
# every file in the tree, in one walk. Full account walk is what this
# needs (thousands of files, hourly), so WebDAV is strictly the right tool
# here even though the native APIs are better suited to on-demand resolve
# (which is exactly what the rest of Red Light already uses them for).
# Hash-list export (see export_hashlist) is the inverse case -- it wants a
# hash, not a playable link -- so it goes through the native mylist APIs
# instead, which hand back a hash for free with no unrestrict call at all.
import base64
import json
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, quote, unquote
from urllib.request import Request, urlopen

import xbmcgui
from caches.settings_cache import get_setting, set_setting
from modules.kodi_utils import notification, kodi_dialog, ok_dialog, logger, addon_profile
from modules.source_utils import supported_video_extensions

# ---------------------------------------------------------------------------
# Settings (get_setting/set_setting keyed storage -- Red Light's settings.xml
# is a single button that opens a skin-driven settings window, not a plain
# Kodi settings dialog, so new fields are exposed via context-menu actions
# below (configure_webdav / toggle_enabled) rather than a settings.xml entry)
# ---------------------------------------------------------------------------

_ENABLED_KEY = 'redlight.library_sync.enabled'
_INTERVAL_KEY = 'redlight.library_sync.interval_minutes'
_ROOT_KEY = 'redlight.library_sync.root'
_PROVIDER_KEYS = ('rd', 'tb', 'dl')
_WEBDAV_LABELS = {'rd': 'Real-Debrid', 'tb': 'TorBox', 'dl': 'Debrid-Link'}


def enabled():
	return get_setting(_ENABLED_KEY, 'false') == 'true'


def sync_interval():
	"""(minutes, seconds) -- mirrors modules.settings.trakt_sync_interval's shape."""
	try: minutes = max(15, int(get_setting(_INTERVAL_KEY, '60')))
	except: minutes = 60
	return minutes, minutes * 60


def _default_root():
	# Same reasoning as kodi-strm-pipeline's update_library.py: the library
	# lives OUTSIDE the app sandbox on Android so a Red Light reinstall
	# doesn't wipe it. Off-Shield (desktop testing) falls back to the addon
	# profile dir.
	shield_userdata = '/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/userdata'
	if os.path.isdir(shield_userdata):
		return '/storage/emulated/0/DebridLibrary'
	return os.path.join(addon_profile(), 'strm_library')


def library_root():
	return get_setting(_ROOT_KEY, '') or _default_root()


def _webdav_key(provider, field):
	return 'redlight.library_sync.webdav.%s.%s' % (provider, field)


def webdav_providers():
	"""[{'name', 'host', 'user', 'pass'}, ...] for every provider with all
	three WebDAV fields filled in via configure_webdav()."""
	providers = []
	for key in _PROVIDER_KEYS:
		host = get_setting(_webdav_key(key, 'host'), '')
		user = get_setting(_webdav_key(key, 'user'), '')
		password = get_setting(_webdav_key(key, 'pass'), '')
		if host and user and password:
			providers.append({'name': _WEBDAV_LABELS[key], 'host': host, 'user': user, 'pass': password})
	return providers


# ---------------------------------------------------------------------------
# WebDAV plumbing -- ported verbatim from kodi-strm-pipeline's update_library.py
# (itself the proven update_wc.py implementation). MAX_WORKERS kept modest on
# purpose: providers rate-limit, and the Shield shares this link with playback.
# ---------------------------------------------------------------------------

MAX_DEPTH = 4
VIDEO_EXT = tuple(e.lower() for e in supported_video_extensions())
MAX_WORKERS = 8


def norm(text):
	return re.sub(r"[._\-\s]+", " ", unquote(text)).lower()


def _propfind(auth_header, host, path):
	url = "https://%s%s" % (host, quote(path))
	req = Request(url, method="PROPFIND",
	              headers={"Depth": "1", "Authorization": auth_header,
	                       "User-Agent": "RedLight-LibrarySync/1.0"})
	with urlopen(req, timeout=15) as resp:
		body = resp.read()
	dirs, files = [], []
	root = ET.fromstring(body)
	for r in root.findall(".//{DAV:}response"):
		href_el = r.find("{DAV:}href")
		if href_el is None or not href_el.text:
			continue
		href_path = unquote(urlparse(href_el.text).path)
		if href_path.rstrip("/") == path.rstrip("/"):
			continue
		is_dir = href_path.endswith("/") or \
			r.find(".//{DAV:}resourcetype/{DAV:}collection") is not None
		if is_dir:
			dirs.append(href_path)
		else:
			lm_el = r.find(".//{DAV:}getlastmodified")
			try:
				lastmod = parsedate_to_datetime(lm_el.text).timestamp()
			except (AttributeError, TypeError, ValueError):
				lastmod = None
			files.append((href_path, lastmod))
	return dirs, files


def _propfind_safe(auth_header, host, path):
	try:
		return _propfind(auth_header, host, path)
	except Exception as exc:
		logger('Library Sync', '%s%s: %s' % (host, path, exc))
		return [], []


def walk_parallel(auth_header, host, root="/"):
	"""[(file_path, lastmod), ...] for the whole tree under root. The ROOT
	PROPFIND is deliberately allowed to raise: that marks the provider
	unreachable, which is what the last-good-tree guard in run_sync() keys
	off. Failures below the root are swallowed (partial result)."""
	dirs, files = _propfind(auth_header, host, root)
	out = list(files)
	frontier, depth = dirs, 1
	while frontier and depth <= MAX_DEPTH:
		with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
			results = list(pool.map(lambda d: _propfind_safe(auth_header, host, d), frontier))
		next_frontier = []
		for sub_dirs, sub_files in results:
			out.extend(sub_files)
			next_frontier.extend(sub_dirs)
		frontier, depth = next_frontier, depth + 1
	return [(f, lm) for f, lm in out if f.lower().endswith(VIDEO_EXT)]


def sanitise(name):
	name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(".")
	return name or "unnamed"


def _strm_path(directory, name):
	return os.path.normpath(os.path.join(directory, sanitise(name) + ".strm"))


def _reconcile(root, desired):
	"""Delta-reconcile the .strm tree under root to match desired
	{path: (url, ts)}. See update_library.py's _reconcile docstring for the
	full rationale (mtime stability, foreign plugin:// strm survival)."""
	written = deleted = 0
	for path, (url, ts) in desired.items():
		body = url
		try:
			with open(path, encoding="utf-8") as fh:
				if fh.read() == body:
					continue
		except OSError:
			pass
		os.makedirs(os.path.dirname(path), exist_ok=True)
		with open(path, "w", encoding="utf-8") as fh:
			fh.write(body)
		if ts:
			os.utime(path, (ts, ts))
		written += 1
	if os.path.isdir(root):
		for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
			for f in filenames:
				if f.endswith(".strm"):
					fp = os.path.normpath(os.path.join(dirpath, f))
					if fp not in desired:
						try:
							with open(fp, encoding="utf-8") as fh:
								if fh.read(16).startswith("plugin://"):
									continue
						except OSError:
							pass
						os.remove(fp)
						deleted += 1
			if dirpath != root and not os.listdir(dirpath):
				os.rmdir(dirpath)
	return written, deleted


# ---------------------------------------------------------------------------
# TV / movie / football classification -- ported verbatim from
# kodi-strm-pipeline's update_library.py. See that file for the full
# rationale behind each rule (measured against 3392 live account files).
# ---------------------------------------------------------------------------

_EP_RE = re.compile(r"\bs(\d{1,2})\s?e(\d{1,3})(?:\s?e\d{1,3})*\b")
_EPX_RE = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")
_EP_SSEE_RE = re.compile(r"\bs(\d{2})(\d{2})\b")
_TRAILING_YEAR_RE = re.compile(r"\(?(?:19|20)\d{2}\)?\s*$")

_PACK_NOISE_RE = re.compile(
	r"\b(season|seasons|complete|series|s\d{1,2}(\s*-\s*s?\d{1,2})?|"
	r"\d{3,4}p|bluray|blu ray|web dl|webrip|web rip|hdtv|x26[45]|h\.?26[45]|hevc|"
	r"remux|aac|dd5|ddp5|10bit|dvdrip|amzn|nf|dsnp|repack|proper)\b.*$")

_MARKER_ANY_RE = re.compile(
	r"\bs\d{1,2}\s?e\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|\bs\d{2}\d{2}\b|\bs\d{1,2}\b")


def _clean_title(text, strip_year=True):
	t = text.strip()
	if strip_year:
		t = _TRAILING_YEAR_RE.sub("", t).strip()
	t = re.sub(r"['’]", "", t)
	return re.sub(r"[^\w]+", " ", t, flags=re.UNICODE).strip()


_CONTAINER_FOLDERS = frozenset({
	"links", "torrents", "downloader", "seedbox", "downloads", "files", "media",
})


def show_from_folder(folder):
	n = norm(folder or "")
	n = re.sub(r"\((?:19|20)\d{2}\)", " ", n)
	m = _MARKER_ANY_RE.search(n)
	if m:
		n = n[:m.start()]
	n = _PACK_NOISE_RE.sub(" ", n)
	n = _clean_title(n)
	if not n or n in _CONTAINER_FOLDERS:
		return None
	return n


_SHOW_ALIASES = {
	"iasip": "its always sunny in philadelphia",
}


def parse_episode(stem, folder=None):
	n = norm(stem)
	m = _EP_RE.search(n) or _EPX_RE.search(n) or _EP_SSEE_RE.search(n)
	if not m:
		return None
	season, episode = int(m.group(1)), int(m.group(2))
	show = _clean_title(n[:m.start()])
	if not show:
		show = show_from_folder(folder)
	if not show:
		return None
	show = _SHOW_ALIASES.get(show, show)
	return (show, season, episode)


_SPORT_TERMS = (
	"world cup", "fifa", "uefa", "premier league", "champions league",
	"europa league", "qualifier", "matchday", "group stage", "round of 32",
	"round of 16", "quarter final", "semi final", "third place", "friendly",
)

_FOOTBALL_NATIONS = frozenset({
	"argentina", "australia", "austria", "belgium", "brazil", "cameroon", "canada",
	"chile", "colombia", "costa rica", "croatia", "czechia", "denmark", "dr congo",
	"ecuador", "egypt", "england", "france", "germany", "ghana", "greece", "haiti",
	"iran", "italy", "ivory coast", "japan", "jordan", "korea", "mexico", "morocco",
	"netherlands", "new zealand", "nigeria", "norway", "panama", "paraguay", "peru",
	"poland", "portugal", "qatar", "saudi arabia", "scotland", "senegal", "serbia",
	"south africa", "spain", "sweden", "switzerland", "tunisia", "uruguay", "usa",
	"uzbekistan", "wales",
})


def looks_like_football(text):
	n = norm(text)
	if any(t in n for t in _SPORT_TERMS):
		return True
	hits = 0
	for nation in _FOOTBALL_NATIONS:
		if re.search(r"\b" + re.escape(nation) + r"\b", n):
			hits += 1
			if hits >= 2:
				return True
	return False


_JUNK_TERMS = ("sample", "trailer", "featurette", "behind the scenes",
               "deleted scene", "bloopers", "outtakes", "gag reel",
               "alternate ending", "alternate scene", "alternate final scene",
               "alternate opening")


def is_junk(stem):
	return any(j in norm(stem) for j in _JUNK_TERMS)


_EVENT_TERMS = (
	"coachella", "glastonbury", "tomorrowland", "lollapalooza", "bonnaroo",
	"reading festival", "leeds festival", "burning man", "ultra music",
	"festival", "in concert", "live at", "live in", "live from", "live aid",
	"world tour", "the tour", " tour ", "mtv unplugged", "bbc proms",
	"boiler room", "b2b", "dj set", "full set", "front row", "eurovision",
)


def looks_like_event(text):
	n = norm(text)
	return any(t in n for t in _EVENT_TERMS)


_QUALITY_TOKENS = (
	"1080p", "720p", "2160p", "4320p", "4k", "uhd", "480p", "576p",
	"bluray", "blu ray", "brrip", "bdrip", "remux", "web dl", "webdl", "webrip",
	"web rip", "hdtv", "dvdrip", "dvd", "x264", "x265", "h264", "h265", "hevc",
	"xvid", "avc", "amzn", "nf", "dsnp", "atvp", "hmax", "stan", "proper", "repack",
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_LEADING_NUMBER_RE = re.compile(r"^\d{1,3}\s")


def parse_movie(stem, full_path=None, folder=None):
	if parse_episode(stem, folder):
		return None
	if looks_like_football(full_path or stem):
		return None
	if looks_like_event(full_path or stem):
		return None
	if is_junk(stem):
		return None
	n = norm(stem)
	for m in reversed(list(_YEAR_RE.finditer(n))):
		if not any(q in n[m.end():] for q in _QUALITY_TOKENS):
			continue
		title = _clean_title(n[:m.start()], strip_year=False)
		if len(title) < 2 or _LEADING_NUMBER_RE.match(title):
			continue
		return (title, int(m.group(0)))
	return None


_RES_RANK = (("4320p", 6), ("2160p", 5), ("4k", 5), ("uhd", 5),
             ("1080p", 4), ("720p", 3), ("576p", 2), ("480p", 1))
_RES_LABEL = {6: "4320p", 5: "2160p", 4: "1080p", 3: "720p", 2: "576p", 1: "480p"}

_TV_PREFERRED_GROUPS = ("successfulcrab", "ethel", "ntb", "flux", "cakes", "silence")

_DV_RE = re.compile(r"\b(?:dolby\s?vision|dovi|dv)\b")
_HDR_RE = re.compile(r"\b(?:hdr10\+?|hdr|hlg)\b")
_CODEC_TAGS = (("x265", "HEVC"), ("h265", "HEVC"), ("hevc", "HEVC"),
               ("av1", "AV1"),
               ("x264", "H264"), ("h264", "H264"), ("avc", "H264"))


def _hdr_rank(n):
	return 2 if _DV_RE.search(n) else (1 if _HDR_RE.search(n) else 0)


def _quality_key(stem):
	n = norm(stem)
	res = max((r for tag, r in _RES_RANK if tag in n), default=0)
	hdr = _hdr_rank(n)
	grp = 1 if any(g in n for g in _TV_PREFERRED_GROUPS) else 0
	return (res, hdr, grp)


def _quality_label(stem):
	n = norm(stem)
	parts = []
	res = max((r for tag, r in _RES_RANK if tag in n), default=0)
	if res:
		parts.append(_RES_LABEL[res])
	hdr = _hdr_rank(n)
	if hdr:
		parts.append("DV" if hdr == 2 else "HDR")
	for tag, disp in _CODEC_TAGS:
		if tag in n:
			parts.append(disp)
			break
	return " ".join(parts)


_GROUP_RE = re.compile(r"-([A-Za-z0-9]{2,20})$")


def _release_group(stem):
	m = _GROUP_RE.search(stem.strip().rstrip("."))
	if not m:
		return ""
	grp = m.group(1)
	if not re.search(r"[A-Za-z]", grp) or _quality_label(grp):
		return ""
	return grp


def _copy_tags(stem):
	parts = [_quality_label(stem)]
	grp = _release_group(stem)
	if grp:
		parts.append(grp)
	return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Collect + write -- same shape as update_library.py's collect()/write_library()
# ---------------------------------------------------------------------------

TV_SUBDIR = "TV Shows"
MOVIE_SUBDIR = "Movies"
BROWSE_SUBDIR = "Browse"


def _build_url(provider, path):
	user_q = quote(provider["user"], safe="")
	pass_q = quote(provider["pass"], safe="")
	return "davs://%s:%s@%s:443%s" % (user_q, pass_q, provider["host"], quote(path))


def collect(providers, errors):
	versions = {}
	tv, movies, browse = {}, {}, {}
	for p in providers:
		token = base64.b64encode(("%s:%s" % (p["user"], p["pass"])).encode()).decode()
		auth_header = "Basic %s" % token
		try:
			for path, lastmod in walk_parallel(auth_header, p["host"], "/"):
				parts = [seg for seg in path.split("/") if seg]
				filename = parts[-1] if parts else path
				stem = filename.rpartition(".")[0] or filename
				folder = parts[-2] if len(parts) >= 2 else ""
				qkey = _quality_key(stem)
				url = _build_url(p, path)

				browse.setdefault(p["name"], {}).setdefault(stem, (url, lastmod))

				if is_junk(stem):
					continue

				episode = parse_episode(stem, folder)
				if episode:
					show, season, ep_no = episode
					key = (show, season, ep_no)
					cur = tv.get(key)
					if cur is None or qkey > cur[0]:
						tv[key] = (qkey, show, season, ep_no, url, lastmod, stem)
					label = "%s - S%02dE%02d" % (show.title(), season, ep_no)
					versions.setdefault(label, []).append((qkey, p["name"], stem, url, lastmod))
					continue

				# Football is deliberately skipped: that belongs to a
				# fixture/stage-grouped view, not this show/movie library.
				film = parse_movie(stem, path, folder)
				if not film:
					continue
				title, year = film
				key = (title, year)
				cur = movies.get(key)
				if cur is None or qkey > cur[0]:
					movies[key] = (qkey, title, year, url, lastmod, stem)
				versions.setdefault("%s (%d)" % (title.title(), year), []).append(
					(qkey, p["name"], stem, url, lastmod))
		except Exception:
			errors.append(p["name"])
	return tv, movies, browse, versions


def write_library(root, tv, movies, browse=None, versions=None):
	tv_root = os.path.join(root, TV_SUBDIR)
	movie_root = os.path.join(root, MOVIE_SUBDIR)
	browse_root = os.path.join(root, BROWSE_SUBDIR)

	tv_want, movie_want, browse_want = {}, {}, {}

	def _flagged(base, stem):
		tags = _copy_tags(stem)
		return "%s [%s]" % (base, tags) if tags else base

	for _qkey, show, season, episode, url, ts, stem in tv.values():
		show_disp = show.title()
		directory = os.path.join(tv_root, sanitise(show_disp), "Season %02d" % season)
		base = "%s - S%02dE%02d" % (show_disp, season, episode)
		tv_want[_strm_path(directory, _flagged(base, stem))] = (url, ts)

	for _qkey, title, year, url, ts, stem in movies.values():
		base = "%s (%d)" % (title.title(), year)
		movie_want[_strm_path(os.path.join(movie_root, sanitise(base)), _flagged(base, stem))] = (url, ts)

	by_provider = os.path.join(browse_root, "By Provider")
	for provider, items in (browse or {}).items():
		directory = os.path.join(by_provider, sanitise(provider))
		for stem, (url, ts) in items.items():
			browse_want[_strm_path(directory, stem)] = (url, ts)

	by_title = os.path.join(browse_root, "By Title")
	for label, copies in (versions or {}).items():
		distinct = {}
		for qkey, _provider, stem, url, ts in sorted(copies, key=lambda c: c[0], reverse=True):
			distinct.setdefault(stem, (url, ts))
		directory = os.path.join(by_title, sanitise(label))
		for stem, (url, ts) in distinct.items():
			flags = _quality_label(stem)
			name = "[%s] %s" % (flags, stem) if flags else stem
			browse_want[_strm_path(directory, name)] = (url, ts)

	written = deleted = 0
	for tree_root, want in ((tv_root, tv_want), (movie_root, movie_want), (browse_root, browse_want)):
		w, d = _reconcile(tree_root, want)
		written += w
		deleted += d
	return written, deleted


def run_sync(notify_start=False):
	"""The actual sync -- called by sync_now (context menu) and
	LibrarySyncMonitor (service.py). Returns a one-line summary string."""
	providers = webdav_providers()
	if not providers:
		return "no WebDAV providers configured"
	root = library_root()
	try:
		os.makedirs(root, exist_ok=True)
	except OSError as exc:
		notification("Library Sync: can't write %s (%s)" % (root, exc), 8000)
		return "write error: %s" % exc
	if notify_start:
		notification("Library Sync: starting...", 2500)
	errors = []
	tv, movies, browse, versions = collect(providers, errors)
	if errors and len(errors) == len(providers):
		notification("Library Sync: all providers failed, kept existing library", 5000)
		return "all providers failed"
	written, deleted = write_library(root, tv, movies, browse, versions)
	if written or deleted:
		_refresh_kodi_library(clean=not errors)
	summary = "%d episodes, %d movies; %d written / %d pruned" % (len(tv), len(movies), written, deleted)
	if errors:
		notification("Library Sync: %s. Failed: %s" % (summary, ", ".join(errors)), 5000)
	else:
		notification("Library Sync: %s" % summary, 4000)
	return summary


def _refresh_kodi_library(clean):
	import xbmc
	if clean:
		xbmc.executeJSONRPC('{"jsonrpc":"2.0","id":1,"method":"VideoLibrary.Clean","params":{"showdialogs":false}}')
	xbmc.executeJSONRPC('{"jsonrpc":"2.0","id":1,"method":"VideoLibrary.Scan","params":{"showdialogs":false}}')


# ---------------------------------------------------------------------------
# Router entry points -- mode=strm_library.<name>, wired in modules/router.py
# ---------------------------------------------------------------------------

def toggle_enabled(params=None):
	new_state = "false" if enabled() else "true"
	set_setting(_ENABLED_KEY, new_state)
	notification("Library Sync %s" % ("Enabled" if new_state == "true" else "Disabled"), 3000)


def configure_webdav(params=None):
	"""Prompt for host/user/pass per provider, same input-dialog UX Red Light
	already uses for Debrid-Link's API key (DebridLinkAPI.auth()). Leaving the
	host blank clears that provider's credentials."""
	for key in _PROVIDER_KEYS:
		label = _WEBDAV_LABELS[key]
		host = kodi_dialog().input("%s WebDAV host (blank to clear)" % label,
		                            defaultt=get_setting(_webdav_key(key, "host"), ""))
		if host is None:
			continue
		host = host.strip()
		if not host:
			for field in ("host", "user", "pass"):
				set_setting(_webdav_key(key, field), "")
			continue
		user = kodi_dialog().input("%s WebDAV username" % label,
		                            defaultt=get_setting(_webdav_key(key, "user"), ""))
		password = kodi_dialog().input("%s WebDAV password" % label,
		                                option=xbmcgui.ALPHANUM_HIDE_INPUT)
		set_setting(_webdav_key(key, "host"), host)
		set_setting(_webdav_key(key, "user"), (user or "").strip())
		set_setting(_webdav_key(key, "pass"), password or "")
	notification("Library Sync: WebDAV credentials updated", 3000)


def sync_now(params=None):
	if not webdav_providers():
		return ok_dialog(heading="Library Sync",
		                  text="No WebDAV providers configured. Run \"Configure WebDAV Credentials\" first.")
	run_sync(notify_start=True)


def set_interval(params=None):
	current = str(sync_interval()[0])
	value = kodi_dialog().input("Library Sync interval (minutes, min 15)", defaultt=current,
	                             type=xbmcgui.INPUT_NUMERIC)
	if not value:
		return
	try: minutes = max(15, int(value))
	except ValueError: return
	set_setting(_INTERVAL_KEY, str(minutes))
	notification("Library Sync: syncing every %d minutes" % minutes, 3000)


def set_root(params=None):
	value = kodi_dialog().input("Library root path", defaultt=library_root())
	if not value or not value.strip():
		return
	set_setting(_ROOT_KEY, value.strip())
	notification("Library Sync: root set to %s" % value.strip(), 3000)
