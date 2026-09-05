# -*- coding: utf-8 -*-
"""player.py's open window and monitor() failure handling (#110, #115).

#115: check_playback_start's open window used to be an implicit ~20 s (0.26 % per 50 ms
tick). It is now the playback_open_timeout setting, and a video that is already playing
with a real duration when the window runs out is treated as opened, not stopped.

#110: an exception in monitor()'s own setup used to be reported as a user cancel, which
stopped a video that was playing fine and skipped the bookmark. The pure decision helpers
are tested directly; the monitor() path is driven with a fake sources object through the
kodi stubs.
"""
import types

import pytest

from modules import player as player_mod
from modules.player import (RedLightPlayer, open_window_expired_outcome, playback_open_percent,
                            playback_open_timeout_ms)


# --- #115: the window --------------------------------------------------------------------

def test_default_window_is_thirty_seconds():
    assert player_mod._PLAYBACK_OPEN_TIMEOUT_SEC == 30
    assert playback_open_timeout_ms(None) == 30000
    assert playback_open_timeout_ms('30') == 30000


@pytest.mark.parametrize('value, expected', [('45', 45000), (12, 12000), ('7.9', 7000), ('1', 1000)])
def test_setting_is_integer_seconds(value, expected):
    assert playback_open_timeout_ms(value) == expected


@pytest.mark.parametrize('value', ['', 'abc', '0', '-5', None, object()])
def test_bad_setting_falls_back_to_default(value):
    assert playback_open_timeout_ms(value) == 30000


def test_percent_climbs_over_the_window_like_the_old_tick():
    assert playback_open_percent(0, 30000) == 0.0
    assert playback_open_percent(15000, 30000) == 50.0
    assert playback_open_percent(30000, 30000) == 100.0
    assert playback_open_percent(45000, 30000) == 100.0
    assert playback_open_percent(0, 0) == 100.0


def test_expired_window_keeps_a_playing_video():
    assert open_window_expired_outcome(True, 1320.0) is True
    assert open_window_expired_outcome(True, '1320.0') is True


@pytest.mark.parametrize('total', ['0.0', '', 0, 0.0, None])
def test_expired_window_without_a_duration_still_fails(total):
    assert open_window_expired_outcome(True, total) is False


def test_expired_window_without_playback_still_fails():
    assert open_window_expired_outcome(False, 1320.0) is False


def _open_player(ticks_to_play, total='1320.0'):
    """A player whose Kodi starts playing after ticks_to_play loop ticks but never reaches
    fullscreen, so only the expiry branch can end the wait."""
    player = RedLightPlayer()
    player.is_generic = False
    player.playback_successful = None
    player.cancel_all_playback = False
    player.kodi_monitor = types.SimpleNamespace(abortRequested=lambda: False)
    state = {'ticks': 0, 'percent': []}
    # The resolver dialog is up during a real open; without it the loop's expiry branch is
    # never reached (pre-existing ordering, unchanged here).
    dialog = types.SimpleNamespace(skip_resolved=lambda: False, iscanceled=lambda: False,
                                   update_resolver=lambda percent=None, text=None: state['percent'].append(percent))
    player.sources_object = types.SimpleNamespace(progress_dialog=dialog, _resolve_user_cancelled=False, cancel_all_playback=False)
    def is_playing():
        state['ticks'] += 1
        return state['ticks'] > ticks_to_play
    player.isPlayingVideo = is_playing
    player.getTotalTime = lambda: total
    player._dismiss_kodi_playback_error_dialog = lambda: False
    player.safe_stop = lambda: state.__setitem__('stopped', True)
    return player, state


def test_slow_open_that_is_playing_at_expiry_is_not_stopped(monkeypatch):
    monkeypatch.setattr(player_mod.st, 'playback_open_timeout', lambda: '1')
    monkeypatch.setattr(player_mod.ku, 'sleep', lambda ms: None)
    monkeypatch.setattr(player_mod.ku, 'get_visibility', lambda cond: False)
    monkeypatch.setattr(player_mod.ku, 'get_property', lambda key: '')
    player, state = _open_player(ticks_to_play=5)
    player.check_playback_start()
    assert player.playback_successful is True
    assert 'stopped' not in state
    assert player.sources_object.cancel_all_playback is False


