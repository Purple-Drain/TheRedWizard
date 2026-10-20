# -*- coding: utf-8 -*-
"""media-stack #52: a close() Kodi drops (window not up yet) must not leave the window open."""
import threading
import time

from modules import progress_windows


class LateWindow:
    """Ignores close() until it is 'up' (like a WindowXMLDialog before doModal shows it)."""
    def __init__(self, up_after=0.3):
        self.t0, self.up_after, self.done = time.time(), up_after, threading.Event()
        self.is_canceled = False
    def run(self): self.done.wait(10)
    def close(self):
        if time.time() - self.t0 >= self.up_after: self.done.set()


def _start(w):
    t = threading.Thread(target=w.run); t.start(); progress_windows.register(w, t); return t


def test_dropped_close_is_retried_until_the_window_ends():
    w = LateWindow(0.3); t = _start(w)
    assert progress_windows.close_window(w, t, timeout=2.0) is True
    assert not t.is_alive()


def test_close_all_closes_an_orphan_and_keeps_the_current_one():
    orphan, current = LateWindow(0.2), LateWindow(0.0)
    to, tc = _start(orphan), _start(current)
    assert progress_windows.close_all(2.0, keep=current) == 1
    assert not to.is_alive() and tc.is_alive()
    progress_windows.close_all(2.0)
    assert not tc.is_alive() and progress_windows.open_count() == 0


def test_a_single_close_would_have_left_it_open():
    w = LateWindow(0.3); t = _start(w)
    w.close(); t.join(0.5)
    assert t.is_alive()          # the old one-shot close: window still up
    progress_windows.close_all(2.0)
    assert not t.is_alive()
