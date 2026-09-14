# -*- coding: utf-8 -*-
"""#165 follow-up: a next-episode play skips any queued source that is the file still playing.

On 14.09 autoplay into Seinfeld S04E02 fell back from a failed SMB source to the next source, the
TorBox copy of Part 1, while Part 1 was still playing. The stash check (#42) compares only the top
result, so the fallback went unchecked. Sources is built with object.__new__ (see
test_sources_thread_join.py); only what _nextep_same_file() reads is set.
"""
import modules.kodi_utils as kodi_utils
from modules.sources import Sources

PLAYING = 'Seinfeld.S04E01.The.Trip.(Part.1).mkv'


def _sources(play_type):
    obj = object.__new__(Sources)
    obj.play_type = play_type
    return obj


def _playing(monkeypatch, name):
    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: name if key == 'redlight.now_playing_release' else '')


def test_the_file_still_playing_is_skipped_in_a_next_episode_play(monkeypatch):
    _playing(monkeypatch, PLAYING)
    assert _sources('autoplay_nextep')._nextep_same_file({'name': PLAYING})
    # The scraped label form of the same file.
    assert _sources('autoscrape_nextep')._nextep_same_file({'name': 'Seinfeld S04E01 The Trip (Part 1) mkv'})


def test_another_file_is_played(monkeypatch):
    _playing(monkeypatch, PLAYING)
    assert not _sources('autoplay_nextep')._nextep_same_file({'name': 'Seinfeld.S04E02.The.Trip.(Part.2).mkv'})


def test_only_next_episode_plays_skip(monkeypatch):
    _playing(monkeypatch, PLAYING)
    for play_type in ('', 'random_continual', None):
        assert not _sources(play_type)._nextep_same_file({'name': PLAYING})


def test_nothing_playing_or_no_name_skips_nothing(monkeypatch):
    _playing(monkeypatch, '')
    assert not _sources('autoplay_nextep')._nextep_same_file({'name': PLAYING})
    _playing(monkeypatch, PLAYING)
    assert not _sources('autoplay_nextep')._nextep_same_file({})
    assert not _sources('autoplay_nextep')._nextep_same_file(None)
