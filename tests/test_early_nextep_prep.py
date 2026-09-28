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


def test_stage1_due_twenty_seconds_in(monkeypatch):
    assert _player(monkeypatch, curr=25.0)._early_prep_due(1200, 85) is True


def test_not_before_twenty_seconds(monkeypatch):
    assert _player(monkeypatch, curr=10.0)._early_prep_due(1200, 85) is False


def test_intro_prompt_does_not_gate_stage1(monkeypatch):
    """The monitor tick itself waits while the Skip Intro modal is up, so no extra gate."""
    p = _player(monkeypatch, curr=25.0)
    p._intro_skip_done = False
    assert p._early_prep_due(1200, 85) is True


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


class Props(dict):
    def get_property(self, k): return self.get(k, '')
    def set_property(self, k, v): self[k] = v
    def clear_property(self, k): self.pop(k, None)


def test_stage2_runs_after_a_stage1_miss_a_minute_in(monkeypatch):
    props = Props({'redlight.nextep_stage1_miss': 'true'})
    for n in ('get_property', 'set_property', 'clear_property'): monkeypatch.setattr(player_mod.ku, n, getattr(props, n))
    p = _player(monkeypatch, curr=45.0)
    p._nextep_early_prep, p.total_time, p.start_prep = True, 1300.0, 85
    p._log_nextep = lambda *a: None
    calls = []
    p._schedule_next_ep = lambda: calls.append(p._early_stage2)
    p._maybe_early_stage2()
    assert calls == []
    p.curr_time = 65.0
    p._maybe_early_stage2()
    assert calls == [True] and p._nextep_prep_attempted is False


def test_stage1_asks_sources_for_folders_only(monkeypatch):
    from modules.sources import Sources
    s = object.__new__(Sources)
    s.background, s.nextep_settings = True, {'early_prep': True, 'folders_only': True}
    assert s._nextep_folders_only() is True
    s.nextep_settings = {'early_prep': True, 'folders_only': False}
    assert s._nextep_folders_only() is False
