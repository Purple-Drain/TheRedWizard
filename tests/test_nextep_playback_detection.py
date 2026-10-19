# -*- coding: utf-8 -*-
"""#268 open check: only a next-episode play (autoplay, Next key, Play # Episodes) starts at 0 when
Next Episode Resume is off. A Next Episodes widget row or a menu play must keep its resume point."""
from modules.sources import Sources


def _sources(play_type='', params=None, **attrs):
    s = object.__new__(Sources)
    s.play_type, s.params = play_type, params or {}
    for k, v in attrs.items(): setattr(s, k, v)
    return s


def test_widget_or_menu_play_is_not_a_next_episode_play():
    # What a widget row or menu item hands the router: mode, ids, no play_type, no stash flag.
    params = {'mode': 'playback.media', 'media_type': 'episode', 'tmdb_id': '1400', 'season': '4', 'episode': '2'}
    assert _sources('', params)._is_nextep_playback() is False


def test_autoplay_play_types_are_next_episode_plays():
    for play_type in ('autoplay_nextep', 'autoscrape_nextep', 'random_continual'):
        assert _sources(play_type)._is_nextep_playback() is True


def test_stash_play_is_a_next_episode_play():
    assert _sources('', {'nextep_stash_play': 'true'})._is_nextep_playback() is True
    assert _sources('', {}, _nextep_stash_results=[{'x': 1}])._is_nextep_playback() is True
    assert _sources('', {}, _nextep_alert_handled=True)._is_nextep_playback() is True
