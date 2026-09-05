# -*- coding: utf-8 -*-
"""Wall-clock caps on the two joins #112 found unbounded.

sources.Sources joined every background prescrape thread with no timeout (the foreground path
goes through the timed scrapers_dialog), and rd_cloud joined one torrents/info thread per
matching torrent, each a 20 s-capped request, with no deadline at all (tb_cloud and pm_cloud
carry min(25, max(10, results.timeout))). A slow Real-Debrid during next-episode prep overran
the pop window with nothing in kodi.log to show why.
"""
import threading
import time

import pytest

import modules.sources as sources
import modules.kodi_utils as kodi_utils
import scrapers.rd_cloud as rd_cloud


def _blocking_thread(name, gate):
    return threading.Thread(target=gate.wait, name=name)


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: lines.append((heading, message)))
    return lines


@pytest.fixture
def instance():
    s = sources.Sources.__new__(sources.Sources)
    s.progress_dialog = None
    s._scrape_user_cancelled = False
    s.threads, s.prescrape_threads = [], []
    return s


# --- sources.py: background prescrape join ---------------------------------------------------

def test_background_prescrape_join_trips_deadline_and_logs(instance, logged):
    gate = threading.Event()
    quick = threading.Thread(target=lambda: None, name='tb_cloud')
    slow = _blocking_thread('rd_cloud', gate)
    instance.prescrape_threads = [quick, slow]
    quick.start(); slow.start()
    try:
        started = time.time()
        alive = instance._join_prescrape_threads(timeout=0.3)
        elapsed = time.time() - started
    finally:
        gate.set()
        slow.join(2)
    assert alive == ['rd_cloud']
    assert elapsed < 2.0
    assert len(logged) == 1
    heading, message = logged[0]
    assert heading == 'Red Light'
    assert 'background prescrape' in message and 'rd_cloud' in message and 'deadline' in message
    assert 'tb_cloud' not in message


def test_background_prescrape_join_is_quiet_when_threads_finish(instance, logged):
    threads = [threading.Thread(target=lambda: None, name='rd_cloud'), threading.Thread(target=lambda: None, name='folders')]
    instance.prescrape_threads = threads
    for t in threads: t.start()
    assert instance._join_prescrape_threads(timeout=5) == []
    assert logged == []


def test_internal_join_uses_the_same_capped_helper(instance, logged):
    gate = threading.Event()
    slow = _blocking_thread('pm_cloud', gate)
    instance.threads = [slow]
    slow.start()
    try:
        assert instance._join_internal_threads(0.2) == ['pm_cloud']
    finally:
        gate.set()
        slow.join(2)
    assert len(logged) == 1 and 'internal scraper' in logged[0][1] and 'pm_cloud' in logged[0][1]


def test_user_cancel_stops_join_without_deadline_log(instance, logged):
    gate = threading.Event()
    slow = _blocking_thread('rd_cloud', gate)
    instance.prescrape_threads = [slow]
    instance._scrape_user_cancelled = True
    slow.start()
    try:
        started = time.time()
        alive = instance._join_prescrape_threads(timeout=5)
        assert time.time() - started < 1.0
    finally:
        gate.set()
        slow.join(2)
    assert alive == ['rd_cloud']
    assert logged == []


@pytest.mark.parametrize('setting, expected', [('20', 30), ('5', 15), ('1', 15), ('60', 35), ('bogus', 30)])
def test_background_prescrape_timeout_band(instance, monkeypatch, setting, expected):
    monkeypatch.setattr(sources, 'get_setting', lambda key, default='20': setting)
    assert instance._background_prescrape_timeout() == expected


# --- rd_cloud.py: scrape deadline -----------------------------------------------------------

class FakeRealDebrid(object):
    """Torrent list plus per-torrent info; the torrents in block_ids wait until released."""
    def __init__(self, torrents, infos, block_ids, gate):
        self.torrents, self.infos, self.block_ids, self.gate = torrents, infos, block_ids, gate
        self.info_calls = []
        self.lock = threading.Lock()

    def user_cloud(self):
        return self.torrents

    def user_cloud_info(self, torrent_id):
        with self.lock: self.info_calls.append(torrent_id)
        if torrent_id in self.block_ids: self.gate.wait()
        return self.infos[torrent_id]

    def downloads(self, fresh=False):
        return []


VIDEO_EXTENSIONS = ['.mkv', '.mp4']  # the Kodi stub's supported-media list is empty


