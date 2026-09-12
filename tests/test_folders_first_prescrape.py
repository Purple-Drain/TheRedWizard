# -*- coding: utf-8 -*-
"""Folders-first prescrape (#149).

With an autoplay-ready folder (zurg) hit, the cloud scrapers are never started instead of being
waited for. The load-bearing part is the bookkeeping: a skipped cloud scraper must not be recorded
as having run, or the full scrape that follows a failed folder play (#104) would skip it too.
Sources is built with object.__new__, as in test_sources_thread_join.py; the scrapers, the waits
and process_results are fakes, and _prescrape_autoplay_candidates is the real one.
"""
import threading

import pytest

from modules import kodi_utils, settings
from modules.sources import Sources

FOLDER = ('folders', 'folder_fn', 'My Library')
RD = ('internal', 'rd_fn', 'rd_cloud')
TB = ('internal', 'tb_fn', 'tb_cloud')
FOLDER_HIT = {'scrape_provider': 'folders', 'name': 'Daria (1997) - S01e11 - Road Worrier.mkv', 'id': 'f1'}
RD_HIT = {'scrape_provider': 'rd_cloud', 'name': 'Daria (1997) - S01e11 - Road Worrier.mkv', 'id': 'r1'}


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: lines.append((heading, message)))
    return lines


@pytest.fixture
def switches(monkeypatch):
    state = {'check_folders': True, 'folders_first': True, 'autoplay': {'folders': True, 'rd_cloud': True, 'tb_cloud': True}}
    monkeypatch.setattr(settings, 'check_prescrape_sources', lambda scraper, media_type: state['check_folders'])
    monkeypatch.setattr(settings, 'prescrape_folders_first', lambda: state['folders_first'])
    monkeypatch.setattr(settings, 'autoplay_prescrape', lambda provider: state['autoplay'].get(provider, False))
    return state


def _bare_sources(results, background=False, cloud=(RD, TB), process=None, gate=None):
    src = object.__new__(Sources)
    src.active_folders, src.media_type = True, 'episode'
    src.tmdb_id, src.season, src.episode = 2131, 1, 11
    src.prescrape_threads, src.prescrape_scrapers, src.prescrape_sources, src.remove_scrapers = [], [], [], []
    src.background, src.autoplay, src.cloud_prescrape_autoplay = background, True, False
    src.progress_dialog, src._scrape_user_cancelled = None, False
    src.ran, src.waits = [], []
    src.append_folder_scrapers = lambda current_list: current_list.append(FOLDER)
    src.internal_sources = lambda prescrape=False, cloud_early=False: list(cloud)

    def _activate(module_type, function, prescrape):
        name = threading.current_thread().name
        if gate is not None and name == FOLDER[2]: gate.wait(2)
        src.ran.append(name)
        found = list(results.get(name, []))
        if found: src.prescrape_sources.extend(found)
        return found
    src.activate_providers = _activate

    def _wait(max_wait=None):
        src.waits.append(max_wait)
        for thread in list(src.prescrape_threads):
            thread.join(timeout=2 if max_wait is None else max_wait)
    src.scrapers_dialog = _wait
    src._join_prescrape_threads = _wait

    def _process(found):
        # The real process_results sets this on an autoplay hit and never clears it.
        if found: src.cloud_prescrape_autoplay = True
        return found
    src.process_results = process or _process
    return src


def _timing(logged):
    return [message for heading, message in logged if heading == 'ScrapePrescrapeTiming']


def test_folder_hit_skips_the_cloud_tier_and_leaves_it_eligible_for_the_full_scrape(switches, logged):
    src = _bare_sources({'My Library': [FOLDER_HIT]})
    assert src.collect_prescrape_results() == [FOLDER_HIT]
    assert src.ran == ['My Library']
    assert src.waits == [4.0]
    assert src.prescrape_scrapers == [FOLDER]
    assert src.prescrape_ran_scrapers == {'My Library'}
    assert 'rd_cloud' not in src.remove_scrapers and 'tb_cloud' not in src.remove_scrapers
    assert 'folders' in src.remove_scrapers
    # The probe's process_results call must not leave the autoplay flag behind.
    assert src.cloud_prescrape_autoplay is False
    assert any('folders first: 1 autoplay hit(s), cloud tier skipped' in m for m in _timing(logged))


def test_folder_miss_starts_the_cloud_tier_after_the_head_start(switches, logged):
    src = _bare_sources({'rd_cloud': [RD_HIT]})
    assert src.collect_prescrape_results() == [RD_HIT]
    assert sorted(src.ran) == ['My Library', 'rd_cloud', 'tb_cloud']
    assert src.ran[0] == 'My Library'
    assert src.waits == [4.0, None]
    assert src.prescrape_ran_scrapers == {'My Library', 'rd_cloud', 'tb_cloud'}
    assert any('folders first declined (no folder results), starting cloud tier' in m for m in _timing(logged))


def test_folder_result_autoplay_would_not_take_starts_the_cloud_tier(switches, logged):
    src = _bare_sources({'My Library': [FOLDER_HIT], 'rd_cloud': [RD_HIT]}, process=lambda found: [])
    src.collect_prescrape_results()
    assert sorted(src.ran) == ['My Library', 'rd_cloud', 'tb_cloud']
    assert src.cloud_prescrape_autoplay is False
    assert any("1 folder result(s), none autoplay would take" in m for m in _timing(logged))


def test_slow_folder_tier_starts_the_cloud_tier_alongside(switches, logged):
    gate = threading.Event()
    src = _bare_sources({'My Library': [FOLDER_HIT]}, gate=gate)
    src._folders_first_head_start = lambda: 0.05
    releaser = threading.Timer(0.3, gate.set)
    releaser.start()
    try:
        src.collect_prescrape_results()
    finally:
        gate.set(); releaser.cancel()
    assert 'rd_cloud' in src.ran and 'tb_cloud' in src.ran
    assert any('folders still running: My Library' in m for m in _timing(logged))


def test_background_prep_runs_every_scraper_at_once(switches):
    src = _bare_sources({'My Library': [FOLDER_HIT]}, background=True)
    src.collect_prescrape_results()
    assert sorted(src.ran) == ['My Library', 'rd_cloud', 'tb_cloud']
    assert src.waits == [None]


def test_kill_switch_off_runs_every_scraper_at_once(switches):
    switches['folders_first'] = False
    src = _bare_sources({'My Library': [FOLDER_HIT]})
    src.collect_prescrape_results()
    assert sorted(src.ran) == ['My Library', 'rd_cloud', 'tb_cloud']
    assert src.waits == [None]


def test_folders_autoplay_off_runs_every_scraper_at_once(switches):
    switches['autoplay']['folders'] = False
    src = _bare_sources({'My Library': [FOLDER_HIT]})
    src.collect_prescrape_results()
    assert sorted(src.ran) == ['My Library', 'rd_cloud', 'tb_cloud']
    assert src.waits == [None]


def test_no_cloud_scrapers_keeps_the_single_wait(switches):
    src = _bare_sources({'My Library': [FOLDER_HIT]}, cloud=())
    assert src.collect_prescrape_results() == [FOLDER_HIT]
    assert src.ran == ['My Library']
    assert src.waits == [None]
