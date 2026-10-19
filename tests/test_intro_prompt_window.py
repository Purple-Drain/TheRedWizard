# -*- coding: utf-8 -*-
"""#272: the Skip Intro prompt opens a few seconds before the intro starts and stays until the intro
ends. It runs on its own thread so the player's monitor is never blocked."""
from modules import player as player_mod
from modules import settings as st
from modules.player import RedLightPlayer


def _player(monkeypatch, curr, start=60, end=110):
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a, **k: None)
    monkeypatch.setattr(st, 'skip_intro_needs_prompt', lambda play_type: True)
    p = object.__new__(RedLightPlayer)
    p._intro_skip_active, p._intro_skip_done = True, False
    p._intro_skip_prompt_answered, p._intro_skip_approved = False, False
    p._intro_skip_segment = {'start_sec': start, 'end_sec': end, 'source': 'introdb'}
    p.curr_time, p.playback_percent, p.total_time = curr, 0, 1500
    p.sources_object = type('S', (), {'play_type': ''})()
    p._player_is_active = lambda: True
    p._try_intro_skip_chapters = lambda: None
    p._intro_skip_position_settled = lambda: True
    p._log_intro_skip = lambda *a: None
    p.prompts = []
    p._prompt_intro_skip = lambda s, e: p.prompts.append((s, e))
    return p


def test_prompt_waits_until_just_before_the_intro(monkeypatch):
    p = _player(monkeypatch, curr=40)
    p._maybe_apply_intro_skip()
    assert p.prompts == []


def test_prompt_opens_a_few_seconds_before_the_intro_and_only_once(monkeypatch):
    p = _player(monkeypatch, curr=56)
    p._maybe_apply_intro_skip()
    p._maybe_apply_intro_skip()
    assert p.prompts == [(60, 110)]
    assert not p._intro_skip_approved


def test_prompt_opens_when_joining_in_the_middle_of_the_intro(monkeypatch):
    p = _player(monkeypatch, curr=90)
    p._maybe_apply_intro_skip()
    assert p.prompts == [(60, 110)]


def test_prompt_at_the_start_of_an_episode_when_the_intro_begins_there(monkeypatch):
    p = _player(monkeypatch, curr=2, start=3, end=50)
    p._maybe_apply_intro_skip()
    assert p.prompts == [(3, 50)]


def _run_prompt(monkeypatch, choice):
    p = _player(monkeypatch, curr=56)
    del p._prompt_intro_skip
    p._spawn = lambda target, **k: target()
    import windows.base_window as bw
    monkeypatch.setattr(bw, 'open_window', lambda *a, **k: choice)
    p._prompt_intro_skip(60, 110)
    return p


def test_yes_approves_the_skip_without_ending_the_intro_skip(monkeypatch):
    p = _run_prompt(monkeypatch, True)
    assert p._intro_skip_approved and not p._intro_skip_done


def test_no_or_an_unanswered_prompt_closes_the_intro_skip(monkeypatch):
    for choice in (False, None):
        p = _run_prompt(monkeypatch, choice)
        assert p._intro_skip_done and not p._intro_skip_approved


def test_prompt_stays_up_through_the_intro_and_closes_when_it_ends():
    from windows.playback_notifications import IntroSkipPrompt
    prompt = object.__new__(IntroSkipPrompt)
    prompt.closed, prompt.timed_out, prompt.start_sec, prompt.end_sec = False, False, 60.0, 110.0
    times, shown, closed = iter([50.0, 62.0, 109.0, 111.0]), [], []
    prompt.setProperty = lambda k, v: shown.append(v)
    prompt.sleep = lambda ms: None
    prompt.close = lambda: closed.append(1)
    prompt.player = type('P', (), {'isPlayingVideo': lambda self: True, 'isPlaying': lambda self: True,
                                   'getTime': lambda self: next(times)})()
    prompt.monitor()
    assert shown == ['  |  starts in 10 s', '  |  ends in 0:48', '  |  ends in 0:01']
    assert prompt.timed_out is True and closed == [1]
