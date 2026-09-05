# -*- coding: utf-8 -*-
"""#94: an rd_cloud file that answers 503 hoster_unavailable (error_code 19) on unrestrict/link is cured
by reinserting the torrent in Debrid Media Manager's order (add the magnet from the hash, select the same
file ids, wait for downloaded, only then delete the old copy) and retrying the unrestrict once. One attempt
per torrent per session, a notification either way, gated by rd.reinsert_on_hoster_unavailable (default on).

The RD calls are scripted on a subclass so the order of the five steps is asserted, not just the result.
"""
import types

import pytest

import apis.real_debrid_api as rd
import modules.sources as sources

HOSTER_UNAVAILABLE = {'error': 'hoster_unavailable', 'error_code': 19}
HASH = 'ABCDEF0123456789ABCDEF0123456789ABCDEF01'
OLD_LINKS = ['https://real-debrid.com/d/OLD1', 'https://real-debrid.com/d/OLD2']
NEW_LINKS = ['https://real-debrid.com/d/NEW1', 'https://real-debrid.com/d/NEW2']
MAGNET = 'magnet:?xt=urn:btih:' + HASH.lower()


def torrent_info(torrent_id, links, status='downloaded'):
    return {'id': torrent_id, 'hash': HASH, 'filename': 'Extras (2005) Season 1-2 S01-S02 + Extras (576p DVD x265 HEVC)',
            'status': status, 'progress': 100 if status == 'downloaded' else 0,
            'files': [{'id': 1, 'path': '/S01E01.mkv', 'bytes': 1, 'selected': 1},
                      {'id': 2, 'path': '/sample.txt', 'bytes': 1, 'selected': 0},
                      {'id': 3, 'path': '/S01E02.mkv', 'bytes': 1, 'selected': 1}],
            'links': links}


class FakeRD(rd.RealDebridAPI):
    """Scripted Real-Debrid: records every call, answers unrestrict per link and torrents/info per id."""

    def __init__(self, new_status='downloaded', add_result=None, new_links=NEW_LINKS):
        self.token = 'token'
        self.calls = []
        self.unrestrict = {OLD_LINKS[0]: HOSTER_UNAVAILABLE, OLD_LINKS[1]: HOSTER_UNAVAILABLE,
                           NEW_LINKS[0]: {'download': 'https://cdn.real-debrid.com/NEW1.mkv', 'id': 'dl1'},
                           NEW_LINKS[1]: {'download': 'https://cdn.real-debrid.com/NEW2.mkv', 'id': 'dl2'}}
        # new_status may be a sequence: one status per torrents/info/NEW call, the last one repeating.
        self.new_statuses = list(new_status) if isinstance(new_status, (list, tuple)) else [new_status]
        self.infos = {'OLD': torrent_info('OLD', OLD_LINKS)}
        self.new_links = new_links
        self.add_result = {'id': 'NEW', 'uri': MAGNET} if add_result is None else add_result

    def _post(self, url, post_data, _retry=True):
        self.calls.append(('post', url, post_data))
        if url == 'unrestrict/link': return self.unrestrict.get(post_data['link'], {'error': 'unknown_ressource', 'error_code': 7})
        if url == 'torrents/addMagnet': return self.add_result
        if url.startswith('torrents/selectFiles/'): return None
        raise AssertionError('unexpected POST %s' % url)

    def _get(self, url, _retry=True):
        self.calls.append(('get', url))
        if url == 'torrents/info/NEW':
            status = self.new_statuses.pop(0) if len(self.new_statuses) > 1 else self.new_statuses[0]
            return torrent_info('NEW', self.new_links, status)
        if url.startswith('torrents/info/'): return self.infos.get(url.rsplit('/', 1)[1], {'error': 'unknown_ressource', 'error_code': 7})
        if url.startswith('torrents?'): return [{'id': 'OLD', 'hash': HASH, 'status': 'downloaded', 'links': OLD_LINKS}]
        raise AssertionError('unexpected GET %s' % url)

    def delete_torrent(self, folder_id):
        self.calls.append(('delete', folder_id))

    def clear_cache(self, clear_hashes=True):
        self.calls.append(('clear_cache', clear_hashes))

    def rd_free_active_slot(self):
        pass

    def names(self):
        return [c[0] if c[0] != 'post' else c[1] for c in self.calls]

    def posts(self, url):
        return [c[2] for c in self.calls if c[0] == 'post' and c[1] == url]


class Clock:
    def __init__(self): self.now = 1000.0
    def time(self): return self.now
    def sleep(self, ms): self.now += ms / 1000.0