def test_nothing_playing_at_expiry_still_fails(monkeypatch):
    monkeypatch.setattr(player_mod.st, 'playback_open_timeout', lambda: '1')
    monkeypatch.setattr(player_mod.ku, 'sleep', lambda ms: None)
    monkeypatch.setattr(player_mod.ku, 'get_visibility', lambda cond: False)
    monkeypatch.setattr(player_mod.ku, 'get_property', lambda key: '')
    player, state = _open_player(ticks_to_play=10 ** 6)
    player.check_playback_start()
    assert player.playback_successful is False
    # 1 s window at 50 ms ticks: 20 ticks, plus the isPlayingVideo probe in _open_window_expired.
    assert state['ticks'] == 1000 // player_mod._PLAYBACK_OPEN_TICK_MS + 1
    assert state['percent'][0] == 5.0 and state['percent'][-1] == 100.0


# --- #110: monitor() setup failure -------------------------------------------------------

def _monitor_player(playing_ticks=2):
    player = RedLightPlayer()
    player.is_generic = False
    player.media_type = 'movie'
    player.playback_successful = True
    player.cancel_all_playback = False
    player.media_marked = False
    player.total_time, player.curr_time = 0.0, 0.0
    player.sources_object = types.SimpleNamespace(playback_successful=True, cancel_all_playback=False, _resolve_user_cancelled=False,
                                                  _kill_progress_dialog=lambda: None, _force_close_sources_overlay_windows=lambda: None)
    state = {'ticks': 0, 'marked': 0, 'stopped': 0}
    def is_playing():
        state['ticks'] += 1
        return state['ticks'] <= playing_ticks
    player.isPlayingVideo = is_playing
    player.getTotalTime = lambda: 1320.0
    player.getTime = lambda: 800.0
    player._owns_active_playback = lambda: True
    player._release_active_playback = lambda: None
    player.media_watched_marker = lambda force_watched=False: state.__setitem__('marked', state['marked'] + 1)
    player.safe_stop = lambda: state.__setitem__('stopped', state['stopped'] + 1)
    player.stop = player.safe_stop
    player.kill_dialog = lambda: state.__setitem__('killed', True)
    return player, state


def _raise_boom():
    raise RuntimeError('boom')


def test_setup_exception_after_open_leaves_the_player_alone_and_bookmarks(monkeypatch):
    monkeypatch.setattr(player_mod.ku, 'sleep', lambda ms: None)
    logged = []
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: logged.append(a))
    # st.stingers_show() is part of the movie setup that runs before the loop.
    monkeypatch.setattr(player_mod.st, 'stingers_show', _raise_boom)
    player, state = _monitor_player(playing_ticks=3)
    player.monitor()
    assert player.sources_object.cancel_all_playback is False
    assert player.sources_object.playback_successful is True
    assert state['stopped'] == 0
    assert state['marked'] == 1
    assert state['ticks'] >= 3, 'waited for the video to end before bookmarking'
    assert player.curr_time == 800.0 and player.total_time == 1320.0
    assert any('monitor() failed' in str(a[1]) and 'boom' in str(a[1]) for a in logged)


def test_setup_exception_before_open_is_still_a_failure(monkeypatch):
    monkeypatch.setattr(player_mod.ku, 'sleep', lambda ms: None)
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    monkeypatch.setattr(player_mod.st, 'stingers_show', _raise_boom)
    player, state = _monitor_player()
    player.playback_successful = None
    player.monitor()
    assert player.sources_object.cancel_all_playback is True
    assert player.sources_object.playback_successful is False
    assert state.get('killed') is True
    assert state['marked'] == 0
