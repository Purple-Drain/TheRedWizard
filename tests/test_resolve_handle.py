# -*- coding: utf-8 -*-
"""#179: playback routes hand Kodi's resolve handle back straight away (setResolvedUrl False),
so Kodi 22 doesn't report "One or more items failed to play" when a widget-started episode ends.
RunPlugin launches (handle -1) must be left alone.
"""
import types

import pytest

from modules import kodi_utils, router


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(kodi_utils.xbmcplugin, 'setResolvedUrl',
                        lambda handle, ok, listitem: seen.append(('resolve', handle, ok)), raising=False)
    return seen


# --- release_resolve_handle --------------------------------------------------------------

def test_real_handle_is_resolved_to_nothing(events):
    assert kodi_utils.release_resolve_handle(['plugin://plugin.video.redlight/', '3', '?mode=playback.media'])
    assert events == [('resolve', 3, False)]


@pytest.mark.parametrize('argv', [
    ['plugin://plugin.video.redlight/', '-1', '?mode=playback.media'],  # RunPlugin
    ['plugin://plugin.video.redlight/'],                                # no handle at all
    ['plugin://plugin.video.redlight/', '', ''],                        # not a number
    [],
])
def test_no_real_handle_leaves_kodi_alone(events, argv):
    assert not kodi_utils.release_resolve_handle(argv)
    assert events == []


def test_resolve_error_is_swallowed(monkeypatch):
    def boom(*a):
        raise RuntimeError('invalid handle')
    monkeypatch.setattr(kodi_utils.xbmcplugin, 'setResolvedUrl', boom, raising=False)
    assert not kodi_utils.release_resolve_handle(['plugin://plugin.video.redlight/', '7', ''])


# --- router ------------------------------------------------------------------------------

def _route(monkeypatch, events, query, handle='5'):
    import caches.settings_cache as settings_cache
    monkeypatch.setattr(settings_cache, 'sync_kodi_profile_context', lambda **k: None)
    monkeypatch.setattr(settings_cache, 'should_block_bootstrap_on_entry', lambda mode: False)
    monkeypatch.setattr(router, 'prepare_directory_listing', lambda mode: None)
    monkeypatch.setattr(kodi_utils, 'player_check', lambda mode, params: events.append(('play', mode)))
    import modules.next_episode_api as next_episode_api
    monkeypatch.setattr(next_episode_api, 'playback_next_episode', lambda params: events.append(('play', 'next')))
    router.routing(types.SimpleNamespace(argv=['plugin://plugin.video.redlight/', handle, query]))


@pytest.mark.parametrize('query, played', [
    ('?mode=playback.media&media_type=movie&tmdb_id=2292', 'playback.media'),
    ('?mode=playback.video&url=x', 'playback.video'),
    ('?mode=playback.next_episode&tmdb_id=2131', 'next'),
])
def test_playback_routes_release_the_handle_once_before_playing(monkeypatch, events, query, played):
    _route(monkeypatch, events, query)
    assert events == [('resolve', 5, False), ('play', played)]


def test_runplugin_playback_route_does_not_resolve(monkeypatch, events):
    _route(monkeypatch, events, '?mode=playback.media&media_type=movie&tmdb_id=2292', handle='-1')
    assert events == [('play', 'playback.media')]
