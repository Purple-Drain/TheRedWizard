# -*- coding: utf-8 -*-
"""MDBList OAuth token refresh (#197).

MDBList access tokens last 30 days. Before this change nothing used the stored refresh token, so once the
token expired every call returned 401 and manual watched/unwatched marks showed "Error". These tests drive
call_mdblist against a fake session: no network, and never the live refresh token (a real refresh could
rotate the pair the devices hold).
"""
import threading
import time

import pytest

from apis import mdblist_api as m
from caches import settings_cache as sc


class FakeResponse:
	def __init__(self, status_code, body):
		self.status_code = status_code
		self._body = body
		self.headers = {'Content-Type': 'application/json'}

	@property
	def ok(self): return 200 <= self.status_code < 400

	def json(self): return self._body


class FakeSession:
	"""request() answers from the queue of API responses; post() answers the token endpoint."""
	def __init__(self, api_responses, refresh_response=None, refresh_delay=0):
		self.api_responses = list(api_responses)
		self.refresh_response = refresh_response
		self.refresh_delay = refresh_delay
		self.requests, self.posts = [], []
		self.lock = threading.Lock()

	def request(self, method, url, params=None, json=None, headers=None, timeout=None):
		with self.lock:
			self.requests.append(dict(headers or {}))
			return self.api_responses.pop(0) if len(self.api_responses) > 1 else self.api_responses[0]

	def post(self, url, data=None, timeout=None):
		with self.lock: self.posts.append(dict(data or {}))
		if self.refresh_delay: time.sleep(self.refresh_delay)
		return self.refresh_response


@pytest.fixture
def store(monkeypatch):
	values = {'mdblist.token': 'old-access', 'mdblist.refresh': 'old-refresh', 'mdblist.expires': '0'}
	notices = []
	props = {}
	monkeypatch.setattr(m, '_mdblist_token', lambda: values['mdblist.token'])
	monkeypatch.setattr(m, 'get_setting', lambda key, fallback='': values.get(key.replace('redlight.', ''), fallback))
	monkeypatch.setattr(m, 'set_setting', lambda key, value: values.__setitem__(key, value))
	monkeypatch.setattr(m.settings, 'mdblist_client', lambda: 'client-id')
	monkeypatch.setattr(sc.settings_cache, 'clear_db_cache', lambda: None)
	monkeypatch.setattr(m.kodi_utils, 'notification', lambda line, *a, **k: notices.append(line))
	monkeypatch.setattr(m.kodi_utils, 'logger', lambda *a, **k: None)
	monkeypatch.setattr(m.kodi_utils, 'get_property', lambda key: props.get(key, ''))
	monkeypatch.setattr(m.kodi_utils, 'set_property', lambda key, value: props.__setitem__(key, value))
	values['notices'] = notices
	return values


def use_session(monkeypatch, fake):
	monkeypatch.setattr(m, 'session', fake)
	return fake


NEW_PAIR = FakeResponse(200, {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 2592000})
EXPIRED_403 = FakeResponse(403, {'error': 'OAuth token has expired'})


def test_401_refreshes_once_and_retries_with_the_new_token(monkeypatch, store):
	fake = use_session(monkeypatch, FakeSession([FakeResponse(401, {}), FakeResponse(200, {'removed': {'episodes': 1}})], NEW_PAIR))
	assert m.call_mdblist('sync/watched/remove', json_data={}, method='post') == {'removed': {'episodes': 1}}
	assert fake.posts == [{'grant_type': 'refresh_token', 'refresh_token': 'old-refresh', 'client_id': 'client-id'}]
	assert [r['Authorization'] for r in fake.requests] == ['Bearer old-access', 'Bearer new-access']
	assert store['mdblist.token'] == 'new-access' and store['mdblist.refresh'] == 'new-refresh'
	assert int(store['mdblist.expires']) > time.time() + 2591000


def test_403_saying_expired_also_refreshes(monkeypatch, store):
	fake = use_session(monkeypatch, FakeSession([EXPIRED_403, FakeResponse(200, {'user_id': 1})], NEW_PAIR))
	assert m.call_mdblist('user') == {'user_id': 1}
	assert len(fake.posts) == 1


