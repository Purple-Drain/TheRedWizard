# -*- coding: utf-8 -*-
"""Waterfall (sequential) prescrape ladder (#22).

Opt-in via redlight.prescrape.sequential (settings.prescrape_sequential), foreground only (#3
keeps background prep running everything at once). Rungs run in provider_sort_ranks() order (#6);
everything inside one rung starts together and races, reacting to the first usable result instead
of waiting for the whole rung (#10, race semantics: whichever slot answers first, not slot 1
always). A decided rung's losing threads are left running, same as #149's folder tier does today,
so a slower slot's result can still land in prescrape_sources and be used as a live fallback pool
by playback_failed_action if the winner later fails to open.

Sources is built with object.__new__, as in test_folders_first_prescrape.py; the scrapers, waits
and process_results are fakes, real methods are _prescrape_autoplay_candidates,
_collect_prescrape_results_sequential, _wait_for_rung, _sequential_rung_candidates and
playback_failed_action's new fallback-pool branch.
"""
import threading
import time

import pytest

from modules import kodi_utils, settings
from modules.sources import Sources

FOLDER_A = ('folders', 'folder_a_fn', 'Slot A')
FOLDER_B = ('folders', 'folder_b_fn', 'Slot B')
RD = ('internal', 'rd_fn', 'rd_cloud')
TB = ('internal', 'tb_fn', 'tb_cloud')
AD = ('internal', 'ad_fn', 'ad_cloud')

RANKS = {'folders': 6, 'rd_cloud': 8, 'tb_cloud': 8, 'ad_cloud': 9}

A_HIT = {'scrape_provider': 'folders', 'source': 'Slot A', 'id': 'a1'}
B_HIT = {'scrape_provider': 'folders', 'source': 'Slot B', 'id': 'b1'}
RD_HIT = {'scrape_provider': 'rd_cloud', 'id': 'r1'}
TB_HIT = {'scrape_provider': 'tb_cloud', 'id': 't1'}
AD_HIT = {'scrape_provider': 'ad_cloud', 'id': 'd1'}


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: lines.append((heading, message)))
    return lines


@pytest.fixture
def switches(monkeypatch):
    state = {'ranks': dict(RANKS), 'check_folders': True, 'autoplay': True, 'sequential': True}
    monkeypatch.setattr(settings, 'check_prescrape_sources', lambda scraper, media_type: state['check_folders'])
    monkeypatch.setattr(settings, 'provider_sort_ranks', lambda: dict(state['ranks']))
    monkeypatch.setattr(settings, 'autoplay_prescrape', lambda provider: True)
    monkeypatch.setattr(settings, 'prescrape_sequential', lambda: state['sequential'])
    monkeypatch.setattr(kodi_utils, 'kodi_monitor', lambda: type('M', (), {'abortRequested': staticmethod(lambda: False)})())
    monkeypatch.setattr(kodi_utils, 'sleep', lambda ms: time.sleep(0.005))
    return state


def _bare_sources(results, folders=(FOLDER_A,), cloud=(RD, TB), background=False, autoplay=True,
                   process=None, gates=None):
    src = object.__new__(Sources)
    src.active_folders, src.media_type = True, 'episode'
    src.tmdb_id, src.season, src.episode = 2131, 1, 11
    src.prescrape_threads, src.prescrape_scrapers, src.prescrape_sources, src.remove_scrapers = [], [], [], []
    src.background, src.autoplay, src.cloud_prescrape_autoplay = background, autoplay, False
    src.progress_dialog, src.sleep_time = None, 10
    src.ran = []
    src.append_folder_scrapers = lambda current_list: current_list.extend(folders)
    src.internal_sources = lambda prescrape=False, cloud_early=False: list(cloud)
    src._touch_sources_busy = lambda: None
    src._process_internal_results = lambda: None
    src._user_cancelled_scrape = lambda: False

    def _activate(module_type, function, prescrape):
        name = threading.current_thread().name
        gate = (gates or {}).get(name)
        if gate is not None:
            gate.wait(2)
        src.ran.append(name)
        found = list(results.get(name, []))
        if found:
            src.prescrape_sources.extend(found)
        return found
    src.activate_providers = _activate

    def _process(found):
        return found
    src.process_results = process or _process
    return src


def _timing(logged):
    return [message for heading, message in logged if heading == 'ScrapePrescrapeTiming']


