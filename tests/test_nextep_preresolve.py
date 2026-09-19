# -*- coding: utf-8 -*-
"""#1: pre-resolve the top autoplay_nextep candidate during the background prep, so the visible
handoff wait at the actual episode switch is the pickle load, not a fresh resolve. Covers the
stash payload (item 2 of the spec), the TTL/key gate as a pure helper (item 3's precondition),
and the fallback ordering when the pre-resolved url turns out to be dead (item 4).
"""
import modules.kodi_utils as kodi_utils
import modules.sources as sources
from modules.sources import Sources, NEXTEP_PRERESOLVE_TTL_SEC


ITEM = {'name': 'Show.S01E02.mkv', 'hash': 'abcd1234', 'scrape_provider': 'rd_cloud', 'cache_provider': ''}
FOLDER_ITEM = {'name': 'Show.S01E02.mkv', 'hash': '', 'url_dl': '/mnt/show/s01e02.mkv', 'scrape_provider': 'folders', 'cache_provider': ''}


def _sources():
    return object.__new__(Sources)


# -- item_key / TTL gate (pure helpers) --------------------------------------------------

def test_item_key_uses_name_and_hash():
    assert sources.nextep_preresolve_item_key(ITEM) == 'Show.S01E02.mkv|abcd1234'


def test_item_key_falls_back_to_url_dl_when_no_hash():
    assert sources.nextep_preresolve_item_key(FOLDER_ITEM) == 'Show.S01E02.mkv|/mnt/show/s01e02.mkv'


def test_item_key_none_for_falsy_item():
    assert sources.nextep_preresolve_item_key(None) is None
    assert sources.nextep_preresolve_item_key({}) is None


def test_is_fresh_true_within_ttl_and_matching_key():
    preresolved = {'url': 'http://x', 'item_key': sources.nextep_preresolve_item_key(ITEM), 'resolved_at': 1000.0}
    assert sources.nextep_preresolve_is_fresh(preresolved, ITEM, now=1000.0 + NEXTEP_PRERESOLVE_TTL_SEC) is True


def test_is_fresh_false_past_ttl():
    preresolved = {'url': 'http://x', 'item_key': sources.nextep_preresolve_item_key(ITEM), 'resolved_at': 1000.0}
    assert sources.nextep_preresolve_is_fresh(preresolved, ITEM, now=1000.0 + NEXTEP_PRERESOLVE_TTL_SEC + 1) is False


def test_is_fresh_false_on_key_mismatch():
    other = dict(ITEM, hash='ffff0000')
    preresolved = {'url': 'http://x', 'item_key': sources.nextep_preresolve_item_key(ITEM), 'resolved_at': 1000.0}
    assert sources.nextep_preresolve_is_fresh(preresolved, other, now=1000.0) is False


def test_is_fresh_false_on_missing_preresolved():
    assert sources.nextep_preresolve_is_fresh(None, ITEM) is False
    assert sources.nextep_preresolve_is_fresh({}, ITEM) is False


def test_is_fresh_false_on_missing_item():
    preresolved = {'url': 'http://x', 'item_key': sources.nextep_preresolve_item_key(ITEM), 'resolved_at': 1000.0}
    assert sources.nextep_preresolve_is_fresh(preresolved, None) is False


# -- stash payload -------------------------------------------------------------------------

def test_stash_carries_preresolved_dict(monkeypatch):
    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: '')
    monkeypatch.setattr(kodi_utils, 'set_property', lambda *a: None)
    monkeypatch.setattr(kodi_utils, 'clear_property', lambda *a: None)
    monkeypatch.setattr(sources, 'nextep_autoplay_cancelled', lambda: False)
    monkeypatch.setattr(sources, '_nextep_stash_key', lambda meta: 'k')
    sources._NEXTEP_AUTOPLAY_STASH.clear()
    preresolved = {'url': 'http://x', 'item_key': 'a|b', 'resolved_at': 123.0}
    assert sources.stash_nextep_autoplay_results([ITEM], {'title': 'Show'}, {}, {}, preresolved=preresolved)
    assert sources._NEXTEP_AUTOPLAY_STASH['k']['preresolved'] == preresolved
    sources._NEXTEP_AUTOPLAY_STASH.clear()


def test_stash_preresolved_absent_by_default(monkeypatch):
    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: '')
    monkeypatch.setattr(kodi_utils, 'set_property', lambda *a: None)
    monkeypatch.setattr(kodi_utils, 'clear_property', lambda *a: None)
    monkeypatch.setattr(sources, 'nextep_autoplay_cancelled', lambda: False)
    monkeypatch.setattr(sources, '_nextep_stash_key', lambda meta: 'k')
    sources._NEXTEP_AUTOPLAY_STASH.clear()
    assert sources.stash_nextep_autoplay_results([ITEM], {'title': 'Show'}, {}, {})
    assert sources._NEXTEP_AUTOPLAY_STASH['k']['preresolved'] is None
    sources._NEXTEP_AUTOPLAY_STASH.clear()


