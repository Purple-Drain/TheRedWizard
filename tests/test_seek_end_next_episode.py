# -*- coding: utf-8 -*-
"""A skip to the end starts the next episode (#199 part 2).

Shield 26.09.26 19:55: a skip from 99 s of 1396 s jumped over the start_prep window, so nothing
was prepared, #157's seek_end branch returned, and the owner had to start S03E01 by hand.
_play_next_after_seek_end now works out the literal next episode (EpisodeTools.next_episode_info,
the prep's own computation) and launches it as a fresh mode=playback.media play.
"""
from modules import player as player_mod
from modules.player import RedLightPlayer


def _player(monkeypatch, next_info, autoplay=True, media_type='episode', prep_attempted=False):
    player = object.__new__(RedLightPlayer)
    player.media_type, player.autoplay_nextep, player.autoscrape_nextep = media_type, autoplay, False
    player.meta = {'title': '30 Rock', 'tmdb_id': 4608, 'season': 2, 'episode': 15}
    player.nextep_settings = {'play_type': 'autoplay_nextep'}
    if prep_attempted: player._nextep_prep_attempted = True
    launched, logged, seen = [], [], {}

    class FakeTools:
        def __init__(self, meta, nextep_settings):
            seen['settings'] = nextep_settings

        def next_episode_info(self):
            return dict(next_info) if isinstance(next_info, dict) else next_info

    import modules.episode_tools as et
    monkeypatch.setattr(et, 'EpisodeTools', FakeTools)
    monkeypatch.setattr(player_mod.ku, 'run_plugin', lambda params, block=False: launched.append(params))
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: logged.append(a))
    return player, launched, logged, seen


NEXT = {'media_type': 'episode', 'tmdb_id': 4608, 'season': 3, 'episode': 1, 'background': 'true',
        'nextep_settings': {'play_type': 'autoplay_nextep'}, 'play_type': 'autoplay_nextep', 'media': 'media'}


def test_skip_to_the_end_launches_the_next_episode_as_a_fresh_play(monkeypatch):
    player, launched, _, _ = _player(monkeypatch, NEXT)
    player._play_next_after_seek_end()
    assert len(launched) == 1
    params = launched[0]
    assert params['mode'] == 'playback.media'
    assert (params['season'], params['episode']) == (3, 1)
    assert 'background' not in params and 'nextep_settings' not in params and 'play_type' not in params


def test_no_next_episode_launches_nothing(monkeypatch):
    player, launched, _, _ = _player(monkeypatch, 'no_next_episode')
    player._play_next_after_seek_end()
    assert launched == []


def test_autoplay_off_or_a_movie_launches_nothing(monkeypatch):
    for kwargs in (dict(autoplay=False), dict(media_type='movie')):
        player, launched, _, _ = _player(monkeypatch, NEXT, **kwargs)
        player._play_next_after_seek_end()
        assert launched == []


def test_prep_already_running_leaves_the_hand_off_to_it(monkeypatch):
    player, launched, _, _ = _player(monkeypatch, NEXT, prep_attempted=True)
    player._play_next_after_seek_end()
    assert launched == []


def test_missing_nextep_settings_still_gets_a_play_type(monkeypatch):
    player, launched, _, seen = _player(monkeypatch, NEXT)
    player.nextep_settings = None
    player._play_next_after_seek_end()
    assert seen['settings'] == {'play_type': 'autoplay_nextep'}
    assert len(launched) == 1


def test_seek_end_branch_calls_the_hand_off(monkeypatch):
    player = RedLightPlayer()
    player.onPlayBackStarted()
    player.is_generic, player.cancel_all_playback = False, False
    player.curr_time, player.total_time, player.playing_filename = 99.0, 1396.0, '30.Rock.S02E15.mkv'
    player._resolve_cancelled = lambda: False
    player.isPlayingVideo = lambda: False
    monkeypatch.setattr(player_mod.ku, 'sleep', lambda ms: player.onPlayBackEnded())
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    called = []
    player._play_next_after_seek_end = lambda: called.append(True)
    player.onPlayBackSeek(1397000, 0)
    player._note_abnormal_end(False, False)
    assert player.end_outcome == 'seek_end'
    assert called == [True]


def test_intro_prompt_closes_once_playback_stops():
    """#199: the prompt held the monitor for its whole countdown after a skip ended playback."""
    from windows.playback_notifications import IntroSkipPrompt
    prompt = object.__new__(IntroSkipPrompt)
    prompt.closed, prompt.timed_out, prompt.countdown_sec = False, False, 15
    ticks = []
    prompt.setProperty = lambda *a: None
    prompt.sleep = lambda ms: ticks.append(ms)
    prompt.close = lambda: None
    prompt.player = type('P', (), {'isPlayingVideo': lambda self: False, 'isPlaying': lambda self: False})()
    prompt.monitor()
    assert prompt.timed_out is True
    assert len(ticks) == 1


# #199 C7: 'play next episode now' (mode=playback.skip_episode).

def _props(monkeypatch, initial):
    props = dict(initial)
    monkeypatch.setattr(player_mod.ku, 'get_property', lambda key: props.get(key, ''))
    monkeypatch.setattr(player_mod.ku, 'set_property', lambda key, value: props.__setitem__(key, value))
    monkeypatch.setattr(player_mod.ku, 'clear_property', lambda key: props.pop(key, None))
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    return props


def test_skip_request_flags_and_stops_a_red_light_play(monkeypatch):
    props = _props(monkeypatch, {player_mod.PROP_ACTIVE_PLAYBACK_KEY: 'k1'})
    stopped = []
    monkeypatch.setattr(player_mod.ku, 'kodi_player', lambda: type('P', (), {'stop': lambda self: stopped.append(True)})())
    assert player_mod.request_skip_episode() is True
    assert props[player_mod.PROP_SKIP_EPISODE] == 'true'
    assert stopped == [True]


def test_skip_request_ignores_playback_red_light_does_not_own(monkeypatch):
    props = _props(monkeypatch, {})
    notes, stopped = [], []
    monkeypatch.setattr(player_mod.ku, 'notification', lambda *a, **k: notes.append(a))
    monkeypatch.setattr(player_mod.ku, 'kodi_player', lambda: type('P', (), {'stop': lambda self: stopped.append(True)})())
    assert player_mod.request_skip_episode() is False
    assert player_mod.PROP_SKIP_EPISODE not in props and stopped == [] and len(notes) == 1


def test_explicit_skip_ignores_autoplay_off_and_an_earlier_prep(monkeypatch):
    player, launched, _, _ = _player(monkeypatch, NEXT, autoplay=False, prep_attempted=True)
    player._play_next_after_seek_end(explicit=True)
    assert len(launched) == 1 and launched[0]['mode'] == 'playback.media'


def test_explicit_skip_still_ignores_movies(monkeypatch):
    player, launched, _, _ = _player(monkeypatch, NEXT, media_type='movie')
    player._play_next_after_seek_end(explicit=True)
    assert launched == []


def test_router_sends_skip_episode_to_the_player(monkeypatch):
    import sys
    from modules import router, kodi_utils
    called = []
    monkeypatch.setattr(player_mod, 'request_skip_episode', lambda: called.append(True) or True)
    monkeypatch.setattr(kodi_utils, 'release_resolve_handle', lambda argv: None)
    monkeypatch.setattr(sys, 'argv', ['plugin://plugin.video.redlight/', '-1', '?mode=playback.skip_episode'])
    router.routing(sys)
    assert called == [True]
