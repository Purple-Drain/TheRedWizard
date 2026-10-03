# -*- coding: utf-8 -*-
"""optiplex-homelab#364: the next-episode in-flight flag is shared through a window property (the
schedule and the resolve run in different interpreters) and a stale one clears itself."""
import modules.sources as sources


def _props(monkeypatch):
    props = {}
    monkeypatch.setattr(sources.kodi_utils, 'get_property', lambda k: props.get(k, ''))
    monkeypatch.setattr(sources.kodi_utils, 'set_property', lambda k, v: props.__setitem__(k, v))
    monkeypatch.setattr(sources.kodi_utils, 'clear_property', lambda k: props.pop(k, None))
    monkeypatch.setattr(sources.kodi_utils, 'logger', lambda *a, **k: None)
    return props


def test_flag_lives_in_window_property(monkeypatch):
    props = _props(monkeypatch)
    sources._set_nextep_stash_play_in_flight(True)
    assert sources.PROP_NEXTEP_STASH_IN_FLIGHT in props
    assert sources._nextep_stash_play_in_flight()
    sources._set_nextep_stash_play_in_flight(False)
    assert not sources._nextep_stash_play_in_flight()


def test_stale_flag_clears(monkeypatch):
    props = _props(monkeypatch)
    props[sources.PROP_NEXTEP_STASH_IN_FLIGHT] = str(sources.time.time() - sources.NEXTEP_STASH_IN_FLIGHT_STALE_SEC - 5)
    assert not sources._nextep_stash_play_in_flight()
    assert sources.PROP_NEXTEP_STASH_IN_FLIGHT not in props


def test_garbage_flag_clears(monkeypatch):
    props = _props(monkeypatch)
    props[sources.PROP_NEXTEP_STASH_IN_FLIGHT] = 'true'
    assert not sources._nextep_stash_play_in_flight()
