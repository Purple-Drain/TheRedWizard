# -*- coding: utf-8 -*-
"""Mid-play stall resume, the decision half (#107).

A TorBox CDN range request left hanging past curl's low-speed timeout ends playback the
way the file ending does. These tests pin the pure helpers that decide whether an end was
abnormal, whether the item is worth re-resolving, and where the reopen should start; the
Kodi-facing half (re-resolve, reopen) is reasoned about in the PR, not simulated here.
"""
import pytest

from modules.player import RedLightPlayer, abnormal_playback_end
from modules.sources import stall_resume_eligible, stall_resume_percent


# --- abnormal_playback_end -------------------------------------------------------------

def test_stall_far_from_end_is_abnormal():
    assert abnormal_playback_end(812, 1320)


def test_natural_end_is_not_abnormal():
    assert not abnormal_playback_end(1310, 1320)


def test_end_inside_min_remaining_window_is_not_abnormal():
    # 50 s left: under the 60 s guard, treated as the file ending.
    assert not abnormal_playback_end(1270, 1320)
    assert abnormal_playback_end(1259, 1320)


def test_min_remaining_is_configurable():
    assert not abnormal_playback_end(1200, 1320, min_remaining=180)
    assert abnormal_playback_end(1200, 1320, min_remaining=100)


@pytest.mark.parametrize('flag', ['user_stopped', 'superseded', 'cancelled', 'media_marked'])
def test_any_deliberate_end_is_not_abnormal(flag):
    assert not abnormal_playback_end(812, 1320, **{flag: True})


@pytest.mark.parametrize('curr, total', [
    (None, 1320), (0, 1320), (0.0, 1320), ('', 1320), ('abc', 1320),
    (812, None), (812, 0), (812, ''), (812, 'abc'),
    (30, 45),      # under a minute long: never a real stream
    (-5, 1320),
])
def test_invalid_or_degenerate_durations_are_not_abnormal(curr, total):
    assert not abnormal_playback_end(curr, total)


def test_string_times_are_accepted():
    assert abnormal_playback_end('812.5', '1320.0')


# --- player callbacks --------------------------------------------------------------------

def test_stop_before_start_is_ignored_as_stale():
    # Redlight stops the previous playback right before opening the next one; that Stopped
    # can be queued for the new player and must not read as a user Stop of the new stream.
    player = RedLightPlayer()
    player.onPlayBackStopped()
    player.onPlayBackEnded()
    assert not player._cb_stopped and not player._cb_ended


def test_stop_after_start_counts():
    player = RedLightPlayer()
    player.onPlayBackStarted()
    player.onPlayBackStopped()
    assert player._cb_stopped and not player._cb_ended


def test_av_started_also_arms_the_flags():
    player = RedLightPlayer()
    player.onAVStarted()
    player.onPlayBackEnded()
    assert player._cb_ended


def test_error_callback_is_recorded_unconditionally():
    player = RedLightPlayer()
    assert not player.playback_error
    player.onPlayBackError()
    assert player.playback_error
    assert player.stall_position is None


# --- stall_resume_eligible ---------------------------------------------------------------

@pytest.mark.parametrize('provider', ['rd_cloud', 'pm_cloud', 'ad_cloud', 'oc_cloud', 'tb_cloud'])
def test_cloud_scraper_items_are_eligible(provider):
    assert stall_resume_eligible({'scrape_provider': provider})


@pytest.mark.parametrize('item', [
    {'scrape_provider': 'external', 'cache_provider': 'Real-Debrid'},
    {'scrape_provider': 'external', 'cache_provider': 'TorBox'},
    {'scrape_provider': 'external', 'debrid': 'Premiumize.me'},
    {'scrape_provider': 'external', 'debrid': 'AllDebrid'},
    {'scrape_provider': 'external', 'debrid': 'Offcloud'},
    {'scrape_provider': 'external', 'debrid': 'torbox'},   # alias form
])
def test_external_debrid_items_are_eligible(item):
    assert stall_resume_eligible(item)


@pytest.mark.parametrize('item', [
    {'scrape_provider': 'easynews'},
    {'scrape_provider': 'folders', 'source': 'nas'},
    {'scrape_provider': 'nzb'},
    {'scrape_provider': 'aiostreams'},
    {'scrape_provider': 'external'},
    {'scrape_provider': 'external', 'debrid': ''},
    {'scrape_provider': 'external', 'debrid': 'SomethingElse'},
    {},
])
def test_everything_else_keeps_the_old_behaviour(item):
    assert not stall_resume_eligible(item)


def test_eligibility_never_raises():
    assert not stall_resume_eligible(None)


# --- stall_resume_percent ----------------------------------------------------------------