def test_rungs_run_in_rank_order_and_a_hit_stops_later_rungs(switches, logged):
    src = _bare_sources({'rd_cloud': [RD_HIT]}, folders=(), cloud=(RD, AD))
    result = src._collect_prescrape_results_sequential()
    assert result == [RD_HIT]
    assert src.ran == ['rd_cloud']
    assert 'ad_cloud' not in src.ran
    assert 'rd_cloud' in src.remove_scrapers
    assert 'ad_cloud' not in src.remove_scrapers  # never started, stays eligible for the full scrape
    timing = _timing(logged)
    assert any('rung started rank=8 scrapers=rd_cloud' in m for m in timing)
    assert any('rung hit rank=8 winner=rd_cloud results=1' in m for m in timing)
    assert not any('rank=9' in m for m in timing)


def test_a_rung_miss_moves_to_the_next_rung_and_stays_in_remove_scrapers(switches, logged):
    src = _bare_sources({'ad_cloud': [AD_HIT]}, folders=(), cloud=(RD, AD))
    result = src._collect_prescrape_results_sequential()
    assert result == [AD_HIT]
    assert sorted(src.ran) == ['ad_cloud', 'rd_cloud']
    # rd_cloud started and missed: it ran, so it must not be re-run by the later full scrape.
    assert 'rd_cloud' in src.remove_scrapers
    assert 'ad_cloud' in src.remove_scrapers
    assert any('rung declined rank=8 reason=no_candidates' in m for m in _timing(logged))


def test_folder_race_picks_whichever_slot_answers_first_not_always_slot_a(switches, logged):
    gate_a = threading.Event()
    src = _bare_sources({'Slot A': [A_HIT], 'Slot B': [B_HIT]}, folders=(FOLDER_A, FOLDER_B), cloud=(),
                         gates={'Slot A': gate_a})
    releaser = threading.Timer(0.2, gate_a.set)
    releaser.start()
    try:
        result = src._collect_prescrape_results_sequential()
    finally:
        gate_a.set()
        releaser.cancel()
    # Slot B wins the race; Slot A is left running, unstopped, and lands in prescrape_sources later.
    assert result == [B_HIT]
    assert any('rung hit rank=6 winner=folders results=1' in m for m in _timing(logged))
    for t in src.prescrape_threads:
        t.join(timeout=2)
    assert A_HIT in src.prescrape_sources  # the losing slot kept running and still landed its result


def test_budget_out_before_start_leaves_the_rung_eligible_for_the_full_scrape(switches, logged, monkeypatch):
    import modules.sources as sources_mod
    src = _bare_sources({'rd_cloud': []}, folders=(), cloud=(RD, AD))
    clock = {'t': time.time()}
    monkeypatch.setattr(sources_mod.time, 'time', lambda: clock['t'])

    def _wait_for_rung(rung_threads, started, rank, remaining_budget, autoplay_only):
        for t in rung_threads:
            t.join(timeout=2)
        clock['t'] += 100  # blow the shared budget so the next rung never starts (risk: no #104 credit for it)
        return False
    src._wait_for_rung = _wait_for_rung
    result = src._collect_prescrape_results_sequential()
    assert result == []
    assert src.ran == ['rd_cloud']
    assert 'rd_cloud' in src.remove_scrapers  # started and missed: must not be re-run by the full scrape
    assert 'ad_cloud' not in src.remove_scrapers  # never started: stays eligible for the full scrape (#104)
    assert any('rung budget_out_before_start rank=9' in m for m in _timing(logged))


def test_prescrape_sequential_off_never_calls_the_ladder(switches, monkeypatch):
    switches['sequential'] = False
    src = _bare_sources({'rd_cloud': [RD_HIT]}, folders=(), cloud=(RD,))
    def _boom():
        raise AssertionError('ladder must not run when prescrape.sequential is off')
    src._collect_prescrape_results_sequential = _boom
    src.prescrape = True
    src.folder_info = []
    src.prescrape_scrapers = []
    src.append_folder_scrapers = lambda current_list: None
    src.internal_sources = lambda prescrape=False, cloud_early=False: []
    assert src.collect_prescrape_results() == []


