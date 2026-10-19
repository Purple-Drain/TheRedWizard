# -*- coding: utf-8 -*-
"""#270: a failed intro lookup is not cached as "no intro", an empty answer is rechecked daily for a
new episode, and the log says which case it was."""
import datetime

import requests

from apis import intro_skip_api as api


class FakeCache(object):
    def __init__(self): self.store, self.hours = {}, {}
    def get(self, key): return self.store.get(key)
    def set(self, key, value, expiration=None): self.store[key], self.hours[key] = value, expiration


class Response(object):
    def __init__(self, status=200, data=None): self.status_code, self._data = status, data or {}
    def json(self): return self._data


def _setup(monkeypatch, behaviour):
    cache = FakeCache()
    monkeypatch.setattr(api, 'main_cache', cache)
    monkeypatch.setattr(api.requests, 'get', behaviour)
    api._status.clear()
    return cache


def _intro(cache):
    return cache.store.get(api._intro_cache_key('1668', 'tt1', 1, 1))


def test_timeout_is_not_cached_and_is_named(monkeypatch):
    def boom(*a, **k): raise requests.exceptions.Timeout()
    cache = _setup(monkeypatch, boom)
    assert api.resolve_intro_segment('1668', 'tt1', 1, 1) is None
    assert cache.store == {}
    assert api.lookup_status('1668', 'tt1', 1, 1) == 'api error (timeout, timeout)'


def test_server_error_is_not_cached(monkeypatch):
    cache = _setup(monkeypatch, lambda *a, **k: Response(503))
    assert api.resolve_intro_segment('1668', 'tt1', 1, 1) is None
    assert cache.store == {}
    assert 'HTTP 503' in api.lookup_status('1668', 'tt1', 1, 1)


def test_a_real_empty_answer_is_cached_for_a_day_on_a_new_episode(monkeypatch):
    cache = _setup(monkeypatch, lambda *a, **k: Response(200, {}))
    today = datetime.date.today().isoformat()
    assert api.resolve_intro_segment('1668', 'tt1', 1, 1, premiered=today) is None
    assert _intro(cache) == ''
    assert cache.hours[api._intro_cache_key('1668', 'tt1', 1, 1)] == 24
    assert api.lookup_status('1668', 'tt1', 1, 1) == 'api miss'


def test_a_404_counts_as_no_data_not_a_failure(monkeypatch):
    cache = _setup(monkeypatch, lambda *a, **k: Response(404))
    assert api.resolve_intro_segment('1668', 'tt1', 1, 1) is None
    assert _intro(cache) == ''


def test_an_old_episode_keeps_the_long_cache_life(monkeypatch):
    cache = _setup(monkeypatch, lambda *a, **k: Response(200, {}))
    assert api.resolve_intro_segment('1668', 'tt1', 1, 1, premiered='1996-01-01') is None
    assert cache.hours[api._intro_cache_key('1668', 'tt1', 1, 1)] == 168


def test_unknown_air_date_is_treated_as_new(monkeypatch):
    cache = _setup(monkeypatch, lambda *a, **k: Response(200, {}))
    api.resolve_intro_segment('1668', 'tt1', 1, 1, premiered='')
    assert cache.hours[api._intro_cache_key('1668', 'tt1', 1, 1)] == 24


def test_a_found_segment_keeps_the_long_cache_life(monkeypatch):
    data = {'intro': [{'start_ms': 30000, 'end_ms': 75000}]}
    cache = _setup(monkeypatch, lambda *a, **k: Response(200, data))
    segment = api.resolve_intro_segment('1668', 'tt1', 1, 1)
    assert segment['start_sec'] == 30 and segment['end_sec'] == 75
    assert cache.hours[api._intro_cache_key('1668', 'tt1', 1, 1)] == 168
    assert api.lookup_status('1668', 'tt1', 1, 1) == 'found'


def test_credits_failure_is_not_cached_but_an_empty_answer_is(monkeypatch):
    cache = _setup(monkeypatch, lambda *a, **k: Response(500))
    assert api.resolve_credits_start_sec('1668', 'tt1', 1, 1) is None
    assert cache.store == {}
    cache = _setup(monkeypatch, lambda *a, **k: Response(200, {}))
    assert api.resolve_credits_start_sec('1668', 'tt1', 1, 1) is None
    assert cache.store == {api._outro_cache_key('1668', 'tt1', 1, 1): ''}