@pytest.fixture(autouse=True)
def session(monkeypatch):
    """Fresh per-session state, no Kodi toasts/log, a fake clock so the wait deadline is deterministic."""
    rd._reinserted_torrents.clear(); rd._reinserted_links.clear()
    clock, toasts = Clock(), []
    monkeypatch.setattr(rd, 'time', types.SimpleNamespace(time=clock.time))
    monkeypatch.setattr(rd, 'sleep', clock.sleep)
    monkeypatch.setattr(rd, 'notification', lambda text, *a, **k: toasts.append(text))
    monkeypatch.setattr(rd, 'logger', lambda *a, **k: None)
    monkeypatch.setattr(rd, 'get_setting', lambda key, fallback='': fallback)
    yield types.SimpleNamespace(clock=clock, toasts=toasts)
    rd._reinserted_torrents.clear(); rd._reinserted_links.clear()


# ---------------------------------------------------------------- step 1: keep the error code

def test_unrestrict_error_keeps_error_and_code():
    assert rd.RealDebridAPI.unrestrict_error(HOSTER_UNAVAILABLE) == {'error': 'hoster_unavailable', 'error_code': 19}
    assert rd.RealDebridAPI.unrestrict_error({'error': 'odd', 'error_code': 'n/a'}) == {'error': 'odd', 'error_code': None}
    assert rd.RealDebridAPI.unrestrict_error({'download': 'https://cdn/x.mkv'}) is None
    assert rd.RealDebridAPI.unrestrict_error(None) is None
    assert rd.RealDebridAPI.unrestrict_error('<html>502</html>') is None


def test_unrestrict_link_details_returns_the_error():
    api = FakeRD()
    assert api._unrestrict_link_details(OLD_LINKS[0]) == (None, None, {'error': 'hoster_unavailable', 'error_code': 19})
    assert api._unrestrict_link_details(NEW_LINKS[0]) == ('https://cdn.real-debrid.com/NEW1.mkv', 'dl1', None)


def test_unrestrict_link_public_result_unchanged():
    api = FakeRD()
    assert api.unrestrict_link(NEW_LINKS[0]) == 'https://cdn.real-debrid.com/NEW1.mkv'
    assert api.unrestrict_link(OLD_LINKS[0]) is None
    assert 'torrents/addMagnet' not in api.names(), 'plain unrestrict_link must never reinsert'


# ---------------------------------------------------------------- steps 2-5: DMM order, then retry

def test_reinserts_in_dmm_order_then_retries_the_same_file(session):
    api = FakeRD()
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') == 'https://cdn.real-debrid.com/NEW1.mkv'
    assert api.names() == [
        'unrestrict/link',              # the 503
        'get',                          # torrents/info/OLD: hash + selected ids
        'torrents/addMagnet',           # 1. add the magnet from the hash
        'clear_cache',                  # (add_torrent_select drops the cloud cache)
        'torrents/selectFiles/NEW',     # 2. same file ids
        'get',                          # 3. torrents/info/NEW: downloaded
        'delete',                       # 4. only then delete the old copy
        'clear_cache',
        'unrestrict/link',              # 5. retry the same file on the new copy
    ]
    assert api.posts('torrents/addMagnet') == [{'magnet': MAGNET}]
    assert api.posts('torrents/selectFiles/NEW') == [{'files': '1,3'}]
    assert api.posts('unrestrict/link') == [{'link': OLD_LINKS[0]}, {'link': NEW_LINKS[0]}]
    assert ('delete', 'OLD') in api.calls and ('delete', 'NEW') not in api.calls
    assert session.toasts and 'reinserted' in session.toasts[-1]
    assert rd._reinserted_torrents == {HASH.lower()}


def test_other_files_of_the_reinserted_torrent_use_the_new_links(session):
    api = FakeRD()
    api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD')
    calls_before = len(api.calls)
    # A result list built before the reinsert still carries the old torrent's links (the next episode of
    # the same season pack); those map straight to the new copy, no second reinsert.
    assert api.unrestrict_cloud_link(OLD_LINKS[1], 'OLD') == 'https://cdn.real-debrid.com/NEW2.mkv'
    assert api.calls[calls_before:] == [('post', 'unrestrict/link', {'link': NEW_LINKS[1]})]


def test_torrent_is_found_by_link_when_the_item_carries_no_folder_id():
    api = FakeRD()
    assert api.unrestrict_cloud_link(OLD_LINKS[1]) == 'https://cdn.real-debrid.com/NEW2.mkv'
    assert ('get', 'torrents?limit=500') in api.calls
    assert api.posts('torrents/selectFiles/NEW') == [{'files': '1,3'}]


# ---------------------------------------------------------------- keep the old torrent when RD has lost the data