def _movie_scraper(deadline_seconds):
    src = rd_cloud.source()
    src.extensions = VIDEO_EXTENSIONS
    src.media_type, src.year, src.season, src.episode = 'movie', 1995, None, None
    src.absolute_episode, src.title_check = None, None
    src.folder_queries = ['heat']
    src.folder_results, src.scrape_results = [], []
    src.scrape_deadline = time.time() + deadline_seconds
    return src


TORRENTS = [
    {'id': 'fast', 'filename': 'Heat.1995.1080p.BluRay', 'status': 'downloaded'},
    {'id': 'slow', 'filename': 'Heat 1995 REMUX', 'status': 'downloaded'},
    {'id': 'nameless', 'filename': '進撃の巨人', 'status': 'downloaded'},
    {'id': 'bracketed', 'filename': '[abc]', 'status': 'downloaded'},
    {'id': 'other', 'filename': 'Other.Movie.2001', 'status': 'downloaded'},
    {'id': 'pending', 'filename': 'Heat.1995.WEB', 'status': 'downloading'},
]
INFOS = {
    'fast': {'files': [{'path': '/Heat.1995.1080p.BluRay.mkv', 'selected': 1, 'bytes': 4000000000}], 'links': ['https://rd/fast']},
    'slow': {'files': [{'path': '/Heat.1995.REMUX.mkv', 'selected': 1, 'bytes': 40000000000}], 'links': ['https://rd/slow']},
}


def test_rd_cloud_join_trips_deadline_keeps_finished_results_and_logs(monkeypatch, logged):
    gate = threading.Event()
    fake = FakeRealDebrid(TORRENTS, INFOS, {'slow'}, gate)
    monkeypatch.setattr(rd_cloud, 'RealDebrid', fake)
    src = _movie_scraper(0.5)
    try:
        started = time.time()
        src._scrape_cloud()
        elapsed = time.time() - started
    finally:
        gate.set()
    assert elapsed < 2.0
    assert [i['url_link'] for i in src.scrape_results] == ['https://rd/fast']
    assert len(logged) == 1
    assert logged[0] == ('Red Light', 'rd_cloud scrape deadline reached with 1 of 2 torrent info fetches still running')


def test_rd_cloud_skips_torrents_whose_cleaned_name_is_empty(monkeypatch, logged):
    gate = threading.Event()
    gate.set()
    fake = FakeRealDebrid(TORRENTS, INFOS, set(), gate)
    monkeypatch.setattr(rd_cloud, 'RealDebrid', fake)
    src = _movie_scraper(10)
    src._scrape_cloud()
    assert sorted(fake.info_calls) == ['fast', 'slow']
    assert src.folder_results == ['fast', 'slow']
    assert sorted(i['url_link'] for i in src.scrape_results) == ['https://rd/fast', 'https://rd/slow']
    assert logged == []


def test_rd_cloud_skips_info_fetches_when_deadline_already_passed(monkeypatch, logged):
    fake = FakeRealDebrid(TORRENTS, INFOS, set(), threading.Event())
    monkeypatch.setattr(rd_cloud, 'RealDebrid', fake)
    src = _movie_scraper(-1)
    src._scrape_cloud()
    assert fake.info_calls == []
    assert src.scrape_results == []
    assert logged == [('Red Light', 'rd_cloud scrape deadline reached before the torrent list')]


@pytest.mark.parametrize('setting, expected', [('20', 20), ('5', 10), ('60', 25)])
def test_rd_cloud_deadline_band_matches_tb_cloud(monkeypatch, setting, expected):
    monkeypatch.setattr(rd_cloud, 'get_setting', lambda key, default='20': setting)
    assert rd_cloud.source()._deadline_seconds() == expected


def test_rd_cloud_results_sets_deadline_and_returns_finished_source(monkeypatch, logged):
    gate = threading.Event()
    fake = FakeRealDebrid(TORRENTS, INFOS, {'slow'}, gate)
    monkeypatch.setattr(rd_cloud, 'RealDebrid', fake)
    monkeypatch.setattr(rd_cloud, 'enabled_debrids_check', lambda name: True)
    monkeypatch.setattr(rd_cloud, 'filter_by_name', lambda name: False)
    src = rd_cloud.source()
    src.extensions = VIDEO_EXTENSIONS
    monkeypatch.setattr(src, '_deadline_seconds', lambda: 0.5)
    try:
        started = time.time()
        results = src.results({'media_type': 'movie', 'title': 'Heat', 'year': 1995, 'tmdb_id': 949, 'aliases': []})
        elapsed = time.time() - started
    finally:
        gate.set()
    assert elapsed < 2.0
    assert [i['url_dl'] for i in results] == ['https://rd/fast']
    assert results[0]['scrape_provider'] == 'rd_cloud'
    assert any('rd_cloud scrape deadline reached' in message for _, message in logged)
