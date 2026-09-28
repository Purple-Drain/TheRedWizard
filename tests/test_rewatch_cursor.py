# -*- coding: utf-8 -*-
"""Rewatch from here (#1 C407): the local per-show cursor behind Next Episodes' rewatch mode."""
import time

import pytest

from modules import rewatch_cursor as rc


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.kodi_utils, 'addon_profile', lambda: str(tmp_path))
    monkeypatch.setattr(rc.kodi_utils, 'logger', lambda *a: None)
    return tmp_path


def test_off_by_default_a_rewatch_sets_nothing(profile):
    assert rc.on_play_started(4608, 3, 11, already_watched=True, continue_mode=False) is None
    assert rc.seed(4608) is None


def test_continue_mode_sets_and_moves_the_cursor(profile):
    assert rc.on_play_started(4608, 3, 11, True, True) == 'set'
    assert rc.seed(4608) == (3, 11, False)
    assert rc.on_play_started(4608, 3, 12, True, True) == 'moved'
    assert rc.seed(4608) == (3, 12, False)


def test_menu_cursor_moves_on_even_with_the_setting_off(profile):
    rc.set_cursor(4608, 3, 11, show_self=True)
    assert rc.seed(4608) == (3, 11, True)
    assert rc.on_play_started(4608, 3, 11, True, False) == 'moved'
    assert rc.seed(4608) == (3, 11, False)


def test_playing_an_unwatched_episode_clears_it(profile):
    rc.set_cursor(4608, 3, 21)
    assert rc.on_play_started(4608, 3, 22, already_watched=False, continue_mode=True) == 'cleared'
    assert rc.seed(4608) is None


def test_old_cursors_expire(profile):
    rc.set_cursor(4608, 3, 11, now=time.time() - rc.CURSOR_MAX_AGE_SEC - 10)
    assert rc.seed(4608) is None


def test_state_token_changes_with_the_cursor(profile):
    before = rc.state_token()
    rc.set_cursor(4608, 3, 11)
    assert rc.state_token() != before


def test_specials_and_bad_input_are_ignored(profile):
    assert rc.on_play_started(4608, 0, 1, True, True) is None
    assert rc.on_play_started(4608, None, 1, True, True) is None
    assert rc.load() == {}


def test_menu_can_restore_an_automatic_cursor_quietly(profile, monkeypatch):
    shown = []
    monkeypatch.setattr(rc.kodi_utils, 'notification', lambda *a, **k: shown.append(a))
    monkeypatch.setattr(rc.kodi_utils, 'kodi_refresh', lambda: None)
    rc.menu({'action': 'set', 'tmdb_id': '1668', 'season': '2', 'episode': '5', 'show_self': 'false', 'quiet': 'true'})
    assert rc.seed(1668) == (2, 5, False) and shown == []
