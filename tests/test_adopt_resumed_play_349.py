# -*- coding: utf-8 -*-
"""#349: an adopted (queued hand-off) play that resumes past 60 s never became fresh.

On 21.11 13:11 Seinfeld S06E16 The Beard was adopted and resumed at 1176 s. The #330 guard counts a
read as the previous file's until one at or under 60 s is seen, so all reads were ignored: the stop
read was 0.0%, nothing was marked watched, and no next episode followed. A position that advances
with the clock is the live file."""
from modules import player as player_mod
from modules.player import RedLightPlayer


def _player(monkeypatch, clock):
    monkeypatch.setattr(player_mod.time, 'time', lambda: clock[0])
    player = object.__new__(RedLightPlayer)
    player._adopted_at, player._adopt_fresh = clock[0], False
    player._log_nextep = lambda *a: None
    return player


def test_a_resumed_file_becomes_fresh_once_its_position_advances(monkeypatch):
    clock = [1000.0]
    player = _player(monkeypatch, clock)
    assert player._adopt_stale_read(1176.0)
    clock[0] += 1
    assert player._adopt_stale_read(1177.0)
    clock[0] += 3
    assert not player._adopt_stale_read(1180.0)
    assert player._adopt_fresh


def test_a_frozen_previous_file_position_stays_stale(monkeypatch):
    clock = [1000.0]
    player = _player(monkeypatch, clock)
    for _ in range(10):
        assert player._adopt_stale_read(1307.4)
        clock[0] += 1
    assert not player._adopt_fresh


def test_previous_position_then_lower_resume_position_then_advance(monkeypatch):
    clock = [1000.0]
    player = _player(monkeypatch, clock)
    assert player._adopt_stale_read(1307.4)
    clock[0] += 4
    assert player._adopt_stale_read(1176.0)
    clock[0] += 4
    assert not player._adopt_stale_read(1180.0)


def test_a_read_under_a_minute_is_still_fresh_at_once(monkeypatch):
    clock = [1000.0]
    player = _player(monkeypatch, clock)
    assert not player._adopt_stale_read(2.0)
    assert player._adopt_fresh
