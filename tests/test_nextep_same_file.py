# -*- coding: utf-8 -*-
"""#165 follow-up: a stashed next-episode play skips any queued source that is the file of the
episode before it.

On 14.09 autoplay into Seinfeld S04E02 fell back from a failed SMB source to the next source, the
TorBox copy of Part 1. The stash check (#42) compares only the top result, and the live
redlight.now_playing_release is cleared before the stash is played (and rewritten for each queue
attempt), so the name is recorded in the stash while the episode is still playing. Sources is built
with object.__new__ (see test_sources_thread_join.py); only what each method reads is set.
"""
import modules.kodi_utils as kodi_utils
import modules.sources as sources
from modules.sources import Sources

PLAYING = 'Seinfeld.S04E01.The.Trip.(Part.1).mkv'


def _sources(prior):
    obj = object.__new__(Sources)
    obj._nextep_prior_release = prior
    return obj


def test_the_stash_records_the_release_still_playing(monkeypatch):
    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: PLAYING if key == 'redlight.now_playing_release' else '')
    monkeypatch.setattr(kodi_utils, 'set_property', lambda *a: None)
    monkeypatch.setattr(kodi_utils, 'clear_property', lambda *a: None)
    monkeypatch.setattr(sources, 'nextep_autoplay_cancelled', lambda: False)
    monkeypatch.setattr(sources, '_nextep_stash_key', lambda meta: 'k')
    sources._NEXTEP_AUTOPLAY_STASH.clear()
    assert sources.stash_nextep_autoplay_results([{'name': 'x'}], {'title': 'Seinfeld'}, {}, {})
    assert sources._NEXTEP_AUTOPLAY_STASH['k']['playing_release'] == PLAYING
    sources._NEXTEP_AUTOPLAY_STASH.clear()


def test_the_file_of_the_episode_before_is_skipped():
    assert _sources(PLAYING)._nextep_same_file({'name': PLAYING})
    # The scraped label form of the same file.
    assert _sources(PLAYING)._nextep_same_file({'name': 'Seinfeld S04E01 The Trip (Part 1) mkv'})


def test_another_file_is_played():
    assert not _sources(PLAYING)._nextep_same_file({'name': 'Seinfeld.S04E02.The.Trip.(Part.2).mkv'})


def test_no_recorded_release_skips_nothing(monkeypatch):
    # A manual play loads no stash, whatever the live property says.
    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: PLAYING)
    assert not _sources('')._nextep_same_file({'name': PLAYING})
    assert not object.__new__(Sources)._nextep_same_file({'name': PLAYING})
    assert not _sources(PLAYING)._nextep_same_file({})
    assert not _sources(PLAYING)._nextep_same_file(None)
