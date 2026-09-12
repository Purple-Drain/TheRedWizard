# -*- coding: utf-8 -*-
"""Wall-clock cap on scrapers/folders.py's recursive joins (sibling to #112/test_scrape_deadlines.py).

folders.py's directory-listing and subfolder-recursion joins were fully unbounded ([i.join() for
i in threads], no timeout), unlike every cloud scraper post-#112/#125 -- worse, in fact, since a
hung WebDAV/Zurg mount has no HTTP-level request timeout the way requests-based scrapers do.
"""
import threading
import time

import pytest

import scrapers.folders as folders
import modules.kodi_utils as kodi_utils


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: lines.append((heading, message)))
    return lines


@pytest.fixture
def instance():
    return folders.source('folders', 'My Library', '/mnt/zurg/shows')


# --- _deadline_seconds: same band as rd_cloud/tb_cloud/pm_cloud ------------------------------

@pytest.mark.parametrize('setting, expected', [('20', 20), ('5', 10), ('60', 25), ('unparsable', None)])
def test_deadline_band_matches_cloud_scrapers(instance, monkeypatch, setting, expected):
    monkeypatch.setattr(folders, 'get_setting', lambda key, default='20': setting)
    if expected is None:
        with pytest.raises(ValueError):
            instance._deadline_seconds()
    else:
        assert instance._deadline_seconds() == expected


# --- _join_until_deadline: same shape as rd_cloud's _join_until_deadline ---------------------

def test_join_until_deadline_trips_and_logs(instance, logged):
    gate = threading.Event()
    quick = threading.Thread(target=lambda: None, name='quick')
    slow = threading.Thread(target=gate.wait, name='slow')
    quick.start(); slow.start()
    instance.scrape_deadline = time.time() + 0.05
    try:
        abandoned = instance._join_until_deadline([quick, slow], 'listing')
    finally:
        gate.set(); slow.join()
    assert abandoned == 1
    assert logged == [('Red Light', 'folders scrape deadline reached with 1 of 2 listing threads still running')]


def test_join_until_deadline_is_quiet_when_threads_finish(instance, logged):
    threads = [threading.Thread(target=lambda: None), threading.Thread(target=lambda: None)]
    for t in threads: t.start()
    instance.scrape_deadline = time.time() + 5
    assert instance._join_until_deadline(threads, 'listing') == 0
    assert logged == []


# --- _scrape_directory: bails before a fresh listing once the deadline has passed ------------

def test_scrape_directory_skips_listing_when_deadline_already_passed(instance, logged, monkeypatch):
    calls = []
    monkeypatch.setattr(folders, 'cache_object', lambda *a, **k: calls.append(a) or [])
    instance.scrape_deadline = time.time() - 1
    instance._scrape_directory('/mnt/zurg/shows/Seinfeld', first_run=False)
    assert calls == []
    assert logged == [('Red Light', 'folders scrape deadline reached before listing /mnt/zurg/shows/Seinfeld')]


def test_scrape_directory_first_run_ignores_deadline(instance, monkeypatch):
    """first_run always attempts at least one listing, even if somehow called past deadline --
    mirrors rd_cloud's _past_deadline, which only gates stages after the first."""
    calls = []
    monkeypatch.setattr(folders, 'cache_object', lambda *a, **k: calls.append(a) or [])
    instance.scrape_deadline = time.time() - 1
    instance.title_query, instance.folder_query = 'seinfeld', ('season03',)
    instance._scrape_directory('/mnt/zurg/shows/Seinfeld', first_run=True)
    assert len(calls) == 1
