# -*- coding: utf-8 -*-
"""#188: staged rollout step 1 of #179's option A design (issue comment 2026-09-16 06:18).

A throwaway diagnostic, not a release: with the marker file absent (every real install, and
every test here unless explicitly set up) release_resolve_handle is exactly pd.67's behaviour,
covered already by test_resolve_handle.py and re-confirmed here. With the marker present, a
real handle is resolved True to a bundled dummy clip instead, and a throwaway RedLightPlayer
observes which of Kodi's four playback callbacks arrive for it -- the one premise nothing has
confirmed on a real device. These tests pin the logging/dispatch logic; the premise itself can
only be answered by the device log this build produces.
"""
import os

import pytest

from modules import kodi_utils, player as player_mod


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(kodi_utils.xbmcplugin, 'setResolvedUrl',
                        lambda handle, ok, listitem: seen.append(('resolve', handle, ok)), raising=False)
    return seen


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, msg: lines.append((heading, msg)))
    return lines


def _marker_present(monkeypatch, present=True):
    monkeypatch.setattr(kodi_utils, 'path_exists', lambda path: present and path == kodi_utils._RESOLVE_PROBE_MARKER)


def _fake_observer(monkeypatch, callback_at_ms=None, callback='ended', still_playing_after=False, stopped_out=None):
    """Drives resolve_probe's own wait loop via a fake ku.sleep, the same pattern
    test_stall_resume.py uses for _note_abnormal_end, so no real time passes. resolve_probe
    constructs its RedLightPlayer only once called, after this fixture returns, so
    isPlayingVideo/stop are patched onto that instance lazily, from inside fake_sleep, the
    first time it runs (the only point before resolve_probe's own checks that this test code
    gets to run)."""
    clock = {'ms': 0}
    created = []
    patched = []

    real_init = player_mod.RedLightPlayer.__init__

    def tracking_init(self):
        real_init(self)
        created.append(self)
    monkeypatch.setattr(player_mod.RedLightPlayer, '__init__', tracking_init)

    def fake_sleep(ms):
        clock['ms'] += ms
        if not created:
            return
        observer = created[0]
        if not patched:
            monkeypatch.setattr(observer, 'isPlayingVideo', lambda: still_playing_after)
            if stopped_out is not None:
                monkeypatch.setattr(observer, 'stop', lambda: stopped_out.append(True))
            patched.append(True)
        if callback_at_ms is not None and clock['ms'] >= callback_at_ms and not (observer._cb_stopped or observer._cb_ended):
            if callback == 'stopped': observer.onPlayBackStarted(); observer.onPlayBackStopped()
            elif callback == 'error': observer.onPlayBackStarted(); observer.onPlayBackError()
            else: observer.onPlayBackStarted(); observer.onAVStarted(); observer.onPlayBackEnded()

    monkeypatch.setattr(kodi_utils, 'sleep', fake_sleep)
    return clock, created


# --- release_resolve_handle dispatch -------------------------------------------------------

def test_marker_absent_is_byte_for_byte_pd67(monkeypatch, events):
    _marker_present(monkeypatch, present=False)
    assert kodi_utils.release_resolve_handle(['plugin://plugin.video.redlight/', '3', '?mode=playback.media'])
    assert events == [('resolve', 3, False)]


def test_runplugin_handle_never_checks_the_marker(monkeypatch, events):
    checked = []
    monkeypatch.setattr(kodi_utils, 'path_exists', lambda p: checked.append(p) or False)
    assert not kodi_utils.release_resolve_handle(['plugin://plugin.video.redlight/', '-1', '?mode=playback.media'])
    assert events == [] and checked == []


def test_marker_present_dispatches_to_the_probe(monkeypatch, events, logged):
    _marker_present(monkeypatch)
    clock, created = _fake_observer(monkeypatch, callback_at_ms=300, callback='ended')
    assert kodi_utils.release_resolve_handle(['plugin://plugin.video.redlight/', '9', '?mode=playback.media'])
    assert events == [('resolve', 9, True)]
    assert len(created) == 1


# --- resolve_probe itself --------------------------------------------------------------------

def test_resolves_true_with_a_playable_dummy_listitem(monkeypatch, events):
    _fake_observer(monkeypatch, callback_at_ms=300, callback='ended')
    assert kodi_utils.resolve_probe(11)
    assert events == [('resolve', 11, True)]


def test_logs_which_callbacks_arrived(monkeypatch, events, logged):
    _fake_observer(monkeypatch, callback_at_ms=300, callback='ended')
    kodi_utils.resolve_probe(11)
    assert len(logged) == 1
    heading, msg = logged[0]
    assert heading == 'ResolveProbe'
    assert 'handle=11' in msg and 'started=True' in msg and 'ended=True' in msg and 'stopped=False' in msg


def test_error_callback_is_recorded(monkeypatch, events, logged):
    _fake_observer(monkeypatch, callback_at_ms=300, callback='error')
    kodi_utils.resolve_probe(4)
    _, msg = logged[0]
    assert 'error=True' in msg


def test_stop_callback_is_recorded(monkeypatch, events, logged):
    _fake_observer(monkeypatch, callback_at_ms=300, callback='stopped')
    kodi_utils.resolve_probe(4)
    _, msg = logged[0]
    assert 'stopped=True' in msg


def test_no_callback_at_all_still_logs_and_returns(monkeypatch, events, logged):
    clock, created = _fake_observer(monkeypatch, callback_at_ms=None)
    assert kodi_utils.resolve_probe(4)
    assert clock['ms'] >= kodi_utils._RESOLVE_PROBE_WAIT_MS
    heading, msg = logged[0]
    assert 'started=False' in msg and 'ended=False' in msg and 'stopped=False' in msg


def test_still_playing_after_the_wait_is_stopped(monkeypatch, events, logged):
    stopped = []
    _fake_observer(monkeypatch, callback_at_ms=None, still_playing_after=True, stopped_out=stopped)
    kodi_utils.resolve_probe(4)
    assert stopped == [True]


def test_setresolvedurl_failure_is_swallowed(monkeypatch, logged):
    def boom(*a):
        raise RuntimeError('invalid handle')
    monkeypatch.setattr(kodi_utils.xbmcplugin, 'setResolvedUrl', boom, raising=False)
    assert not kodi_utils.resolve_probe(4)
    assert any('setResolvedUrl' in msg for _, msg in logged)


def test_dummy_asset_is_bundled_and_playable_by_extension():
    # #188 ships resources/media/resolve_probe_dummy.mp4 alongside the code; a missing or
    # empty file would resolve to nothing and never be picked up as a media file.
    path = os.path.normpath(os.path.join(
        os.path.dirname(__file__), '..', 'plugin.video.redlight', kodi_utils._RESOLVE_PROBE_DUMMY))
    assert os.path.isfile(path)
    assert os.path.getsize(path) > 0
    assert path.endswith('.mp4')
