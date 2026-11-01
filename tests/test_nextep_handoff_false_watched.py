# -*- coding: utf-8 -*-
"""#319: a queued next-episode hand-off marked the new episode watched three seconds in, and the
same-file skip then marked later episodes of a season pack watched.

On 01.11 16:52 South Park S04E11 was adopted from the queue. Its first stop read logged 98.5%
(curr=1307.4s), S04E10's end: set_constants reset current_point but kept curr_time, and the seek
guard in _refresh_playback_position never shrinks a known position. The next-episode prep then
marked S04E14 to S04E16 watched because their top source had the same (season pack) name as the
playing release."""
import modules.kodi_utils as kodi_utils
from modules import player as player_mod
from modules.player import RedLightPlayer
from modules.sources import Sources


class FakeSources:
    meta = {'title': 'South Park', 'season': 4, 'episode': 11}
    playback_percent = 0.0
    playing_filename = 'South.Park.S04E11.mkv'


def _player(monkeypatch, live_curr, live_total):
    monkeypatch.setattr(player_mod.ku, 'clear_property', lambda *a: None)
    monkeypatch.setattr(player_mod.ku, 'kodi_monitor', lambda: None)
    player = object.__new__(RedLightPlayer)
    player._read_player_times = lambda: (live_curr, live_total)
    return player


def test_a_new_play_does_not_keep_the_episode_before_s_position(monkeypatch):
    player = _player(monkeypatch, 3.0, 1327.9)
    # Left over from S04E10 on the same player object.
    player.curr_time, player.total_time, player.current_point = 1307.4, 1327.9, 98.5
    try: player.set_constants('url', FakeSources())
    except Exception: pass  # later lines need more Kodi; the reset sits before them
    assert player.curr_time == 0.0 and player.total_time == 0.0
    assert player._refresh_playback_position(allow_stale=True)
    assert player.current_point < 1


def test_the_seek_guard_still_keeps_a_larger_position_within_one_play(monkeypatch):
    player = _player(monkeypatch, 3.0, 1327.9)
    player.curr_time, player.total_time = 600.0, 1327.9
    player._refresh_playback_position(allow_stale=True)
    assert player.curr_time == 600.0


PACK = 'South.Park.S04.1080p.WEB-DL'


def _sources(monkeypatch, playing, season, episode):
    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: playing if key == 'redlight.now_playing_release' else '')
    obj = object.__new__(Sources)
    obj.season, obj.episode = season, episode
    return obj


def test_a_season_pack_name_is_not_a_combined_file(monkeypatch):
    assert not _sources(monkeypatch, PACK, 4, 14)._nextep_duplicate_of_playing([{'name': PACK}])


def test_a_combined_file_that_names_the_episode_is_still_skipped(monkeypatch):
    combined = 'Seinfeld.S03E17E18.The.Boyfriend.mkv'
    assert _sources(monkeypatch, combined, 3, 18)._nextep_duplicate_of_playing([{'name': combined}])
    assert not _sources(monkeypatch, combined, 3, 19)._nextep_duplicate_of_playing([{'name': combined}])
