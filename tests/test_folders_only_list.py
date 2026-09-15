# -*- coding: utf-8 -*-
"""Folder results first in the manual source list (#175).

With Autoplay off and folders.only_list on, a finished folder tier with results opens the list
without starting the cloud tier, and the list's last entry then searches the clouds as well. The
load-bearing part is the bookkeeping: the skipped cloud scrapers stay out of remove_scrapers and
prescrape_ran_scrapers, and the follow-up search must not exclude them. Built like
test_folders_first_prescrape.py: object.__new__(Sources) with fake scrapers, waits and
process_results.
"""
import threading
from types import SimpleNamespace

import pytest

from modules import kodi_utils, settings
from modules.sources import Sources

FOLDER = ('folders', 'folder_fn', 'RD folder')
RD = ('internal', 'rd_fn', 'rd_cloud')
TB = ('internal', 'tb_fn', 'tb_cloud')
FOLDER_HIT = {'scrape_provider': 'folders', 'name': 'Clerks (1994) 1080p.mkv', 'id': 'f1'}
RD_HIT = {'scrape_provider': 'rd_cloud', 'name': 'Clerks (1994) 1080p.mkv', 'id': 'r1'}


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: lines.append((heading, message)))
    return lines


@pytest.fixture
def switches(monkeypatch):
    state = {'check_folders': True, 'folders_first': True, 'only_list': True,
             'autoplay': {'folders': True, 'rd_cloud': True, 'tb_cloud': True}}
    monkeypatch.setattr(settings, 'check_prescrape_sources', lambda scraper, media_type: state['check_folders'])
    monkeypatch.setattr(settings, 'prescrape_folders_first', lambda: state['folders_first'])
    monkeypatch.setattr(settings, 'folders_only_list', lambda: state['only_list'])
    monkeypatch.setattr(settings, 'autoplay_prescrape', lambda provider: state['autoplay'].get(provider, False))
    return state


def _bare_sources(results, autoplay=False, background=False, process=None, gate=None):
    src = object.__new__(Sources)
    src.active_folders, src.media_type = True, 'movie'
    src.tmdb_id, src.season, src.episode = 2292, None, None
    src.prescrape_threads, src.prescrape_scrapers, src.prescrape_sources, src.remove_scrapers = [], [], [], []
    src.background, src.autoplay, src.cloud_prescrape_autoplay = background, autoplay, False
    src.progress_dialog, src._scrape_user_cancelled = None, False
    src.ran, src.waits = [], []
    src.append_folder_scrapers = lambda current_list: current_list.append(FOLDER)
    src.internal_sources = lambda prescrape=False, cloud_early=False: [RD, TB]

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
        if found: src.cloud_prescrape_autoplay = True
        return found
    src.process_results = process or _process
    return src


def _timing(logged):
    return [message for heading, message in logged if heading == 'ScrapePrescrapeTiming']


def test_manual_folder_hit_lists_the_folders_and_leaves_the_clouds_for_the_search_entry(switches, logged):
    src = _bare_sources({'RD folder': [FOLDER_HIT]})
    assert src.collect_prescrape_results() == [FOLDER_HIT]
    assert src.ran == ['RD folder']
    assert src.waits == [4.0]
    assert src.folders_only_skipped == ['rd_cloud', 'tb_cloud']
    assert src.prescrape_ran_scrapers == {'RD folder'}
    assert 'rd_cloud' not in src.remove_scrapers and 'tb_cloud' not in src.remove_scrapers
    assert src.cloud_prescrape_autoplay is False
    assert any('folders only: 1 folder result(s) listed, cloud tier left for the search entry' in m for m in _timing(logged))


def test_manual_folder_miss_starts_the_cloud_tier(switches, logged):
    src = _bare_sources({'rd_cloud': [RD_HIT]})
    assert src.collect_prescrape_results() == [RD_HIT]
    assert sorted(src.ran) == ['RD folder', 'rd_cloud', 'tb_cloud']
    assert src.waits == [4.0, None]
    assert src.folders_only_skipped == []
    assert any('folders only declined (no folder results), starting cloud tier' in m for m in _timing(logged))


