# -*- coding: utf-8 -*-
"""#108 item 3: _get now surfaces the HTTP status on self.last_status, so
unrestrict_link's retry loop can stop on a definitive 4xx instead of burning
its full 12-attempt, 30s-deadline budget on a response that will never change."""
import apis.torbox_api as tb


def test_is_retryable_status_transport_error_retries():
    assert tb._is_retryable_status(None) is True


def test_is_retryable_status_429_retries():
    assert tb._is_retryable_status(429) is True


def test_is_retryable_status_5xx_retries():
    assert tb._is_retryable_status(500) is True
    assert tb._is_retryable_status(503) is True


def test_is_retryable_status_4xx_stops():
    assert tb._is_retryable_status(400) is False
    assert tb._is_retryable_status(401) is False
    assert tb._is_retryable_status(404) is False


def test_is_retryable_status_2xx_retries():
    # never actually seen here (a 2xx either parses to a usable url or falls
    # through the existing r.get('success') handling), but stay permissive.
    assert tb._is_retryable_status(200) is True


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


def test_unrestrict_link_stops_retrying_on_definitive_4xx(monkeypatch):
    api = tb.TorBoxAPI()
    api.token = 'tok'
    monkeypatch.setattr(tb, '_cached_public_ip', lambda: '')

    calls = []

    def fake_get(url, data=None):
        calls.append(1)
        api.last_status = 401
        return {'success': False, 'detail': 'invalid token'}

    monkeypatch.setattr(api, '_get', fake_get)
    monkeypatch.setattr(tb, 'sleep', lambda ms: None)

    result = api.unrestrict_link('123,456')

    assert result is None
    assert len(calls) == 1
    assert api.last_unrestrict_error == {'error': 'requestdl returned 401'}


def test_unrestrict_link_keeps_retrying_on_5xx_until_deadline(monkeypatch):
    api = tb.TorBoxAPI()
    api.token = 'tok'
    monkeypatch.setattr(tb, '_cached_public_ip', lambda: '')

    clock = _FakeClock()
    monkeypatch.setattr(tb.time, 'time', clock.time)

    calls = []

    def fake_get(url, data=None):
        calls.append(1)
        api.last_status = 503
        return None

    def fake_sleep(ms):
        clock.now += ms / 1000.0

    monkeypatch.setattr(api, '_get', fake_get)
    monkeypatch.setattr(tb, 'sleep', fake_sleep)

    result = api.unrestrict_link('123,456', deadline_s=3)

    assert result is None
    # kept retrying past a single attempt (unlike the 4xx case above), until the
    # short 3s deadline cut it off.
    assert len(calls) > 1
