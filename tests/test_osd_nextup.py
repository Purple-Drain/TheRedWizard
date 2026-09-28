# -*- coding: utf-8 -*-
"""#1 C428: Red Light's Next Up dialog on OSD open, without blocking the monitor."""
import modules.sources as sources_mod
from modules import player as player_mod
from modules.player import RedLightPlayer


def _player(monkeypatch, osd=True, ready=True, props=None):
    props = {} if props is None else props
    monkeypatch.setattr(player_mod.ku, 'get_visibility', lambda c: osd)
    monkeypatch.setattr(player_mod.ku, 'get_property', lambda k: props.get(k, ''))
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    monkeypatch.setattr(sources_mod, 'peek_nextep_autoplay_stash', lambda: {'meta': {'title': 'F', 'season': 5, 'episode': 9}})
    p = object.__new__(RedLightPlayer)
    p._nextep_ready_shown, p._nextep_alert_shown = ready, False
    p.curr_time, p._intro_skip_active = 120.0, False
    if not ready: monkeypatch.setattr(sources_mod, 'peek_nextep_autoplay_stash', lambda: None)
    spawned = []
    p._spawn = lambda target, args=(), kwargs=None, name=None, daemon=False: spawned.append(name)
    return p, spawned


def test_opens_once_per_osd_opening_on_its_own_thread(monkeypatch):
    p, spawned = _player(monkeypatch)
    p._maybe_osd_nextup()
    p._osd_nextup_open = False
    p._maybe_osd_nextup()
    assert spawned == ['osd_nextup']
    monkeypatch.setattr(player_mod.ku, 'get_visibility', lambda c: False)
    p._maybe_osd_nextup()
    monkeypatch.setattr(player_mod.ku, 'get_visibility', lambda c: True)
    p._maybe_osd_nextup()
    assert spawned == ['osd_nextup', 'osd_nextup']


def test_not_without_a_prepared_episode_or_during_a_skip(monkeypatch):
    p, spawned = _player(monkeypatch, ready=False)
    p._maybe_osd_nextup()
    q, spawned2 = _player(monkeypatch, props={'redlight.skip_episode_requested': 'true'})
    q._maybe_osd_nextup()
    assert spawned == [] and spawned2 == []


def test_osd_mode_cancel_only_closes(monkeypatch):
    from windows.playback_notifications import NextEpisode
    d = object.__new__(NextEpisode)
    d.osd_mode, d.default_action, d.closed = True, 'close', False
    d.close = lambda: None
    monkeypatch.setattr('modules.kodi_utils.logger', lambda *a: None)
    d.onClick(12)
    assert d.selected == 'close'


def test_not_in_the_first_seconds_or_over_skip_intro(monkeypatch):
    p, spawned = _player(monkeypatch)
    p.curr_time = 5.0
    p._maybe_osd_nextup()
    q, spawned2 = _player(monkeypatch)
    q._intro_skip_active, q._intro_skip_done = True, False
    q._maybe_osd_nextup()
    assert spawned == [] and spawned2 == []


def test_finding_state_while_the_prep_runs(monkeypatch):
    p, spawned = _player(monkeypatch, ready=False, props={'redlight.nextep_pending': 'true'})
    p.meta, p.meta_get = {'title': 'F', 'season': 5, 'episode': 9}, None
    p.meta_get = p.meta.get
    p._maybe_osd_nextup()
    assert spawned == ['osd_nextup']


def test_info_line():
    from modules.player import RedLightPlayer
    stash = {'results': [{'quality': '4K', 'extraInfo': 'DV | HEVC | DTS-HD MA'}], 'meta': {'duration': 1320}}
    assert RedLightPlayer._nextup_info_line(stash) == '4K  ·  DV  ·  22 min'