def test_other_403_does_not_spend_the_refresh_token(monkeypatch, store):
	fake = use_session(monkeypatch, FakeSession([FakeResponse(403, {'error': 'insufficient scope'})], NEW_PAIR))
	assert m.call_mdblist('lists/user') is None
	assert fake.posts == [] and store['notices'] == []


def test_retry_is_capped_at_one(monkeypatch, store):
	fake = use_session(monkeypatch, FakeSession([FakeResponse(401, {})], NEW_PAIR))
	assert m.call_mdblist('sync/last_activities') is None
	assert len(fake.posts) == 1 and len(fake.requests) == 2


def test_rejected_refresh_keeps_tokens_and_asks_for_reauth_once(monkeypatch, store):
	fake = use_session(monkeypatch, FakeSession([FakeResponse(401, {})], FakeResponse(400, {'error': 'invalid_grant'})))
	assert m.call_mdblist('sync/watched/remove', json_data={}, method='post') is None
	assert m.call_mdblist('sync/last_activities') is None
	assert store['mdblist.token'] == 'old-access' and store['mdblist.refresh'] == 'old-refresh'
	assert len(store['notices']) == 1 and 'authorise MDBList again' in store['notices'][0]
	assert len(fake.posts) == 2


def test_rejected_refresh_still_retries_when_another_process_already_refreshed(monkeypatch, store):
	def other_process_wins(url, data=None, timeout=None):
		store['mdblist.token'] = 'from-other-process'
		return FakeResponse(400, {'error': 'invalid_grant'})
	fake = use_session(monkeypatch, FakeSession([FakeResponse(401, {}), FakeResponse(200, {'ok': True})]))
	monkeypatch.setattr(fake, 'post', other_process_wins)
	assert m.call_mdblist('user') == {'ok': True}
	assert fake.requests[-1]['Authorization'] == 'Bearer from-other-process'
	assert store['notices'] == []


def test_parallel_401s_refresh_only_once(monkeypatch, store):
	fake = use_session(monkeypatch, FakeSession([FakeResponse(401, {})], NEW_PAIR, refresh_delay=0.2))
	# After the refresh, any request carrying the new token succeeds.
	def request(method, url, params=None, json=None, headers=None, timeout=None):
		with fake.lock: fake.requests.append(dict(headers))
		return FakeResponse(200, {'ok': True}) if headers['Authorization'] == 'Bearer new-access' else FakeResponse(401, {})
	monkeypatch.setattr(fake, 'request', request)
	results = []
	threads = [threading.Thread(target=lambda: results.append(m.call_mdblist('sync/last_activities'))) for _ in range(4)]
	for t in threads: t.start()
	for t in threads: t.join()
	assert results == [{'ok': True}] * 4
	assert len(fake.posts) == 1


def test_api_key_mode_never_refreshes(monkeypatch, store):
	store['mdblist.refresh'] = '0'
	fake = use_session(monkeypatch, FakeSession([FakeResponse(401, {})], NEW_PAIR))
	assert m.call_mdblist('user') is None
	assert fake.posts == [] and store['notices'] == []


def test_refresh_without_a_new_refresh_token_keeps_the_old_one(monkeypatch, store):
	fake = use_session(monkeypatch, FakeSession([FakeResponse(401, {}), FakeResponse(200, {'ok': True})],
		FakeResponse(200, {'access_token': 'new-access', 'expires_in': 60})))
	assert m.call_mdblist('user') == {'ok': True}
	assert store['mdblist.refresh'] == 'old-refresh'


@pytest.mark.parametrize('expires_offset, refreshes', [(-10, True), (3600, True), (5 * 86400, False), (None, False)])
def test_monitor_refreshes_ahead_of_expiry(monkeypatch, store, expires_offset, refreshes):
	store['mdblist.expires'] = '0' if expires_offset is None else str(int(time.time()) + expires_offset)
	fake = use_session(monkeypatch, FakeSession([FakeResponse(200, {})], NEW_PAIR))
	m.mdblist_refresh_if_due()
	assert (len(fake.posts) == 1) is refreshes
	assert (store['mdblist.token'] == 'new-access') is refreshes