def test_preresolved_survives_the_persisted_pickle(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, '_nextep_play_stash_path', lambda: str(tmp_path / 'nextep_play_stash.pkl'))
    preresolved = {'url': 'http://x', 'item_key': 'a|b', 'resolved_at': 123.0}
    assert sources.persist_nextep_play_stash({'results': [], 'preresolved': preresolved})
    assert sources.consume_persisted_nextep_play_stash()['preresolved'] == preresolved


# -- _preresolve_nextep_candidate (background prep side) -----------------------------------

def test_preresolve_skipped_when_setting_off(monkeypatch):
    obj = _sources()
    monkeypatch.setattr(sources.settings, 'autoplay_preresolve_next_episode', lambda: False)
    assert obj._preresolve_nextep_candidate([ITEM]) is None


def test_preresolve_skipped_for_folders_provider(monkeypatch):
    obj = _sources()
    monkeypatch.setattr(sources.settings, 'autoplay_preresolve_next_episode', lambda: True)
    monkeypatch.setattr(kodi_utils, 'logger', lambda *a: None)
    assert obj._preresolve_nextep_candidate([FOLDER_ITEM]) is None


def test_preresolve_skipped_when_resolve_fails(monkeypatch):
    obj = _sources()
    monkeypatch.setattr(sources.settings, 'autoplay_preresolve_next_episode', lambda: True)
    monkeypatch.setattr(kodi_utils, 'logger', lambda *a: None)
    obj._resolve_sources_wait = lambda item, meta=None: None
    assert obj._preresolve_nextep_candidate([ITEM]) is None


def test_preresolve_returns_url_and_key_on_success(monkeypatch):
    obj = _sources()
    monkeypatch.setattr(sources.settings, 'autoplay_preresolve_next_episode', lambda: True)
    monkeypatch.setattr(kodi_utils, 'logger', lambda *a: None)
    obj._resolve_sources_wait = lambda item, meta=None: 'http://resolved'
    result = obj._preresolve_nextep_candidate([ITEM])
    assert result['url'] == 'http://resolved'
    assert result['item_key'] == sources.nextep_preresolve_item_key(ITEM)
    assert isinstance(result['resolved_at'], float)


# -- fallback ordering in the play_file bypass ----------------------------------------------

def test_try_preresolved_play_success_sets_playback_successful(monkeypatch):
    obj = _sources()
    obj.playback_successful = None
    monkeypatch.setattr(obj, '_user_cancelled_resolve', lambda: False)
    monkeypatch.setattr(obj, '_ensure_play_headers', lambda url, item: url)
    monkeypatch.setattr(obj, '_set_play_mime_hint', lambda item, url: None)
    monkeypatch.setattr(obj, '_cleanup_offcloud_resolved_url', lambda item, url: None)
    monkeypatch.setattr(obj, '_cleanup_rd_resolved_url', lambda item, url: None)
    monkeypatch.setattr(kodi_utils, 'logger', lambda *a: None)

    class FakePlayer:
        def run(inner_self, url, src):
            src.playback_successful = True

    monkeypatch.setattr(sources, 'RedLightPlayer', FakePlayer)
    preresolved = {'url': 'http://x', 'item_key': 'a|b', 'resolved_at': 1000.0}
    ok, url = obj._try_preresolved_play(ITEM, preresolved, monitor=None)
    assert ok is True
    assert url == 'http://x'


def test_try_preresolved_play_failure_falls_back_cleanly(monkeypatch):
    """A dead pre-resolved url: playback_successful ends False, the helper reports failure and
    leaves self.playback_successful reset to None so the caller's normal per-item loop (which
    resolves the same item fresh) runs exactly as it would on a normal resolve failure."""
    obj = _sources()
    obj.playback_successful = None
    monkeypatch.setattr(obj, '_user_cancelled_resolve', lambda: False)
    monkeypatch.setattr(obj, '_ensure_play_headers', lambda url, item: url)
    monkeypatch.setattr(obj, '_set_play_mime_hint', lambda item, url: None)
    monkeypatch.setattr(obj, '_cleanup_offcloud_resolved_url', lambda item, url: None)
    monkeypatch.setattr(obj, '_cleanup_rd_resolved_url', lambda item, url: None)
    monkeypatch.setattr(kodi_utils, 'logger', lambda *a: None)

    class FakePlayer:
        def run(inner_self, url, src):
            src.playback_successful = False

    monkeypatch.setattr(sources, 'RedLightPlayer', FakePlayer)
    preresolved = {'url': 'http://dead', 'item_key': 'a|b', 'resolved_at': 1000.0}
    ok, url = obj._try_preresolved_play(ITEM, preresolved, monitor=None)
    assert ok is False
    assert url is None
    assert obj.playback_successful is None


def test_try_preresolved_play_cancelled_before_start(monkeypatch):
    obj = _sources()
    obj.playback_successful = None
    obj.cancel_all_playback = False
    obj._resolve_user_cancelled = False
    monkeypatch.setattr(obj, '_user_cancelled_resolve', lambda: True)
    ok, url = obj._try_preresolved_play(ITEM, {'url': 'http://x', 'item_key': 'a|b', 'resolved_at': 1000.0}, monitor=None)
    assert ok is False
    assert url is None
    assert obj._resolve_user_cancelled is True
    assert obj.cancel_all_playback is True
