# -*- coding: utf-8 -*-
"""Source tier ranking (#297) and the folders-first TV-capture decline (#306).

Resolution still sorts first; inside one resolution a disc remux beats a disc encode beats WEB-DL
beats WEBRip beats DVD beats a TV capture beats an unnamed release. Runs under pytest and directly:
python3 tests/test_source_tiers.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if 'xbmc' not in sys.modules:
	sys.path.insert(0, HERE)
	import kodi_stub
	kodi_stub.install()
	sys.path.insert(0, os.path.join(HERE, '..', 'plugin.video.redlight', 'resources', 'lib'))

from modules import kodi_utils, settings
from modules import source_tiers
from modules.source_tiers import source_tier, source_tags
from modules.sources import Sources

SAMPLES = {
	1: ['Movie.2020.2160p.UHD.BluRay.REMUX.HDR.mkv', 'Movie.1080p.BDRemux.mkv', 'Movie.1080p.BDMux.mkv', 'Movie.BDMV', 'Movie.ISO', 'Movie.2020.COMPLETE.BLURAY-GRP'],
	2: ['Movie.1080p.BluRay.x264-GRP.mkv', 'Movie.1080p.BDRip.mkv', 'Movie.1080p.BRRip.mkv', 'Movie.2160p.UHDRip.mkv', 'Movie 1080p Blu-ray x265'],
	3: ['Show.S01E01.1080p.WEB-DL.DDP5.1.mkv', 'Show.S01E01.1080p.WEBDL.mkv', 'Show.S01E01.1080p.WEBMux.mkv', 'Show.S01E01.1080p.WEB.H264-GRP.mkv'],
	4: ['Show.S01E01.1080p.WEBRip.x265.mkv', 'Show.S01E01.1080p.WEB.Rip.mkv'],
	5: ['Movie.DVDRemux.mkv', 'Movie.DVDMux.mkv', 'Movie.DVD5.mkv', 'Movie.DVD9.mkv', 'Movie.DVDRip.xvid.avi'],
	6: ['Show.S01E01.720p.HDTV.x264-GRP.mkv', 'Show.S01E01.PDTV.mkv', 'Show.S01E01.SDTV.mkv', 'Show.S01E01.DSR.mkv', 'Show.S01E01.DSRip.mkv',
		'Show.S01E01.TVRip.mkv', 'Show.S01E01.SATRip.mkv', 'Show.S01E01.HDRip.mkv'],
	7: ['Show.S01E01.1080p.x265-GRP.mkv', 'Daria (1997) - S01e11 - Road Worrier.mkv', '', None],
}


def test_each_tier_matches_its_sample_names():
	for tier, names in SAMPLES.items():
		for name in names:
			assert source_tier(name) == tier, (name, source_tier(name), tier)


def test_specific_names_beat_a_bare_web_and_dvd_remux_is_not_a_disc_remux():
	assert source_tier('The.Web.S01E01.HDTV.mkv') == 6
	assert source_tier('Movie.DVDRemux.mkv') == 5
	assert 'REMUX' not in source_tags('Movie.DVDRemux.mkv')
	assert source_tags('Movie.1080p.WEBRip.mkv') == ['WEBRIP']
	assert source_tags('Movie.1080p.WEB-DL.mkv') == ['WEB-DL']
	assert source_tags('Movie.2160p.BluRay.REMUX.mkv') == ['REMUX', 'BLURAY']


def test_subtitle_matcher_shares_the_table_and_keeps_web_and_tv_families():
	from indexers import subtitles
	assert not hasattr(subtitles, '_RELEASE_SOURCE_PATTERNS')
	assert subtitles._detect_release_source_tags('Show.S01E01.1080p.WEB-DL.mkv') == {'WEB'}
	assert subtitles._detect_release_source_tags('Show.S01E01.1080p.WEBRip.mkv') == {'WEB'}
	assert subtitles._detect_release_source_tags('Show.S01E01.720p.HDTV.mkv') == {'HDTV'}
	assert subtitles._detect_release_source_tags('Movie.2160p.BluRay.REMUX.mkv') == {'REMUX', 'BLURAY'}
	assert subtitles._release_sources_compatible('WEB', 'WEB')
	assert not subtitles._release_sources_compatible('WEB', 'HDTV')


def _sorted_names(items, sort_order='1'):
	"""Run the real sort_results over items [(name, quality)], 4K/1080p/720p/SD in that order."""
	values = {'redlight.results.sort_order': sort_order}
	original = settings.get_setting
	settings.get_setting = lambda key, default=None: values.get(key, default)
	try:
		src = object.__new__(Sources)
		src.sort_function = settings.results_sort_order()
		src.quality_sort_order = ['4K', '1080p', '720p', 'SD']
		src.provider_sort_ranks = {}
		src._get_size_rank = lambda item: 0
		src._aiostreams_preserve_order = lambda: False
		src._sort_uncached_results = lambda results: results
		rows = [{'name': n, 'quality': q, 'debrid': 'Real-Debrid', 'scrape_provider': 'rd_cloud'} for n, q in items]
		return [i['name'] for i in src.sort_results(rows)]
	finally:
		settings.get_setting = original


def test_resolution_first_then_tier_in_every_sort_order():
	for order in ('0', '1', '2', '3', '4', '5'):
		# Size is equal everywhere, so the quality/tier keys decide in all six orders.
		got = _sorted_names([('a.HDTV', '1080p'), ('b.WEBRip', '1080p'), ('c.unnamed', '1080p'), ('d.WEB-DL', '1080p'), ('e.REMUX', '1080p'),
			('f.BluRay', '1080p'), ('g.DVDRip', '1080p')], order)
		assert got == ['e.REMUX', 'f.BluRay', 'd.WEB-DL', 'b.WEBRip', 'g.DVDRip', 'a.HDTV', 'c.unnamed'], (order, got)


def test_web_dl_beats_hdtv_and_resolution_beats_tier():
	assert _sorted_names([('x.1080p.HDTV', '1080p'), ('x.1080p.WEB-DL', '1080p')]) == ['x.1080p.WEB-DL', 'x.1080p.HDTV']
	assert _sorted_names([('x.1080p.REMUX', '1080p'), ('x.2160p.WEB-DL', '4K')]) == ['x.2160p.WEB-DL', 'x.1080p.REMUX']
	assert _sorted_names([('x.WEBRip', '1080p'), ('x.WEB-DL', '1080p')]) == ['x.WEB-DL', 'x.WEBRip']


def test_sort_first_keeps_folders_first_then_quality_then_tier():
	original = settings.sort_to_top
	settings.sort_to_top = lambda provider: provider == 'folders'
	try:
		src = object.__new__(Sources)
		src.all_scrapers = ['folders', 'rd_cloud']
		src._sort_folder_to_top = lambda provider: 0 if provider == 'folders' else 1
		rows = [{'name': 'cloud', 'scrape_provider': 'rd_cloud', 'quality_rank': 1, 'source_tier': 1},
			{'name': 'folder hdtv', 'scrape_provider': 'folders', 'quality_rank': 2, 'source_tier': 6},
			{'name': 'folder web', 'scrape_provider': 'folders', 'quality_rank': 2, 'source_tier': 3},
			{'name': 'folder 4k', 'scrape_provider': 'folders', 'quality_rank': 1, 'source_tier': 6}]
		assert [i['name'] for i in src.sort_first(rows)] == ['folder 4k', 'folder web', 'folder hdtv', 'cloud']
	finally:
		settings.sort_to_top = original


def _shortcut(names):
	"""Run the real _folders_first_shortcut over folder hits with these names; (took shortcut, timing lines)."""
	lines = []
	src = object.__new__(Sources)
	src.prescrape_sources = [{'scrape_provider': 'folders', 'name': n, 'id': str(i)} for i, n in enumerate(names)]
	src.cloud_prescrape_autoplay, src.autoplay = False, True
	src.process_results = lambda found: list(found)
	src._log_prescrape_timing = lambda started, message: lines.append(message)
	original = settings.autoplay_prescrape
	settings.autoplay_prescrape = lambda provider: True
	try:
		return src._folders_first_shortcut([], 0.0), lines
	finally:
		settings.autoplay_prescrape = original


def test_folders_first_declines_when_every_hit_is_a_tv_capture():
	took, lines = _shortcut(['Show.S01E01.720p.HDTV.x264-GRP.mkv'])
	assert took is False
	assert any('only TV-capture folder hit(s)' in m and 'starting cloud tier' in m for m in lines), lines
	took, lines = _shortcut(['Show.S01E01.HDTV.mkv', 'Show.S01E01.PDTV.mkv'])
	assert took is False


def test_folders_first_accepts_a_web_dl_or_unnamed_hit_and_a_mixed_set():
	took, lines = _shortcut(['Show.S01E01.1080p.WEB-DL.mkv'])
	assert took is True and any('cloud tier skipped' in m for m in lines), lines
	assert _shortcut(['Daria (1997) - S01e11 - Road Worrier.mkv'])[0] is True
	assert _shortcut(['Show.S01E01.HDTV.mkv', 'Show.S01E01.1080p.WEB-DL.mkv'])[0] is True


if __name__ == '__main__':
	ran = 0
	for fn_name, fn in sorted(globals().items()):
		if fn_name.startswith('test_') and callable(fn):
			fn(); ran += 1
	print('%d source tier tests passed' % ran)
