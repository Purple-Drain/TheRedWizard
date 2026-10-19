# -*- coding: utf-8 -*-
"""#265: soft intro skip. The skip waits a couple of seconds into the intro and lands a few seconds
before its end, instead of a hard cut at both ends."""
from modules import player as player_mod
from modules import settings as st
from modules.player import RedLightPlayer


def _player(monkeypatch, curr, keep_start=2, keep_end=5, start=60, end=110):
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a, **k: None)
    values = {'redlight.intro_skip_keep_start': str(keep_start), 'redlight.intro_skip_keep_end': str(keep_end)}
    monkeypatch.setattr(st, 'get_setting', lambda k, d=None: values.get(k, d))
    monkeypatch.setattr(st, 'skip_intro_needs_prompt', lambda play_type: False)
    p = object.__new__(RedLightPlayer)
    p._intro_skip_active, p._intro_skip_done = True, False
    p._intro_skip_prompt_answered, p._intro_skip_approved = True, True
    p._intro_skip_segment = {'start_sec': start, 'end_sec': end, 'source': 'introdb'}
    p.curr_time, p.playback_percent, p.total_time = curr, 0, 1500
    p.sources_object = type('S', (), {'play_type': ''})()
    p._player_is_active = lambda: True
    p._try_intro_skip_chapters = lambda: None
    p._log_intro_skip = lambda *a: None
    p.seeks = []
    p._execute_intro_skip_seek = lambda s, e, src, target=None: p.seeks.append(target) or True
    return p


def test_defaults_keep_two_seconds_at_the_start_and_five_at_the_end(monkeypatch):
    monkeypatch.setattr(st, 'get_setting', lambda k, d=None: d)
    assert st.intro_skip_keep_start_sec() == 2
    assert st.intro_skip_keep_end_sec() == 5


def test_pad_settings_are_clamped(monkeypatch):
    monkeypatch.setattr(st, 'get_setting', lambda k, d=None: '99')
    assert st.intro_skip_keep_start_sec() == 15
    monkeypatch.setattr(st, 'get_setting', lambda k, d=None: 'junk')
    assert st.intro_skip_keep_end_sec() == 5


def test_skip_waits_into_the_intro(monkeypatch):
    p = _player(monkeypatch, curr=61)
    p._maybe_apply_intro_skip()
    assert p.seeks == [] and not p._intro_skip_done


def test_skip_fires_after_the_start_padding_and_lands_before_the_end(monkeypatch):
    p = _player(monkeypatch, curr=62.5)
    p._maybe_apply_intro_skip()
    assert p.seeks == [105] and p._intro_skip_done


def test_zero_padding_is_the_old_hard_cut(monkeypatch):
    p = _player(monkeypatch, curr=60, keep_start=0, keep_end=0)
    p._maybe_apply_intro_skip()
    assert p.seeks == [110]


def test_landing_point_never_precedes_the_intro_start(monkeypatch):
    p = _player(monkeypatch, curr=60, keep_start=0, keep_end=15, start=60, end=70)
    p._maybe_apply_intro_skip()
    assert p.seeks == [] and p._intro_skip_done


def test_a_jump_that_would_barely_move_is_not_made(monkeypatch):
    p = _player(monkeypatch, curr=104)
    p._maybe_apply_intro_skip()
    assert p.seeks == [] and p._intro_skip_done
