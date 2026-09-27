# -*- coding: utf-8 -*-
"""#1 bug G: a stream playing without fullscreen still counts as open after a short grace."""
from modules import player as player_mod
from modules.player import RedLightPlayer


def _player(monkeypatch, fullscreen, total=1277.0):
    monkeypatch.setattr(player_mod.ku, 'get_visibility', lambda cond: fullscreen)
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    builtins = []
    monkeypatch.setattr(player_mod.ku, 'execute_builtin', lambda cmd, block=False: builtins.append(cmd))
    player = object.__new__(RedLightPlayer)
    player.playback_successful = None
    player.getTotalTime = lambda: total
    return player, builtins


def test_fullscreen_confirms_at_once(monkeypatch):
    player, builtins = _player(monkeypatch, True)
    player._confirm_open(0)
    assert player.playback_successful is True and builtins == []


def test_no_fullscreen_waits_then_brings_it_up(monkeypatch):
    player, builtins = _player(monkeypatch, False)
    player._confirm_open(1000)
    player._confirm_open(3950)
    assert player.playback_successful is None
    player._confirm_open(4000)
    assert player.playback_successful is True
    assert builtins == ['ActivateWindow(fullscreenvideo)']


def test_no_duration_is_not_open(monkeypatch):
    player, _ = _player(monkeypatch, False, total=0.0)
    for ms in (0, 5000, 10000): player._confirm_open(ms)
    assert player.playback_successful is None
