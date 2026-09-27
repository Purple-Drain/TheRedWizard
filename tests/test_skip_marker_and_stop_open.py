# -*- coding: utf-8 -*-
"""#1/#199 fixes from the 28.09.26 Shield runs (pd.86).

C6 (W-280926-1): Kodi dropped the queued marker as unplayable and ended the playlist, so the end
handling's playlist-position check missed the Next key. The marker's plugin call now leaves a
property the end handling reads.

Bug F (W-280926-2): a user Stop while a stream was still settling was treated as a failed open and
the next source played. A Stopped callback after the stream started is now a cancel.
"""
from modules import player as player_mod
from modules.player import RedLightPlayer


class Props(dict):
    def get_property(self, k): return self.get(k, '')
    def set_property(self, k, v): self[k] = v
    def clear_property(self, k): self.pop(k, None)


def _props(monkeypatch):
    props = Props()
    monkeypatch.setattr(player_mod.ku, 'get_property', props.get_property)
    monkeypatch.setattr(player_mod.ku, 'set_property', props.set_property)
    monkeypatch.setattr(player_mod.ku, 'clear_property', props.clear_property)
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    return props


def test_marker_call_is_seen_after_kodi_dropped_it(monkeypatch):
    props = _props(monkeypatch)
    monkeypatch.setattr(player_mod.ku, 'release_resolve_handle', lambda argv: None)
    monkeypatch.setattr(player_mod.ku, 'make_playlist', lambda kind: type('PL', (), {'getposition': lambda self: -1})())
    player_mod.queued_next_selected(['plugin://x', '1', ''])
    player = object.__new__(RedLightPlayer)
    player._queued_next = True
    assert player._queued_next_taken() is True
    assert player_mod.PROP_QUEUED_NEXT_HIT not in props


def test_no_marker_call_no_skip(monkeypatch):
    _props(monkeypatch)
    monkeypatch.setattr(player_mod.ku, 'make_playlist', lambda kind: type('PL', (), {'getposition': lambda self: -1})())
    player = object.__new__(RedLightPlayer)
    player._queued_next = True
    assert player._queued_next_taken() is False


def test_user_stop_while_opening_cancels(monkeypatch):
    _props(monkeypatch)
    monkeypatch.setattr(player_mod.ku, 'hide_busy_dialog', lambda: None)
    player = object.__new__(RedLightPlayer)
    player.playback_successful, player._cb_stopped = None, True
    player._playback_open_timeout_ms = lambda: 1000
    player.sources_object = type('S', (), {'cancel_all_playback': False, '_resolve_user_cancelled': False})()
    player.check_playback_start()
    assert player.playback_successful is False
    assert player.sources_object.cancel_all_playback is True
