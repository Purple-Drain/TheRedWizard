# -*- coding: utf-8 -*-
"""#260: "Skip intro after the first episode of a sitting". The first episode of a show plays its
intro; a consecutive episode of the same show (chain play, or picked inside the sitting gap)
is auto-skipped through the existing skip-intro path without a prompt."""
from modules import player as player_mod
from modules import settings as st
from modules.player import RedLightPlayer


class Props(dict):
    def get_property(self, k): return self.get(k, '')
    def set_property(self, k, v): self[k] = v
    def clear_property(self, k): self.pop(k, None)


class Sources(object):
    def __init__(self, play_type): self.play_type = play_type


def _setup(monkeypatch, binge=True, gap_min=30, skip_mode=0, tmdb_id='1400', play_type='', now=100000.0):
    props = Props()
    for n in ('get_property', 'set_property', 'clear_property'): monkeypatch.setattr(player_mod.ku, n, getattr(props, n))
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a, **k: None)
    values = {'redlight.binge_skip_intro': 'true' if binge else 'false',
              'redlight.binge_sitting_gap_min': str(gap_min),
              'redlight.autoplay_skip_intro': str(skip_mode),
              'redlight.skip_intro_all_episodes': 'true'}
    monkeypatch.setattr(st, 'get_setting', lambda k, d=None: values.get(k, d))
    monkeypatch.setattr(player_mod.time, 'time', lambda: now)
    p = object.__new__(RedLightPlayer)
    p.media_type, p.tmdb_id, p.imdb_id, p.season, p.episode = 'episode', tmdb_id, 'tt1', 1, 1
    p.sources_object = Sources(play_type)
    p._spawn = lambda *a, **k: None
    return p, props


def _fetch_started(p, monkeypatch):
    import apis.intro_skip_api as api
    monkeypatch.setattr(api, 'peek_intro_segment_cache', lambda *a, **k: {'start_sec': 10, 'end_sec': 70, 'source': 'introdb'})
    p._start_intro_skip_fetch()
    return getattr(p, '_intro_skip_active', False)


def test_first_episode_of_a_sitting_plays_the_intro(monkeypatch):
    p, props = _setup(monkeypatch)
    p._note_binge_sitting()
    assert not p._binge_consecutive
    assert props[player_mod.PROP_BINGE_SITTING] == '1400|100000'
    assert not _fetch_started(p, monkeypatch)


def test_same_show_inside_the_gap_is_consecutive_and_auto_approved(monkeypatch):
    p, props = _setup(monkeypatch, now=100000.0 + 20 * 60)
    props[player_mod.PROP_BINGE_SITTING] = '1400|100000'
    p._note_binge_sitting()
    assert p._binge_consecutive
    assert _fetch_started(p, monkeypatch)
    assert p._intro_skip_approved and p._intro_skip_prompt_answered


def test_same_show_past_the_gap_starts_a_new_sitting(monkeypatch):
    p, props = _setup(monkeypatch, now=100000.0 + 31 * 60)
    props[player_mod.PROP_BINGE_SITTING] = '1400|100000'
    p._note_binge_sitting()
    assert not p._binge_consecutive


def test_other_show_inside_the_gap_starts_a_new_sitting(monkeypatch):
    p, props = _setup(monkeypatch, now=100000.0 + 60)
    props[player_mod.PROP_BINGE_SITTING] = '999|100000'
    p._note_binge_sitting()
    assert not p._binge_consecutive
    assert props[player_mod.PROP_BINGE_SITTING] == '1400|100060'


def test_chain_play_is_always_consecutive(monkeypatch):
    p, props = _setup(monkeypatch, play_type='autoplay_nextep')
    p._note_binge_sitting()
    assert p._binge_consecutive


def test_stop_refreshes_the_sitting_clock(monkeypatch):
    p, props = _setup(monkeypatch, now=200000.0)
    p._touch_binge_sitting()
    assert props[player_mod.PROP_BINGE_SITTING] == '1400|200000'


def test_setting_off_leaves_the_skip_intro_mode_in_charge(monkeypatch):
    p, props = _setup(monkeypatch, binge=False, skip_mode=2, now=100000.0 + 60)
    props[player_mod.PROP_BINGE_SITTING] = '1400|100000'
    p._note_binge_sitting()
    assert _fetch_started(p, monkeypatch)
    assert p._intro_skip_approved
    p2, _ = _setup(monkeypatch, binge=False, skip_mode=0)
    p2._note_binge_sitting()
    assert not _fetch_started(p2, monkeypatch)
