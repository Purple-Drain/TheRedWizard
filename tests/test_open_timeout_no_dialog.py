# -*- coding: utf-8 -*-
"""#343: a failed open with no progress dialog (quiet start) ends at the open timeout instead of
waiting for ever (Parks 13.11.26: dead pre-resolved link, HTTP 503, 5 minutes of nothing)."""
from types import SimpleNamespace

from modules import player as player_mod
from modules.player import RedLightPlayer


class _Monitor:
    def abortRequested(self): return False


def _player(monkeypatch, playing=False, total=0.0, fullscreen=False, error_dialog=False):
    ticks = {'n': 0}

    def fake_sleep(ms):
        ticks['n'] += 1
        if ticks['n'] > 500: raise AssertionError('open loop never ended')

    def fake_visibility(cond):
        if 'okdialog' in cond: return error_dialog
        return fullscreen

    monkeypatch.setattr(player_mod.ku, 'sleep', fake_sleep)
    monkeypatch.setattr(player_mod.ku, 'hide_busy_dialog', lambda *a, **k: None)
    monkeypatch.setattr(player_mod.ku, 'close_dialog', lambda *a, **k: None)
    monkeypatch.setattr(player_mod.ku, 'get_visibility', fake_visibility)
    monkeypatch.setattr(player_mod.ku, 'get_property', lambda *a, **k: '')
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    monkeypatch.setattr(player_mod, '_PLAYBACK_OPEN_TICK_MS', 1000)
    player = object.__new__(RedLightPlayer)
    player.playback_successful = None
    player._queued_next = False
    player._cb_stopped = False
    player.kodi_monitor = _Monitor()
    player.sources_object = SimpleNamespace(progress_dialog=None, cancel_all_playback=False, _resolve_user_cancelled=False)
    player._resolve_cancelled = lambda: False
    player._playback_open_timeout_ms = lambda: 5000
    player.isPlayingVideo = lambda: playing
    player.getTotalTime = lambda: total
    return player, ticks


def test_never_opens_times_out_as_failed(monkeypatch):
    player, ticks = _player(monkeypatch)
    player.check_playback_start()
    assert player.playback_successful is False
    assert ticks['n'] == 5


def test_kodi_error_dialog_fails_at_once(monkeypatch):
    player, ticks = _player(monkeypatch, error_dialog=True)
    player.check_playback_start()
    assert player.playback_successful is False
    assert ticks['n'] <= 1


def test_playing_with_duration_still_opens(monkeypatch):
    player, _ = _player(monkeypatch, playing=True, total=1277.0, fullscreen=True)
    player.check_playback_start()
    assert player.playback_successful is True


def test_slow_open_playing_at_timeout_is_kept(monkeypatch):
    player, _ = _player(monkeypatch, playing=True, total=1277.0, fullscreen=False)
    player.check_playback_start()
    assert player.playback_successful is True