def test_manual_folder_results_all_filtered_out_start_the_cloud_tier(switches, logged):
    src = _bare_sources({'RD folder': [FOLDER_HIT], 'rd_cloud': [RD_HIT]}, process=lambda found: [])
    src.collect_prescrape_results()
    assert sorted(src.ran) == ['RD folder', 'rd_cloud', 'tb_cloud']
    assert src.folders_only_skipped == []
    assert any('1 folder result(s), none passed the filters' in m for m in _timing(logged))


def test_slow_manual_folder_tier_starts_the_cloud_tier_alongside(switches, logged):
    gate = threading.Event()
    src = _bare_sources({'RD folder': [FOLDER_HIT]}, gate=gate)
    src._folders_first_head_start = lambda: 0.05
    releaser = threading.Timer(0.3, gate.set)
    releaser.start()
    try:
        src.collect_prescrape_results()
    finally:
        gate.set(); releaser.cancel()
    assert 'rd_cloud' in src.ran and 'tb_cloud' in src.ran
    assert src.folders_only_skipped == []


def test_setting_off_runs_every_scraper_at_once(switches):
    switches['only_list'] = False
    src = _bare_sources({'RD folder': [FOLDER_HIT]})
    src.collect_prescrape_results()
    assert sorted(src.ran) == ['RD folder', 'rd_cloud', 'tb_cloud']
    assert src.waits == [None]
    assert src.folders_only_skipped == []


def test_autoplay_on_keeps_the_folders_first_path(switches, logged):
    src = _bare_sources({'RD folder': [FOLDER_HIT]}, autoplay=True)
    src.collect_prescrape_results()
    assert src.ran == ['RD folder']
    assert src.folders_only_skipped == []
    assert any('folders first: 1 autoplay hit(s), cloud tier skipped' in m for m in _timing(logged))


def test_background_prep_ignores_the_setting(switches):
    src = _bare_sources({'RD folder': [FOLDER_HIT]}, background=True)
    src.collect_prescrape_results()
    assert sorted(src.ran) == ['RD folder', 'rd_cloud', 'tb_cloud']
    assert src.folders_only_skipped == []


def _followup_sources(skipped):
    src = object.__new__(Sources)
    src.determine_scrapers_status = lambda: None
    src.active_internal_scrapers = ['external', 'folders', 'rd_cloud', 'tb_cloud', 'easynews']
    src.remove_scrapers = ['RD folder', 'folders']
    src.folders_only_skipped = skipped
    src.active_external = False
    return src


def test_search_entry_after_a_folders_only_list_keeps_the_skipped_clouds_eligible():
    src = _followup_sources(['rd_cloud', 'tb_cloud'])
    src._exclude_internal_scrapers_for_external_only_followup()
    assert 'rd_cloud' not in src.remove_scrapers and 'tb_cloud' not in src.remove_scrapers
    assert 'easynews' in src.remove_scrapers and 'external' not in src.remove_scrapers
    assert src._can_continue_full_scrape() is True


def test_search_entry_after_a_normal_list_still_runs_external_only():
    src = _followup_sources([])
    src._exclude_internal_scrapers_for_external_only_followup()
    assert {'rd_cloud', 'tb_cloud', 'easynews'} <= set(src.remove_scrapers)


@pytest.mark.parametrize('ref, label', [
    (None, 'RUN EXTERNAL SCRAPER SEARCH'),
    (SimpleNamespace(folders_only_skipped=[], active_external=True), 'RUN EXTERNAL SCRAPER SEARCH'),
    (SimpleNamespace(folders_only_skipped=['rd_cloud'], active_external=True), 'SEARCH CLOUD AND EXTERNAL SCRAPERS'),
    (SimpleNamespace(folders_only_skipped=['rd_cloud'], active_external=False), 'SEARCH CLOUD SCRAPERS'),
])
def test_search_entry_label(ref, label):
    from windows.sources import SourcesResults
    window = object.__new__(SourcesResults)
    window.sources_ref = ref
    assert window._full_search_label() == label
