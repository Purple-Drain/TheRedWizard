# -*- coding: utf-8 -*-
"""#1 C426: no sources window when the source is known within the quiet-start delay."""
import modules.sources as sources_mod


class FakeWindow:
    def __init__(self): self.calls, self.closed = [], False
    def run(self): pass
    def close(self): self.closed = True
    def update_scraper(self, *a): self.calls.append(('update_scraper', a))
    def enable_resolver(self): self.calls.append(('enable_resolver', ()))


def _proxy(monkeypatch):
    windows = []
    monkeypatch.setattr(sources_mod, 'create_window', lambda *a, **k: windows.append(FakeWindow()) or windows[-1])
    monkeypatch.setattr(sources_mod.kodi_utils, 'sleep', lambda ms: None)
    monkeypatch.setattr(sources_mod.kodi_utils, 'logger', lambda *a: None)
    monkeypatch.setattr(sources_mod, 'Thread', lambda target=None, args=(), name=None, daemon=None: type('T', (), {'start': lambda self: None})())
    s = type('S', (), {'meta': {}, 'progress_thread': None})()
    return sources_mod._DeferredProgress(s, 1000), windows


def test_records_until_shown_then_replays(monkeypatch):
    p, windows = _proxy(monkeypatch)
    assert p.iscanceled() is False and p.skip_resolved() is False and bool(p)
    p.update_scraper(1, 2, 3)
    p.enable_resolver()
    assert windows == []
    p.materialize('test')
    assert windows and ('update_scraper', (1, 2, 3)) in windows[0].calls and ('enable_resolver', ()) in windows[0].calls


def test_suppressed_or_closed_never_shows(monkeypatch):
    p, windows = _proxy(monkeypatch)
    p.suppress()
    assert p.materialize('late timer') is None and windows == []
    q, windows2 = _proxy(monkeypatch)
    q.close()
    assert q.materialize('late timer') is None and windows2 == []


def test_attribute_writes_reach_the_window_later(monkeypatch):
    p, windows = _proxy(monkeypatch)
    p.is_canceled = True
    assert p.is_canceled is True
    p.materialize('x')
    assert windows[0].is_canceled is True


def test_close_during_materialize_closes_the_new_window(monkeypatch):
    p, windows = _proxy(monkeypatch)
    calls = {'n': 0}
    def get_property(k):
        calls['n'] += 1
        if calls['n'] == 1: p.close()   # the main thread kills the dialog while the timer builds it
        return 'true'
    monkeypatch.setattr(sources_mod.kodi_utils, 'get_property', get_property)
    assert p.materialize('timer') is None
    assert windows and windows[0].closed is True


def test_bare_reads_are_falsy_before_the_window(monkeypatch):
    p, _ = _proxy(monkeypatch)
    assert p.is_canceled is False and p.resume_choice is None and p.window_mode is None
    try:
        p.no_such_thing
        assert False
    except AttributeError:
        pass


def test_player_run_suppresses_a_pending_quiet_start(monkeypatch):
    from modules import player as player_mod
    p, windows = _proxy(monkeypatch)
    obj = type('O', (), {'progress_dialog': p})()
    player = object.__new__(player_mod.RedLightPlayer)
    monkeypatch.setattr(player_mod.ku, 'hide_busy_dialog', lambda: None)
    player.clear_playback_properties = lambda clear_navigation=False: None
    player.play_video = lambda url, o: None
    player.run('dav://x', obj)
    assert p.materialize('late') is None and windows == []
