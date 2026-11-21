# -*- coding: utf-8 -*-
"""#349 (with #143, #330, #339): the adopt path after a wake and after a queued hand-off.

21.11 Seinfeld S06E16 The Beard: the device slept at 11:56 with the episode at 1156 s of 1409 s. At
13:10:43 it woke and Kodi itself restored the file at 1156 s (no Red Light player, no previous file).
The wake-resume adopt ran at 13:11:08 and its first read was 1176 s. The #330 guard, written for a
queued hand-off where Kodi can still report the PREVIOUS file's position, treated every read as stale
for the whole play (none was ever at or under 60 s): stop read 0.0%, no watched mark, no next-episode
prep, no autoplay.

Fixes: a wake adopt (params adopt_no_resume) is exempt from the guard; a queued hand-off that resumes
a saved bookmark past 60 s becomes fresh once its position advances at about the pace of the clock;
the monitor loop no longer reads show_stinger unbound for an episode (#339)."""
import inspect

from modules import player as player_mod
from modules.player import RedLightPlayer


class FakePlaylist:
    def __init__(self): self.items, self.position = ['prev', 'cur'], 1
    def getposition(self): return self.position
    def add(self, url, listitem=None, index=-1): self.items.append(url)


class FakeSources:
    def __init__(self, params): self.params = params


def _player(monkeypatch, clock, params=None):
    monkeypatch.setattr(player_mod.time, 'time', lambda: clock[0])
    props = {player_mod.PROP_ADOPT_QUEUED: 'true'}
    monkeypatch.setattr(player_mod.ku, 'get_property', lambda k: props.get(k, ''))
    monkeypatch.setattr(player_mod.ku, 'clear_property', lambda k: props.pop(k, None))
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    monkeypatch.setattr(player_mod.ku, 'make_playlist', lambda kind: FakePlaylist())
    monkeypatch.setattr(player_mod.ku, 'build_url', lambda p: 'marker')
    monkeypatch.setattr(player_mod.xbmcgui, 'ListItem', lambda *a, **k: type('LI', (), {'setProperty': lambda s, k, v: None})())
    player = object.__new__(RedLightPlayer)
    player._queued_next, player._queued_real, player._own_index = False, None, 0
    player.is_generic, player.media_type = False, 'episode'
    player.isPlayingVideo = lambda: True
    player.getTotalTime = lambda: 1409.0
    player.getTime = lambda: 1176.0
    if params is not None: player.sources_object = FakeSources(params)
    player.logs = []
    player._log_nextep = lambda msg, *a: player.logs.append(msg)
    return player


def _adopted(monkeypatch, clock, params=None):
    player = _player(monkeypatch, clock, params)
    assert player._adopt_queued_play() is True
    return player


def test_wake_adopt_is_exempt_from_the_330_guard(monkeypatch):
    clock = [1000.0]
    player = _adopted(monkeypatch, clock, {'adopt_no_resume': 'true'})
    assert not player._adopt_quiet()
    assert not player._adopt_stale_read(1176.0)
    assert 'wake resume' in player.logs[-1]


def test_queued_hand_off_keeps_the_guard(monkeypatch):
    clock = [1000.0]
    for params in (None, {}, {'adopt_no_resume': 'false'}):
        player = _adopted(monkeypatch, clock, params)
        assert player._adopt_quiet()
        assert player._adopt_stale_read(1293.2)


def _stopped_player(player, curr, total, percent_tick=90):
    spawned, scrobbles = [], []
    player._read_player_times = lambda: (curr, total)
    player._short_of_tmdb_runtime = lambda: False
    player._spawn = lambda fn, args=(), name='': spawned.append(name)
    player._mark_combined_release_siblings_watched = lambda p: None
    for n in ('_trakt_scrobble_stop', '_simkl_scrobble_stop', '_punchplay_scrobble_stop', '_wetrakr_on_stop'):
        setattr(player, n, lambda *a, **k: scrobbles.append(n))
    player.watched_tick_percent = percent_tick
    player.tmdb_id, player.title, player.year, player.season, player.episode, player.tvdb_id = 1, 'Seinfeld', 1990, 6, 16, 2
    player.curr_time, player.total_time, player.current_point = 0.0, 0.0, 0
    return spawned


def test_wake_adopt_stopped_over_90_percent_is_marked_watched(monkeypatch):
    clock = [1000.0]
    player = _adopted(monkeypatch, clock, {'adopt_no_resume': 'true'})
    spawned = _stopped_player(player, 1300.0, 1409.0)
    clock[0] += 30
    player.media_watched_marker()
    assert 'mark_watched' in spawned


def test_queued_hand_off_adopt_in_the_same_situation_is_not_marked(monkeypatch):
    clock = [1000.0]
    player = _adopted(monkeypatch, clock)
    spawned = _stopped_player(player, 1300.0, 1409.0)
    clock[0] += 30
    player.media_watched_marker()
    assert 'mark_watched' not in spawned


def test_330_real_numbers_stay_stale_and_never_set_a_high_point(monkeypatch):
    clock = [1000.0]
    player = _adopted(monkeypatch, clock)
    reads = [1293.2] * 5
    for value in reads:
        assert player._adopt_stale_read(value)
        clock[0] += 2
    assert player._adopt_stale_read(1293.3)
    clock[0] += 4
    assert player._adopt_stale_read(1294.3)
    assert not player._adopt_fresh
    player._read_player_times = lambda: (1294.3, 1294.3)
    player.curr_time = player.total_time = 0.0
    player.current_point = 0
    assert player._refresh_playback_position(allow_stale=False) is False
    assert not player.current_point or player.current_point < 90


def test_queued_hand_off_with_a_bookmark_becomes_fresh_when_it_advances(monkeypatch):
    clock = [1000.0]
    player = _adopted(monkeypatch, clock)
    assert player._adopt_stale_read(1307.4)
    clock[0] += 4
    assert player._adopt_stale_read(600.0)
    clock[0] += 4
    assert not player._adopt_stale_read(604.0)
    assert player._adopt_fresh


def test_a_paused_resumed_file_stays_stale_then_becomes_fresh(monkeypatch):
    clock = [1000.0]
    player = _adopted(monkeypatch, clock)
    for _ in range(11):
        assert player._adopt_stale_read(600.0)
        clock[0] += 2
    assert not player._adopt_fresh
    clock[0] += 1
    assert player._adopt_stale_read(600.0)
    clock[0] += 4
    assert not player._adopt_stale_read(604.0)
    assert player._adopt_fresh


def test_a_read_at_or_under_a_minute_is_fresh_at_once(monkeypatch):
    clock = [1000.0]
    player = _adopted(monkeypatch, clock)
    assert not player._adopt_stale_read(2.0)
    assert player._adopt_fresh
    player = _adopted(monkeypatch, clock)
    assert not player._adopt_stale_read(60.0)


def test_339_show_stinger_is_assigned_before_the_episode_branch():
    src = inspect.getsource(RedLightPlayer.monitor)
    assert src.index('show_stinger,') < src.index("if self.media_type == 'episode':")
