# -*- coding: utf-8 -*-
"""Where a release came from, ranked (#297). One pattern table, shared by the result sort
(source_tier), the source_utils info tags and the subtitle matcher, so the three cannot drift.

Tiers, best first: 1 disc remux/full disc, 2 disc encode, 3 web untouched (WEB-DL), 4 web
re-encoded (WEBRip), 5 DVD, 6 TV capture, 7 unknown. Resolution still sorts first; this orders
releases inside one resolution.
"""
import re
from functools import lru_cache

UNKNOWN_TIER = 7
TV_CAPTURE_TIER = 6

# (tag, tier, patterns, remove). Patterns are matched against the dotted lower-case form of the name
# (every run of non-alphanumerics is one dot, dots on both ends), so '.dsr.' is a whole word and
# 'web.dl' covers WEB-DL, WEB_DL and WEBDL spelled apart. 'remove' lists text blanked before matching,
# so a DVDRemux is not also a REMUX.
SOURCE_TABLE = (
	('DVD', 5, ('dvdremux', 'dvd.remux', 'dvdmux', 'dvd.mux'), ()),
	('REMUX', 1, ('remux', 'bdmux', 'bd.mux'), ('dvdremux', 'dvd.remux')),
	('DISC', 1, ('.bdmv.', 'complete.bluray', 'complete.blu.ray', '.iso.'), ()),
	('BLURAY', 2, ('bluray', 'blu.ray', 'bdrip', 'bd.rip', 'brrip', 'br.rip', 'uhdrip', 'uhd.rip', '.bdr.'), ()),
	('WEB-DL', 3, ('webdl', 'web.dl', 'webmux', 'web.mux'), ()),
	('WEBRIP', 4, ('webrip', 'web.rip'), ()),
	('DVD', 5, ('dvd5', 'dvd9', 'dvdrip', 'dvd.rip', '.dvd.'), ()),
	('HDTV', 6, ('hdtv',), ()),
	('PDTV', 6, ('pdtv',), ()),
	('SDTV', 6, ('sdtv',), ()),
	('DSR', 6, ('.dsr.', '.dsrip.', '.ds.rip.'), ()),
	('TVRIP', 6, ('tvrip', 'tv.rip'), ()),
	('SATRIP', 6, ('satrip', 'sat.rip'), ()),
	('HDRIP', 6, ('.hdrip.', '.hd.rip.'), ()),
)
# A bare '.web.' is WEB-DL, but only when nothing more specific matched: 'The.Web.S01.HDTV' is HDTV.
WEAK_WEB = ('WEB-DL', 3, ('.web.',), ())

# Subtitle releases are compared by family: a WEB-DL sub fits a WEBRip video, a TV capture fits any
# TV capture. Tags not listed are their own family.
_FAMILY = {'DISC': 'REMUX', 'WEB-DL': 'WEB', 'WEBRIP': 'WEB', 'PDTV': 'HDTV', 'SDTV': 'HDTV', 'DSR': 'HDTV', 'TVRIP': 'HDTV', 'SATRIP': 'HDTV'}

def normalize(text):
	return '.%s.' % re.sub(r'[^a-z0-9]+', '.', (text or '').lower()).strip('.')

@lru_cache(maxsize=4096)
def _matches(norm):
	"""[(tag, tier)] for every table row that matches, in table order; the bare-web row only when alone."""
	found = []
	for tag, tier, patterns, remove in SOURCE_TABLE:
		text = norm
		for gone in remove: text = text.replace(gone, '.')
		if any(p in text for p in patterns): found.append((tag, tier))
	if not found and WEAK_WEB[2][0] in norm: found.append((WEAK_WEB[0], WEAK_WEB[1]))
	return tuple(found)

def source_tags(name):
	"""Matched source tags, best tier first, without repeats."""
	tags = []
	for tag, tier in sorted(_matches(normalize(name)), key=lambda m: m[1]):
		if tag not in tags: tags.append(tag)
	return tags

def source_tier(name):
	"""1 (disc remux) to 6 (TV capture); 7 when the name says nothing about the source."""
	found = _matches(normalize(name))
	return min([tier for tag, tier in found]) if found else UNKNOWN_TIER

def source_family(tag):
	return _FAMILY.get(tag, tag)
