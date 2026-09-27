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


def _prep_player(monkeypatch, props, pending_after_schedule=True):
    player, marks = _player(monkeypatch)
    player.nextep_info_gathered, player.nextep_settings = True, {'play_type': 'autoplay_nextep'}
    calls = {'prep': 0, 'stop': 0}
    def fake_schedule():
        calls['prep'] += 1
        if pending_after_schedule: props[player_mod.PROP_NEXTEP_PENDING] = 'true'
    player._schedule_next_ep = fake_schedule
    player.stop = lambda: calls.__setitem__('stop', calls['stop'] + 1)
    return player, marks, calls


def test_no_stash_claims_the_skip_and_prepares_now(monkeypatch):
    """C410: no stash yet, so the skip is claimed (no stop) and the prep starts while playing."""
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    scheduled = _wire(monkeypatch, props, None)
    player, marks, calls = _prep_player(monkeypatch, props)
    assert player._try_skip_to_stash() is True
    assert calls == {'prep': 1, 'stop': 0} and marks == [True] and scheduled == []
    assert props[player_mod.PROP_SKIP_EPISODE_ACK] == 'true'
    assert player_mod.PROP_SKIP_EPISODE not in props
    assert player._skip_prep_deadline


def test_waiting_skip_hands_over_when_the_prep_stashes(monkeypatch):
    props = Props()
    stash_box = [None]
    scheduled = _wire(monkeypatch, props, None)
    monkeypatch.setattr(sources_mod, 'peek_nextep_autoplay_stash', lambda: stash_box[0])
    monkeypatch.setattr(sources_mod, 'take_nextep_autoplay_stash', lambda: stash_box[0])
    player, _, calls = _prep_player(monkeypatch, props)
    props[player_mod.PROP_SKIP_EPISODE] = 'true'
    player._try_skip_to_stash()
    assert player._try_skip_to_stash() is False  # prep still running
    stash_box[0] = {'meta': {'title': '30 Rock', 'season': 3, 'episode': 21}}
    assert player._try_skip_to_stash() is True
    assert scheduled == [(stash_box[0], False)] and calls['stop'] == 0
    assert player._skip_prep_deadline is None


def test_waiting_skip_falls_back_when_the_prep_finds_nothing(monkeypatch):
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    _wire(monkeypatch, props, None)
    player, _, calls = _prep_player(monkeypatch, props)
    player._try_skip_to_stash()
    props.pop(player_mod.PROP_NEXTEP_PENDING)  # prep finished, no stash
    assert player._try_skip_to_stash() is False
    assert calls['stop'] == 1 and props[player_mod.PROP_SKIP_EPISODE] == 'true'


def test_waiting_skip_falls_back_after_the_deadline(monkeypatch):
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    _wire(monkeypatch, props, None)
    player, _, calls = _prep_player(monkeypatch, props)
    player._try_skip_to_stash()
    player._skip_prep_deadline = 1.0
    assert player._try_skip_to_stash() is False and calls['stop'] == 1


def test_autoplay_off_leaves_the_skip_to_the_stop_path(monkeypatch):
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    _wire(monkeypatch, props, None)
    player, _ = _player(monkeypatch)
    player.autoplay_nextep = False
    assert player._try_skip_to_stash() is False
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


def test_next_up_dialog_plays_on_a_skip(monkeypatch):
    """C410: the player's monitor is blocked in the Next Up modal, so the dialog claims the skip."""
    from modules import kodi_utils
    from windows.playback_notifications import NextEpisode
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    monkeypatch.setattr(kodi_utils, 'get_property', props.get_property)
    monkeypatch.setattr(kodi_utils, 'set_property', props.set_property)
    monkeypatch.setattr(kodi_utils, 'clear_property', props.clear_property)
    monkeypatch.setattr(kodi_utils, 'logger', lambda *a: None)
    dialog = object.__new__(NextEpisode)
    dialog.selected, dialog.closed = 'close', False
    closed = []
    dialog.close = lambda: closed.append(1)
    assert dialog._skip_requested() is True
    assert dialog.selected == 'play' and dialog.closed and closed == [1]
    assert props[player_mod.PROP_SKIP_EPISODE_ACK] == 'true'
    assert player_mod.PROP_SKIP_EPISODE not in props
    props.clear()
    assert dialog._skip_requested() is False


def test_intro_prompt_yields_to_a_skip(monkeypatch):
    """W-280926-3: a skip in the intro-prompt window closes the prompt and acks, keeping the flag."""
    from modules import kodi_utils
    from windows.playback_notifications import IntroSkipPrompt
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    monkeypatch.setattr(kodi_utils, 'get_property', props.get_property)
    monkeypatch.setattr(kodi_utils, 'set_property', props.set_property)
    monkeypatch.setattr(kodi_utils, 'logger', lambda *a: None)
    dialog = object.__new__(IntroSkipPrompt)
    dialog.selected, dialog.closed = True, False
    closed = []
    dialog.close = lambda: closed.append(1)
    assert dialog._skip_requested() is True
    assert dialog.selected is False and dialog.closed and closed == [1]
    assert props[player_mod.PROP_SKIP_EPISODE_ACK] == 'true'
    assert props[player_mod.PROP_SKIP_EPISODE] == 'true'
