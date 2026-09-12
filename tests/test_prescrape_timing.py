# -*- coding: utf-8 -*-
"""Per-scraper prescrape finish times in kodi.log (#149).

Measuring #149 on 12.09.26 found no way to read a folder hit's own latency from kodi.log: only the
prescrape start (ScrapePrescrape) and the join-deadline overrun were logged. Sources is built with
object.__new__ to skip __init__, as in test_sources_thread_join.py.
"""
import re
import threading
import time

import pytest

from modules import kodi_utils
from modules.sources import Sources


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: lines.append((heading, message)))
    return lines


def _bare_sources(**attrs):
    obj = object.__new__(Sources)
    obj.tmdb_id, obj.media_type, obj.season, obj.episode = 2131, 'episode', 1, 11
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


def test_timed_prescrape_logs_the_scraper_name_elapsed_time_and_result_count(logged, monkeypatch):
    src = _bare_sources()
    monkeypatch.setattr(src, 'activate_providers', lambda module_type, function, prescrape: [{'id': 'a'}])
    thread = threading.Thread(target=src._timed_prescrape, args=('folders', None, time.time() - 0.4), name='My Library')
    thread.start(); thread.join()
    assert len(logged) == 1
    heading, message = logged[0]
    assert heading == 'ScrapePrescrapeTiming'
    assert re.fullmatch(r'tmdb=2131 S01E11 My Library done 0\.\d\ds results=1', message), message


def test_timed_prescrape_passes_prescrape_true(logged, monkeypatch):
    seen = []
    src = _bare_sources()
    monkeypatch.setattr(src, 'activate_providers', lambda module_type, function, prescrape: seen.append((module_type, function, prescrape)))
    src._timed_prescrape('internal', 'fn', time.time())
    assert seen == [('internal', 'fn', True)]
    assert logged[0][1].endswith('results=0')


def test_timed_prescrape_still_logs_when_the_scraper_raises(logged, monkeypatch):
    def _boom(module_type, function, prescrape): raise RuntimeError('scraper failed')
    src = _bare_sources()
    monkeypatch.setattr(src, 'activate_providers', _boom)
    with pytest.raises(RuntimeError):
        src._timed_prescrape('internal', None, time.time())
    assert len(logged) == 1 and ' done ' in logged[0][1]


def test_wait_ended_names_scrapers_still_running(logged):
    _bare_sources()._log_prescrape_timing(time.time() - 3, 'wait ended', 1, ['rd_cloud', 'tb_cloud'])
    assert re.fullmatch(r'tmdb=2131 S01E11 wait ended 3\.\d\ds results=1 still_running=rd_cloud,tb_cloud', logged[0][1]), logged[0][1]


def test_movie_label_has_no_episode_and_no_still_running_when_all_finished(logged):
    _bare_sources(tmdb_id=603, media_type='movie')._log_prescrape_timing(time.time(), 'wait ended', 0, [])
    assert re.fullmatch(r'tmdb=603 wait ended 0\.\d\ds results=0', logged[0][1]), logged[0][1]


def test_logging_never_raises(monkeypatch):
    def _broken(heading, message): raise OSError('log write failed')
    monkeypatch.setattr(kodi_utils, 'logger', _broken)
    _bare_sources()._log_prescrape_timing(time.time(), 'wait ended', 0)
