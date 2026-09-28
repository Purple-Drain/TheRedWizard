# -*- coding: utf-8 -*-
"""#1 bug H: play_file learns of a confirmed open before monitor() runs, so a Stop during
monitor()'s setup is not read as a failed source (Shield W-280926-9: __magic__ reopened)."""
from modules import player as player_mod
from modules.player import RedLightPlayer


def test_confirmed_open_is_reported_before_monitor(monkeypatch):
    for name in ('volume_checker', 'hide_busy_dialog'):
        monkeypatch.setattr(player_mod.ku, name, lambda *a, **k: None)
    monkeypatch.setattr(player_mod.ku, 'set_property', lambda *a: None)
    monkeypatch.setattr(player_mod.ku, 'clear_property', lambda *a: None)
    monkeypatch.setattr(player_mod.ku, 'get_property', lambda *a: '')
    player = object.__new__(RedLightPlayer)
    src = type('S', (), {'playback_successful': None, '_release_resolve_busy': lambda s: None, '_release_sources_busy': lambda s: None})()
    def set_constants(url, obj):
        player.url, player.sources_object, player.is_generic, player.media_type = url, src, False, 'episode'
    player.set_constants = set_constants
    player._resolve_cancelled = lambda: False
    player.make_listing = lambda: None
    player._adopt_queued_play = lambda: False
    player._play_with_queued_next = lambda li: True
    def check(): player.playback_successful = True
    player.check_playback_start = check
    player._register_active_playback = lambda: None
    seen = []
    player.monitor = lambda: seen.append(src.playback_successful)
    player._join_end_threads = lambda: None
    player.play_video('dav://x', 'obj')
    assert seen == [True]
