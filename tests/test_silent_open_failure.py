# -*- coding: utf-8 -*-
"""#190: a stream that opens (Kodi confirms a real duration and fullscreen video, #115) but
never advances past 0s before Kodi closes it again -- a curl read failure straight after
OpenFile is the repro'd case -- used to be dropped silently by abnormal_playback_end's
curr<=0 guard, so the user saw nothing: no error, no dialog, just back on Redlight's list.

These tests pin the pure decision (silent_open_failure) and the player-level wiring in
_note_abnormal_end that notifies the user exactly for that case, without touching any
existing abnormal_playback_end behaviour (real curr_time progress still goes through the
unchanged stall/resume path tested in test_stall_resume.py).
"""
import pytest

from modules import player as player_mod
from modules.player import RedLightPlayer, silent_open_failure, _STALL_CALLBACK_WAIT_MS


# --- silent_open_failure (pure) -----------------------------------------------------------

def test_zero_curr_on_a_real_duration_is_a_silent_failure():
    assert silent_open_failure(0, 1293)


@pytest.mark.parametrize('curr', [None, '', 0, 0.0, -5])
def test_any_non_positive_curr_counts(curr):
    assert silent_open_failure(curr, 1293)


def test_real_progress_is_not_a_silent_failure():
    # abnormal_playback_end already owns this case; silent_open_failure must not overlap it.
    assert not silent_open_failure(812, 1293)


@pytest.mark.parametrize('flag', ['user_stopped', 'superseded', 'cancelled', 'media_marked'])
def test_any_deliberate_end_is_not_a_silent_failure(flag):
    assert not silent_open_failure(0, 1293, **{flag: True})


@pytest.mark.parametrize('total', [None, '', 0, 45, 'abc'])
def test_no_real_duration_is_not_a_silent_failure(total):
    # Matches abnormal_playback_end's own total<60 guard: no valid duration means Kodi never
    # really told us anything, so there is nothing to notify about.
    assert not silent_open_failure(0, total)


def test_string_times_are_accepted():
    assert silent_open_failure('0', '1293.0')


# --- _note_abnormal_end wiring ------------------------------------------------------------

def _opened_but_silent_player(monkeypatch, callback_at_ms=None, callback='ended'):
    """A player Kodi confirmed had opened with a 1293 s duration, but that never advanced
    past 0s (#190's shape) before Kodi's own callback lands callback_at_ms later."""
    player = RedLightPlayer()
    player.onPlayBackStarted()
    player.is_generic, player.cancel_all_playback = False, False
    player.curr_time, player.total_time, player.playing_filename = 0.0, 1293.0, 'The Office UK S01E01.mkv'
    player._resolve_cancelled = lambda: False
    player.isPlayingVideo = lambda: False
    clock = {'ms': 0}

    def fake_sleep(ms):
        clock['ms'] += ms
        if callback_at_ms is not None and clock['ms'] >= callback_at_ms:
            if callback == 'stopped': player.onPlayBackStopped()
            elif callback == 'ended': player.onPlayBackEnded()
            else: player.onPlayBackError()

    logged, notified = [], []
    monkeypatch.setattr(player_mod.ku, 'sleep', fake_sleep)
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: logged.append(a))
    monkeypatch.setattr(player_mod.ku, 'notification', lambda *a, **k: notified.append((a, k)))
    return player, clock, logged, notified


def test_ended_with_zero_progress_notifies_and_does_not_resume(monkeypatch):
    player, _, logged, notified = _opened_but_silent_player(monkeypatch, callback_at_ms=300, callback='ended')
    player._note_abnormal_end(False, False)
    assert player.stall_position is None            # #190 is a failed open, not a stall to resume
    assert player.end_outcome == 'silent_open_failure'
    assert len(notified) == 1
    assert any('#190' in str(a) for a in logged)


def test_error_with_zero_progress_notifies(monkeypatch):
    player, _, _, notified = _opened_but_silent_player(monkeypatch, callback_at_ms=300, callback='error')
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'silent_open_failure'
    assert len(notified) == 1


def test_stop_with_zero_progress_does_not_notify(monkeypatch):
    # A genuine user Stop before anything played is not a failure to report.
    player, _, _, notified = _opened_but_silent_player(monkeypatch, callback_at_ms=300, callback='stopped')
    player._note_abnormal_end(False, False)
    assert notified == []
    assert player.end_outcome != 'silent_open_failure'


def test_no_callback_at_all_still_does_not_notify(monkeypatch):
    # #190's own repro DID see a Kodi callback (the file closed); a truly silent close with
    # no callback at all stays exactly as before -- logged, not notified (test_stall_resume.py
    # covers the equivalent real-progress case).
    player, clock, logged, notified = _opened_but_silent_player(monkeypatch, callback_at_ms=None)
    player._note_abnormal_end(False, False)
    assert notified == []
    assert clock['ms'] >= _STALL_CALLBACK_WAIT_MS


def test_notification_failure_does_not_break_note_abnormal_end(monkeypatch):
    player, _, _, _ = _opened_but_silent_player(monkeypatch, callback_at_ms=300, callback='ended')
    monkeypatch.setattr(player_mod.ku, 'notification', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no GUI')))
    player._note_abnormal_end(False, False)  # must not raise
    assert player.end_outcome == 'silent_open_failure'


def test_real_progress_case_is_unaffected(monkeypatch):
    # Sanity check that this change is additive: a stall with real curr_time still resumes
    # exactly as test_stall_resume.py already verifies, never taking the new branch.
    player = RedLightPlayer()
    player.onPlayBackStarted()
    player.is_generic, player.cancel_all_playback = False, False
    player.curr_time, player.total_time, player.playing_filename = 1010.0, 1293.0, 'Daria S01E11.mkv'
    player._resolve_cancelled = lambda: False
    player.isPlayingVideo = lambda: False
    notified = []
    monkeypatch.setattr(player_mod.ku, 'sleep', lambda ms: player.onPlayBackEnded() if ms else None)
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    monkeypatch.setattr(player_mod.ku, 'notification', lambda *a, **k: notified.append(a))
    player._note_abnormal_end(False, False)
    assert player.stall_position == (1010.0, 1293.0)
    assert notified == []
