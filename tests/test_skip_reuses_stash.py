# -*- coding: utf-8 -*-
"""The explicit next-episode skip reuses the prepared next episode (#1, pd.82).

Before this, request_skip_episode always stopped playback; the stop dropped the autoplay stash and
_play_next_after_seek_end scraped the next episode from scratch, after a trip to the home screen.
Now the playing monitor claims the skip when a stash is ready and hands it over like the Next Up
dialog's Play; request_skip_episode stops only when nobody claims it.
"""
import modules.sources as sources_mod
from modules import player as player_mod
from modules.player import RedLightPlayer


class Props(dict):
    def get_property(self, k): return self.get(k, '')
    def set_property(self, k, v): self[k] = v
    def clear_property(self, k): self.pop(k, None)


def _wire(monkeypatch, props, stash):
    monkeypatch.setattr(player_mod.ku, 'get_property', props.get_property)
    monkeypatch.setattr(player_mod.ku, 'set_property', props.set_property)
    monkeypatch.setattr(player_mod.ku, 'clear_property', props.clear_property)
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    scheduled = []
    monkeypatch.setattr(sources_mod, 'nextep_autoplay_cancelled', lambda: False)
    monkeypatch.setattr(sources_mod, 'nextep_end_play_superseded', lambda *a: False)
    monkeypatch.setattr(sources_mod, 'peek_nextep_autoplay_stash', lambda: stash)
    monkeypatch.setattr(sources_mod, 'take_nextep_autoplay_stash', lambda: stash)
    monkeypatch.setattr(sources_mod, 'schedule_nextep_stashed_play', lambda s, show_busy=None: scheduled.append((s, show_busy)) or True)
    return scheduled


def _player(monkeypatch):
    player = object.__new__(RedLightPlayer)
    player.media_type, player.autoplay_nextep, player.media_marked = 'episode', True, False
    player._nextep_stash_play_scheduled = False
    marks = []
    player.media_watched_marker = lambda force_watched=False: marks.append(force_watched)
    player._log_nextep = lambda *a: None
    return player, marks


def test_monitor_hands_the_skip_to_the_stash(monkeypatch):
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    stash = {'meta': {'title': '30 Rock', 'season': 3, 'episode': 21}}
    scheduled = _wire(monkeypatch, props, stash)
    player, marks = _player(monkeypatch)
    assert player._try_skip_to_stash() is True
    assert scheduled == [(stash, False)]
    assert marks == [True]
    assert player._nextep_stash_play_scheduled
    assert player_mod.PROP_SKIP_EPISODE not in props
    assert props[player_mod.PROP_SKIP_EPISODE_ACK] == 'true'


def test_no_stash_leaves_the_skip_to_the_stop_path(monkeypatch):
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    scheduled = _wire(monkeypatch, props, None)
    player, marks = _player(monkeypatch)
    assert player._try_skip_to_stash() is False
    assert scheduled == [] and marks == []
    assert props[player_mod.PROP_SKIP_EPISODE] == 'true'


def test_no_skip_request_does_nothing(monkeypatch):
    props = Props()
    scheduled = _wire(monkeypatch, props, {'meta': {}})
    player, _ = _player(monkeypatch)
    assert player._try_skip_to_stash() is False and scheduled == []


def test_request_returns_without_stop_when_claimed(monkeypatch):
    props = Props()
    _wire(monkeypatch, props, None)
    props[player_mod.PROP_ACTIVE_PLAYBACK_KEY] = 'k'
    stops = []
    monkeypatch.setattr(player_mod.ku, 'kodi_player', lambda: type('P', (), {'stop': lambda self: stops.append(1)})())
    def fake_sleep(ms): props[player_mod.PROP_SKIP_EPISODE_ACK] = 'true'
    monkeypatch.setattr(player_mod.ku, 'sleep', fake_sleep)
    assert player_mod.request_skip_episode() is True
    assert stops == []


def test_request_stops_when_unclaimed(monkeypatch):
    props = Props()
    _wire(monkeypatch, props, None)
    props[player_mod.PROP_ACTIVE_PLAYBACK_KEY] = 'k'
    stops = []
    monkeypatch.setattr(player_mod.ku, 'kodi_player', lambda: type('P', (), {'stop': lambda self: stops.append(1)})())
    monkeypatch.setattr(player_mod.ku, 'sleep', lambda ms: None)
    assert player_mod.request_skip_episode() is True
    assert stops == [1] and props[player_mod.PROP_SKIP_EPISODE] == 'true'
