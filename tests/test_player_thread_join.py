# -*- coding: utf-8 -*-
"""play_video joins the threads it started before returning (#133).

Kodi tears an invocation's sub-interpreter down shortly after play_video returns; a thread
started during playback (a scrobble, a watched mark, a bookmark write) that outlives the
return races that teardown and has crashed the Shield with a SIGSEGV/SIGABRT in a
Python-named 'Thread-N'. play_video must join everything it tracked via self._spawn() before
it returns, on both the normal-end and the user-stop path.
"""
import time
import types

from modules import player as player_mod
from modules.player import RedLightPlayer


def _playable_player(monkeypatch, playback_successful):
    player = RedLightPlayer()
    player.is_generic = False
    monkeypatch.setattr(player, 'set_constants', lambda url, obj: None)
    monkeypatch.setattr(player, 'make_listing', lambda: None)
    monkeypatch.setattr(player, 'play', lambda *a, **k: None)
    monkeypatch.setattr(player_mod.ku, 'volume_checker', lambda: None)
    monkeypatch.setattr(player_mod.ku, 'set_property', lambda *a, **k: None)
    monkeypatch.setattr(player_mod.ku, 'clear_property', lambda *a, **k: None)
    monkeypatch.setattr(player, 'check_playback_start',
                        lambda: setattr(player, 'playback_successful', playback_successful))
    player.sources_object = types.SimpleNamespace(
        _release_resolve_busy=lambda: None, _release_sources_busy=lambda: None,
        playback_successful=None, cancel_all_playback=False, _resolve_user_cancelled=False,
        progress_dialog=None)
    monkeypatch.setattr(player, '_register_active_playback', lambda: None)
    monkeypatch.setattr(player, 'run_error', lambda *a, **k: None)
    monkeypatch.setattr(player, '_dismiss_kodi_playback_error_dialog', lambda: False)
    monkeypatch.setattr(player, 'kill_dialog', lambda: None)
    monkeypatch.setattr(player, 'safe_stop', lambda: None)
    return player


def _slow_job(result, seconds=0.05):
    time.sleep(seconds)
    result['done'] = True


def test_play_video_joins_a_thread_started_during_normal_playback_end(monkeypatch):
    player = _playable_player(monkeypatch, playback_successful=True)
    result = {}

    def fake_monitor():
        # Mirrors the real monitor(): scrobble-stop / watched-mark threads are started here,
        # via self._spawn, before monitor() returns.
        player._spawn(_slow_job, args=(result,), name='mark_watched')
    monkeypatch.setattr(player, 'monitor', fake_monitor)

    player.play_video('http://example/video', 'movie')

    assert result.get('done') is True, 'play_video returned before its own thread finished'
    assert player._bg.alive() == []


def test_play_video_joins_a_thread_started_on_the_user_stop_path(monkeypatch):
    # playback_successful False -> the safe_stop()/run_error() branch, the shape onPlayBack
    # takes when the user stops mid-open. A thread started there must be joined too.
    player = _playable_player(monkeypatch, playback_successful=False)
    result = {}

    real_safe_stop_target = {}
    def fake_safe_stop():
        player._spawn(_slow_job, args=(result,), name='stop_path_thread')
    monkeypatch.setattr(player, 'safe_stop', fake_safe_stop)

    player.play_video('http://example/video', 'movie')

    assert result.get('done') is True
    assert player._bg.alive() == []


def test_join_end_threads_logs_live_threads_at_exit(monkeypatch):
    player = RedLightPlayer()
    logged = []
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: logged.append(a))
    player._spawn(lambda: time.sleep(0.01), name='quick')
    player._join_end_threads(1.0)
    assert any('live threads at exit' in str(a[1]) for a in logged)


def test_spawn_falls_back_to_a_plain_thread_if_tracking_raises(monkeypatch):
    player = RedLightPlayer()
    monkeypatch.setattr(player._bg, 'spawn', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    result = {}
    thread = player._spawn(lambda: result.__setitem__('ran', True), name='fallback')
    thread.join(1.0)
    assert result.get('ran') is True
