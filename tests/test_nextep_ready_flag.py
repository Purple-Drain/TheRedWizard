# -*- coding: utf-8 -*-
"""#1 C418: the window property the skin's OSD "next episode ready" button shows on."""
import modules.sources as sources_mod
from modules import player as player_mod
from modules.player import RedLightPlayer


class Props(dict):
    def get_property(self, k): return self.get(k, '')
    def set_property(self, k, v): self[k] = v
    def clear_property(self, k): self.pop(k, None)


def _setup(monkeypatch, stash, cancelled=False):
    props = Props()
    for n in ('get_property', 'set_property', 'clear_property'): monkeypatch.setattr(player_mod.ku, n, getattr(props, n))
    box = [stash]
    monkeypatch.setattr(sources_mod, 'peek_nextep_autoplay_stash', lambda: box[0])
    monkeypatch.setattr(sources_mod, 'nextep_autoplay_cancelled', lambda: cancelled)
    p = object.__new__(RedLightPlayer)
    p.media_type, p.autoplay_nextep = 'episode', True
    return p, props, box


def test_flag_follows_the_stash(monkeypatch):
    p, props, box = _setup(monkeypatch, {'meta': {'season': 2, 'episode': 7, 'ep_name': 'The One'}})
    p._update_nextep_ready_flag()
    assert props[player_mod.PROP_NEXTEP_READY] == 'true'
    assert props[player_mod.PROP_NEXTEP_READY_LABEL] == 'S02E07 The One'
    box[0] = None
    p._update_nextep_ready_flag()
    assert player_mod.PROP_NEXTEP_READY not in props


def test_no_flag_when_cancelled(monkeypatch):
    p, props, _ = _setup(monkeypatch, {'meta': {'season': 2, 'episode': 7}}, cancelled=True)
    p._update_nextep_ready_flag()
    assert player_mod.PROP_NEXTEP_READY not in props
