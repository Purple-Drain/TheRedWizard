# -*- coding: utf-8 -*-
"""#1 early prep: the next episode is prepared about a minute in, once the play is settled."""
import time

from modules import player as player_mod
from modules.player import RedLightPlayer


def _player(monkeypatch, curr=90.0, early=True):
    monkeypatch.setattr(player_mod.st, 'nextep_prep_early', lambda: early)
    p = object.__new__(RedLightPlayer)
    p.autoplay_nextep, p.curr_time = True, curr
    p._intro_skip_active, p._intro_skip_done, p._last_seek = True, True, None
    return p


def test_due_a_minute_in(monkeypatch):
    assert _player(monkeypatch)._early_prep_due(1200, 85) is True


def test_not_before_a_minute(monkeypatch):
    assert _player(monkeypatch, curr=40.0)._early_prep_due(1200, 85) is False


def test_waits_for_the_intro_prompt(monkeypatch):
    p = _player(monkeypatch)
    p._intro_skip_done = False
    assert p._early_prep_due(1200, 85) is False


def test_waits_after_a_seek(monkeypatch):
    p = _player(monkeypatch)
    p._last_seek = (time.time() - 3, 400.0)
    assert p._early_prep_due(1200, 85) is False
    p._last_seek = (time.time() - 30, 400.0)
    assert p._early_prep_due(1200, 85) is True


def test_setting_near_the_end_keeps_the_old_timing(monkeypatch):
    assert _player(monkeypatch, early=False)._early_prep_due(1200, 85) is False


def test_autoplay_off_never_early(monkeypatch):
    p = _player(monkeypatch)
    p.autoplay_nextep = False
    assert p._early_prep_due(1200, 85) is False