def test_reopen_starts_a_few_seconds_before_the_stall():
    assert stall_resume_percent(812, 1320) == round(807 / 1320 * 100, 2)


def test_rewind_is_configurable():
    assert stall_resume_percent(812, 1320, rewind=0) == round(812 / 1320 * 100, 2)


def test_stall_near_the_start_reopens_from_zero():
    assert stall_resume_percent(3, 1320) == 0.0


@pytest.mark.parametrize('curr, total', [(812, 0), (812, None), ('a', 'b'), (None, None)])
def test_degenerate_input_reopens_from_zero(curr, total):
    assert stall_resume_percent(curr, total) == 0.0


# --- stall_end_signal and the late-Stop order (12.09.26) ---------------------------------

from modules import player as player_mod  # noqa: E402
from modules.player import stall_end_signal, seek_to_end, _STALL_CALLBACK_WAIT_MS, _SEEK_END_WINDOW_SEC  # noqa: E402


def test_stop_wins_over_everything():
    assert stall_end_signal(True, False, False) == 'stopped'
    assert stall_end_signal(True, True, True) == 'stopped'


def test_end_or_error_is_a_stall():
    assert stall_end_signal(False, True, False) == 'stall'
    assert stall_end_signal(False, False, True) == 'stall'


def test_no_callback_is_no_signal():
    assert stall_end_signal(False, False, False) is None


def _closed_player(monkeypatch, callback_at_ms=None, callback='stopped'):
    """A player whose stream closed at 1010 s of 1293 s (the Daria S01E11 case) and whose
    Kodi callback lands callback_at_ms after the close, measured on a fake clock."""
    player = RedLightPlayer()
    player.onPlayBackStarted()
    player.is_generic, player.cancel_all_playback = False, False
    player.curr_time, player.total_time, player.playing_filename = 1010.0, 1293.0, 'Daria S01E11.mkv'
    player._resolve_cancelled = lambda: False
    player.isPlayingVideo = lambda: False
    clock = {'ms': 0}

    def fake_sleep(ms):
        clock['ms'] += ms
        if callback_at_ms is not None and clock['ms'] >= callback_at_ms:
            if callback == 'stopped': player.onPlayBackStopped()
            elif callback == 'ended': player.onPlayBackEnded()
            else: player.onPlayBackError()

    logged = []
    monkeypatch.setattr(player_mod.ku, 'sleep', fake_sleep)
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: logged.append(a))
    return player, clock, logged


def test_stop_arriving_after_the_old_2s_window_does_not_resume(monkeypatch):
    # Shield, 12.09.26: CloseFile 14:16:41.855, OnPlayBackStopped 14:16:45.352.
    player, clock, _ = _closed_player(monkeypatch, callback_at_ms=3500, callback='stopped')
    player._note_abnormal_end(False, False)
    assert player.stall_position is None
    assert clock['ms'] < _STALL_CALLBACK_WAIT_MS   # returned as soon as the Stop landed


def test_end_of_file_well_before_the_end_resumes(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=300, callback='ended')
    player._note_abnormal_end(False, False)
    assert player.stall_position == (1010.0, 1293.0)


def test_error_callback_resumes(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=300, callback='error')
    player._note_abnormal_end(False, False)
    assert player.stall_position == (1010.0, 1293.0)


# --- seek_to_end: a skip to the end that fails is not a stall (Veep S02E09, 12.09.26) -------

def test_recent_seek_past_the_end_is_a_skip():
    # Shield: seek target 1704.68 s on a 1703 s file, EOF 2 s later.
    assert seek_to_end((100.0, 1704.68), 1703, 102.2)


def test_recent_seek_inside_the_last_minute_is_a_skip():
    assert seek_to_end((100.0, 1650), 1703, 105)


def test_seek_into_the_middle_is_not_a_skip():
    assert not seek_to_end((100.0, 900), 1703, 102)


def test_old_seek_is_ignored():
    assert not seek_to_end((100.0, 1700), 1703, 100 + _SEEK_END_WINDOW_SEC + 1)


@pytest.mark.parametrize('last_seek', [None, (), ('a', 'b'), (100.0,)])
def test_missing_or_bad_seek_is_not_a_skip(last_seek):
    assert not seek_to_end(last_seek, 1703, 101)


def test_seek_callback_records_target_in_seconds():
    player = RedLightPlayer()
    player.onPlayBackSeek(1704680, 0)
    assert player._last_seek[1] == pytest.approx(1704.68)


def test_eof_right_after_a_seek_to_the_end_does_not_resume(monkeypatch):
    player, _, logged = _closed_player(monkeypatch, callback_at_ms=300, callback='ended')
    player.onPlayBackSeek(1293000, 0)
    player._note_abnormal_end(False, False)
    assert player.stall_position is None
    assert any('skip to the end' in str(a) for a in logged)


