# -*- coding: utf-8 -*-
"""#282: a credits marker from a shorter cut of the episode is re-anchored to the end of the file
playing, so the next-episode prompt fires at the credits, not minutes early. Parks and Recreation
S04E20: introdb.app holds the broadcast cut's credits (1262 to 1268 s); the Shield played a 1562 s
Director's Cut and the prompt came up 305 s before the end."""
from apis import intro_skip_api as api


class FakeCache(object):
    def __init__(self): self.store = {}
    def get(self, key): return self.store.get(key)
    def set(self, key, value, expiration=None): self.store[key] = value


class Response(object):
    def __init__(self, status=200, data=None): self.status_code, self._data = status, data or {}
    def json(self): return self._data


BROADCAST = {'start_sec': 1262, 'end_sec': 1268}
INTRODB_APP = {'outro': {'start_sec': 1262, 'end_sec': 1268, 'start_ms': 1262000, 'end_ms': 1268000}}


def _setup(monkeypatch, behaviour):
    cache = FakeCache()
    monkeypatch.setattr(api, 'main_cache', cache)
    monkeypatch.setattr(api.requests, 'get', behaviour)
    return cache


def test_marker_that_fits_the_file_is_untouched():
    assert api.credits_start_for_duration(BROADCAST, 1274) == (1262.0, None)


def test_short_tail_after_the_credits_is_still_a_fit():
    assert api.credits_start_for_duration(BROADCAST, 1268 + api._CUT_MISMATCH_GAP_SEC) == (1262.0, None)
    assert api.credits_start_for_duration(BROADCAST, 1269 + api._CUT_MISMATCH_GAP_SEC)[1] == 1262.0


def test_longer_cut_gets_the_marker_re_anchored_to_the_end():
    start, shifted_from = api.credits_start_for_duration(BROADCAST, 1562)
    assert (start, shifted_from) == (1556.0, 1262.0)
    assert 1562 - start == 1268 - 1262  # credits length kept


def test_marker_without_an_end_fits_when_the_file_ends_soon_after():
    # TheIntroDB credits carry end_ms null: measured from the start with the wider gap.
    assert api.credits_start_for_duration({'start_sec': 1262, 'end_sec': None}, 1274) == (1262.0, None)
    assert api.credits_start_for_duration({'start_sec': 1262, 'end_sec': None}, 1262 + api._CUT_MISMATCH_GAP_NO_END_SEC) == (1262.0, None)


def test_marker_without_an_end_on_a_longer_cut_is_re_anchored_to_the_end():
    # S04E21 Producer's Cut, 1531 s, marker 1262 s with no end: the prompt fell at 274 s.
    assert api.credits_start_for_duration({'start_sec': 1262, 'end_sec': None}, 1531) == (1531.0, 1262.0)


def test_no_marker_and_no_duration():
    assert api.credits_start_for_duration(None, 1562) == (None, None)
    assert api.credits_start_for_duration(BROADCAST, None) == (1262.0, None)


def test_one_cached_marker_serves_both_cuts(monkeypatch):
    cache = _setup(monkeypatch, lambda *a, **k: Response(200, INTRODB_APP))
    assert api.resolve_credits_start_sec('', 'tt1266020', 4, 20, 1562) == 1556.0
    # #283 keys the entry by the file's length; the fit still happens at use time.
    assert cache.store[api._outro_cache_key('', 'tt1266020', 4, 20, 1562)] == {'start_sec': 1262.0, 'end_sec': 1268.0}
    assert api.resolve_credits_start_sec('', 'tt1266020', 4, 20, 1274) == 1262.0


def test_a_marker_cached_before_pd129_is_fetched_again(monkeypatch):
    # S04E21 was prefetched during S04E20's play, before pd.129, as a bare number with no end.
    cache = _setup(monkeypatch, lambda *a, **k: Response(200, INTRODB_APP))
    cache.store[api._outro_cache_key('', 'tt1266020', 4, 21)] = 1262.0
    assert api.resolve_credits_segment('', 'tt1266020', 4, 21) == {'start_sec': 1262.0, 'end_sec': 1268.0}
    assert api.resolve_credits_start_sec('', 'tt1266020', 4, 21, 1531) == 1525.0


def test_a_marker_cached_before_pd129_is_still_guarded_when_the_refetch_fails(monkeypatch):
    cache = _setup(monkeypatch, lambda *a, **k: Response(503))
    cache.store[api._outro_cache_key('', 'tt1', 1, 1)] = 1262.0
    assert api.resolve_credits_segment('', 'tt1', 1, 1) is None
    assert cache.store[api._outro_cache_key('', 'tt1', 1, 1)] == 1262.0  # a failed refetch keeps the old value


def test_theintrodb_credits_keep_their_null_end(monkeypatch):
    _setup(monkeypatch, lambda *a, **k: Response(200, {'credits': [{'start_ms': 1321000, 'end_ms': None}]}))
    segment = api.resolve_credits_segment('1668', '', 4, 9, 1334)
    assert (segment['start_sec'], segment['end_sec']) == (1321.0, None)
