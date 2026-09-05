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