def test_background_never_calls_the_ladder_even_when_sequential_is_on(switches):
    src = _bare_sources({'rd_cloud': [RD_HIT]}, folders=(), cloud=(RD,), background=True)
    def _boom():
        raise AssertionError('background prep must not use the ladder (#3)')
    src._collect_prescrape_results_sequential = _boom
    src.threads = []
    src.internal_sources = lambda prescrape=False, cloud_early=False: []
    src.active_folders = False
    src.prescrape_scrapers = []
    assert src.collect_prescrape_results() == []


def test_progress_percent_never_goes_backwards_across_rungs(switches, logged):
    percents = []
    dialog = type('D', (), {'update_scraper': staticmethod(
        lambda sd, p720, p1080, p4k, total, line1, percent: percents.append(percent))})()
    gate_rd = threading.Event()
    src = _bare_sources({'ad_cloud': [AD_HIT]}, folders=(), cloud=(RD, AD), gates={'rd_cloud': gate_rd})
    src.sources_sd = src.sources_720p = src.sources_1080p = src.sources_4k = src.sources_total = 0
    src.progress_dialog = dialog
    releaser = threading.Timer(0.05, gate_rd.set)
    releaser.start()
    try:
        src._collect_prescrape_results_sequential()
    finally:
        gate_rd.set()
        releaser.cancel()
    assert percents == sorted(percents)


def test_foreground_poll_runs_the_real_internal_results_count(switches, logged, monkeypatch):
    """#199: _wait_for_rung's foreground poll calls the real _process_internal_results, which iterates
    internal_scrapers. Only scrapers_dialog assigned that before, so every foreground ladder play raised
    AttributeError on its first poll, and the open progress dialog then froze Kodi. The fixture above
    stubs _process_internal_results, which hid it."""
    import json
    props = {'redlight.internal_results.rd_cloud': json.dumps([RD_HIT])}
    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: props.get(key, ''))
    monkeypatch.setattr(kodi_utils, 'set_property', lambda key, value: props.__setitem__(key, value))
    src = _bare_sources({'rd_cloud': [RD_HIT]}, folders=(), cloud=(RD, AD))
    del src._process_internal_results  # use the real method, not the fixture's stub
    counted = []
    src._sources_quality_count = lambda sources: counted.extend(sources)
    assert src._collect_prescrape_results_sequential() == [RD_HIT]
    assert src.internal_scrapers == ['rd_cloud']
    assert counted == [RD_HIT]
    assert props['redlight.internal_results.rd_cloud'] == 'checked'


def test_fallback_pool_retries_a_slower_slots_result_before_the_full_scrape(switches, monkeypatch):
    src = object.__new__(Sources)
    src.autoplay, src.background = True, False
    src.cloud_prescrape_autoplay = True
    src._last_cloud_autoplay_results = [A_HIT]
    src.prescrape_sources = [A_HIT, B_HIT]  # B_HIT arrived from the losing slot after the pick
    src._resolve_failure = None
    src._kill_progress_dialog = lambda join_timeout=None: None
    src._prepare_cloud_autoplay_resolve = lambda: None
    src._user_cancelled_resolve = lambda: False
    monkeypatch.setattr(settings, 'autoplay_prescrape', lambda provider: True)
    monkeypatch.setattr(settings, 'cloud_queue_fallthrough', lambda: True)
    played = []
    src.play_file = lambda results: played.append(results)
    src.playback_failed_action()
    assert played == [[B_HIT]]
    assert src._prescrape_fallback_pool_done is True
    assert src._last_cloud_autoplay_results == [A_HIT, B_HIT]


def test_fallback_pool_only_retries_once_then_falls_through_to_full_scrape(switches, monkeypatch):
    src = object.__new__(Sources)
    src.autoplay, src.background = True, False
    src.cloud_prescrape_autoplay = True
    src._last_cloud_autoplay_results = [A_HIT, B_HIT]
    src._prescrape_fallback_pool_done = True  # the one retry already happened
    src.prescrape_sources = [A_HIT, B_HIT]
    src._resolve_failure = None
    src._kill_progress_dialog = lambda join_timeout=None: None
    src._user_cancelled_resolve = lambda: False
    monkeypatch.setattr(settings, 'autoplay_prescrape', lambda provider: True)
    monkeypatch.setattr(settings, 'cloud_queue_fallthrough', lambda: True)
    got_sources_called = []
    src.get_sources = lambda: got_sources_called.append(True)
    src.playback_failed_action()
    assert got_sources_called == [True]
    assert src.prescrape is False and src.prescrape_sources == []
