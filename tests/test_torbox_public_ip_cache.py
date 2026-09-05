# -*- coding: utf-8 -*-
"""#116: a failed ipify lookup used to go uncached, so every TorBox resolve while
api.ipify.org is unreachable paid a fresh 2s timeout. _cached_public_ip now holds a
failed lookup for 60s (still shorter than the 1800s success cache) before retrying."""
import apis.torbox_api as tb


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _reset_cache():
    tb._public_ip_cache['ip'] = ''
    tb._public_ip_cache['expires'] = 0


def test_failed_lookup_is_cached_for_60_seconds(monkeypatch):
    _reset_cache()
    clock = FakeClock()
    monkeypatch.setattr(tb.time, 'time', clock.time)
    calls = []

    def failing_get(url, timeout):
        calls.append(url)
        raise Exception('unreachable')

    monkeypatch.setattr(tb.requests, 'get', failing_get)

    assert tb._cached_public_ip() == ''
    assert len(calls) == 1

    # Within the 60s negative-cache window: no further ipify round-trip.
    clock.advance(30)
    assert tb._cached_public_ip() == ''
    assert len(calls) == 1


def test_failed_lookup_retries_after_60_seconds(monkeypatch):
    _reset_cache()
    clock = FakeClock()
    monkeypatch.setattr(tb.time, 'time', clock.time)
    calls = []

    def failing_get(url, timeout):
        calls.append(url)
        raise Exception('unreachable')

    monkeypatch.setattr(tb.requests, 'get', failing_get)

    assert tb._cached_public_ip() == ''
    assert len(calls) == 1

    clock.advance(61)

    class Resp:
        text = '203.0.113.9'

    monkeypatch.setattr(tb.requests, 'get', lambda url, timeout: (calls.append(url), Resp())[1])
    assert tb._cached_public_ip() == '203.0.113.9'
    assert len(calls) == 2


def test_successful_lookup_still_cached_for_1800_seconds(monkeypatch):
    _reset_cache()
    clock = FakeClock()
    monkeypatch.setattr(tb.time, 'time', clock.time)
    calls = []

    class Resp:
        text = '198.51.100.4'

    monkeypatch.setattr(tb.requests, 'get', lambda url, timeout: (calls.append(url), Resp())[1])

    assert tb._cached_public_ip() == '198.51.100.4'
    assert len(calls) == 1

    clock.advance(1799)
    assert tb._cached_public_ip() == '198.51.100.4'
    assert len(calls) == 1