def test_not_downloaded_by_the_deadline_keeps_the_old_torrent(session):
    api = FakeRD(new_status='downloading')
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') is None
    assert ('delete', 'NEW') in api.calls and ('delete', 'OLD') not in api.calls
    assert api.posts('unrestrict/link') == [{'link': OLD_LINKS[0]}], 'no retry without a downloaded copy'
    assert session.clock.now - 1000.0 >= rd.REINSERT_WAIT_SECONDS
    assert 'old torrent kept' in session.toasts[-1]


def test_dead_new_copy_stops_without_waiting_out_the_deadline(session):
    api = FakeRD(new_status='magnet_error')
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') is None
    assert ('delete', 'NEW') in api.calls and ('delete', 'OLD') not in api.calls
    assert session.clock.now - 1000.0 < rd.REINSERT_WAIT_SECONDS


def test_downloaded_within_the_deadline_after_a_short_wait(session):
    api = FakeRD(new_status=['queued', 'downloading', 'downloaded'])
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') == 'https://cdn.real-debrid.com/NEW1.mkv'
    assert session.clock.now - 1000.0 == 2.0
    assert ('delete', 'OLD') in api.calls and ('delete', 'NEW') not in api.calls


def test_waiting_files_selection_is_reselected_once():
    api = FakeRD(new_status=['waiting_files_selection', 'waiting_files_selection', 'downloaded'])
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') == 'https://cdn.real-debrid.com/NEW1.mkv'
    assert api.posts('torrents/selectFiles/NEW') == [{'files': '1,3'}, {'files': '1,3'}]


def test_add_magnet_refusal_leaves_everything_in_place(session):
    api = FakeRD(add_result={'error': 'too_many_requests', 'error_code': 34})
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') is None
    assert not [c for c in api.calls if c[0] == 'delete']
    assert 'could not re-add' in session.toasts[-1]


def test_link_count_mismatch_keeps_the_old_torrent():
    api = FakeRD(new_links=NEW_LINKS[:1])
    assert api.unrestrict_cloud_link(OLD_LINKS[1], 'OLD') is None
    assert ('delete', 'NEW') in api.calls and ('delete', 'OLD') not in api.calls


# ---------------------------------------------------------------- once per torrent per session, and the gates

def test_one_attempt_per_torrent_per_session():
    FakeRD(new_status='downloading').unrestrict_cloud_link(OLD_LINKS[0], 'OLD')
    api = FakeRD()
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') is None
    assert 'torrents/addMagnet' not in api.names()
    assert not [c for c in api.calls if c[0] == 'delete']


def test_setting_off_disables_the_reinsert(monkeypatch):
    monkeypatch.setattr(rd, 'get_setting', lambda key, fallback='': 'false' if key == 'redlight.rd.reinsert_on_hoster_unavailable' else fallback)
    api = FakeRD()
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') is None
    assert api.names() == ['unrestrict/link']


def test_setting_defaults_to_on():
    entry = [i for i in rd_settings() if i['setting_id'] == 'rd.reinsert_on_hoster_unavailable']
    assert entry and entry[0]['setting_type'] == 'boolean' and entry[0]['setting_default'] == 'true'


def rd_settings():
    from caches.settings_cache import default_settings
    return default_settings()


def test_other_error_codes_do_not_reinsert():
    api = FakeRD()
    api.unrestrict[OLD_LINKS[0]] = {'error': 'bad_token', 'error_code': 8}
    assert api.unrestrict_cloud_link(OLD_LINKS[0], 'OLD') is None
    assert api.names() == ['unrestrict/link']


def test_unmappable_link_is_not_reinserted():
    api = FakeRD()
    api.unrestrict['https://real-debrid.com/d/STRANGER'] = HOSTER_UNAVAILABLE
    assert api.unrestrict_cloud_link('https://real-debrid.com/d/STRANGER', 'OLD') is None
    assert 'torrents/addMagnet' not in api.names() and rd._reinserted_torrents == set()


# ---------------------------------------------------------------- wiring: resolve_internal hands rd_cloud items to the helper

def test_resolve_internal_routes_rd_cloud_through_the_helper(monkeypatch):
    seen = []

    class Api:
        def unrestrict_cloud_link(self, link, torrent_id=None):
            seen.append((link, torrent_id)); return 'https://cdn.real-debrid.com/x.mkv'
        def unrestrict_link(self, link):
            raise AssertionError('rd_cloud must not take the plain unrestrict path')

    obj = sources.Sources.__new__(sources.Sources)
    monkeypatch.setattr(obj, 'debrid_importer', lambda provider: Api, raising=False)
    assert obj.resolve_internal('rd_cloud', OLD_LINKS[0], '', False, None, 'OLD') == 'https://cdn.real-debrid.com/x.mkv'
    assert seen == [(OLD_LINKS[0], 'OLD')]