def test_stall_after_a_seek_into_the_middle_still_resumes(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=300, callback='ended')
    player.onPlayBackSeek(600000, 0)
    player._note_abnormal_end(False, False)
    assert player.stall_position == (1010.0, 1293.0)


def test_silent_close_does_not_resume_and_says_why(monkeypatch):
    player, clock, logged = _closed_player(monkeypatch, callback_at_ms=None)
    player._note_abnormal_end(False, False)
    assert player.stall_position is None
    assert clock['ms'] >= _STALL_CALLBACK_WAIT_MS
    assert any('not resuming' in str(a) for a in logged)


# --- _should_prep_next_ep before info_next_ep has run -------------------------------------

class _DevicePlayer(RedLightPlayer):
    # kodi_stub's xbmc.Player answers every attribute; the real one raises, which is the
    # AttributeError the Shield logged. Reproduce that for the attribute under test.
    def __getattr__(self, name):
        if name == 'start_prep': raise AttributeError(name)
        return super().__getattr__(name)


def _prep_player(monkeypatch):
    player = _DevicePlayer()
    player.autoplay_nextep = False
    player._owns_active_playback = lambda: True
    player.total_time, player.curr_time = 1293.0, 1250.0
    monkeypatch.setattr(player_mod.ku, 'get_property', lambda key: '')
    return player


def test_prep_check_before_start_prep_exists_is_false_not_an_error(monkeypatch):
    # Every Daria play on 12.09.26 logged "no attribute 'start_prep'" about 3 s in.
    player = _prep_player(monkeypatch)
    assert not hasattr(player, 'start_prep')
    assert player._should_prep_next_ep() is False


def test_prep_check_fires_once_start_prep_is_known(monkeypatch):
    player = _prep_player(monkeypatch)
    player.start_prep = 103
    assert player._should_prep_next_ep() is True


# --- playback_end_outcome: the playback log's end row (#141, #72) --------------------------

from modules.player import playback_end_outcome  # noqa: E402


@pytest.mark.parametrize('kwargs, want', [
    (dict(superseded=True, nextep_handoff=True), 'next_episode'),
    (dict(superseded=True), 'superseded'),
    (dict(superseded=True, stopped=True), 'superseded'),
    (dict(), 'ended'),
    (dict(stopped=True), 'stopped'),
    (dict(abnormal=True, signal='stopped'), 'stopped'),
    (dict(abnormal=True, signal=None), 'no_callback'),
    (dict(abnormal=True, signal='stall'), 'stall'),
    (dict(abnormal=True, signal='stall', seek_end=True), 'seek_end'),
])
def test_end_outcome_table(kwargs, want):
    assert playback_end_outcome(**kwargs) == want


def test_stall_sets_outcome_stall(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=300, callback='ended')
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'stall'


def test_late_stop_sets_outcome_stopped(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=3500, callback='stopped')
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'stopped'


def test_silent_close_sets_outcome_no_callback(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=None)
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'no_callback'


def test_skip_to_the_end_sets_outcome_seek_end(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=300, callback='ended')
    player.onPlayBackSeek(1293000, 0)
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'seek_end'


def test_another_stream_playing_after_the_wait_is_superseded(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=300, callback='ended')
    player.isPlayingVideo = lambda: True
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'superseded'


def test_natural_end_sets_outcome_ended(monkeypatch):
    player, clock, _ = _closed_player(monkeypatch, callback_at_ms=None)
    player.curr_time = 1290.0
    player.onPlayBackEnded()
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'ended'
    assert clock['ms'] == 0   # a quiet end never waits for callbacks


def test_nextep_alert_play_is_a_next_episode_handoff(monkeypatch):
    # Shield, 12.09.26 19:46: Veep S02E09 alert -> Play -> S02E10 took over ("superseded by user
    # playback" in the log). That is a normal handoff, not a user interrupting the show.
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=None)
    player._nextep_alert_shown = True
    player._note_abnormal_end(True, False)
    assert player.end_outcome == 'next_episode'


def test_other_takeover_is_superseded(monkeypatch):
    player, _, _ = _closed_player(monkeypatch, callback_at_ms=None)
    player._note_abnormal_end(True, False)
    assert player.end_outcome == 'superseded'


def test_nextep_prep_branch_still_classifies_a_stop(monkeypatch):
    # _note_abnormal_end returns before the stall check once next-episode prep ran; the end
    # row must still say Stop vs end there, or autoplay chains drop out of the counts.
    player, clock, _ = _closed_player(monkeypatch, callback_at_ms=None)
    player._nextep_prep_attempted = True
    player.onPlayBackStopped()
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'stopped'
    assert clock['ms'] == 0
