# -*- coding: utf-8 -*-
"""sources.py's own thread lifetime fixes (#133), on top of PR #135's player.py joins.

kodi.log named the survivor after PR #135 landed: 'live threads at exit: 1 [Thread-23 (run)]'
-- sources.py:2007's resolve progress-dialog thread, started unnamed and never joined on the
success path (only _kill_progress_dialog's failure/no-results paths joined it; play_file's
success path returned without calling it at all).

Pure/threading behaviour only: no real Kodi window, no real player. Sources is built with
object.__new__ to skip __init__ (which wants a live meta/settings context) and only the
attributes each method under test actually reads are set.
"""
import threading
import time

from modules.sources import Sources


def _bare_sources(**attrs):
    obj = object.__new__(Sources)
    obj.progress_dialog = None
    obj.progress_thread = None
    obj.background = False
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


class _FakeDialog(object):
    def __init__(self):
        self.is_canceled = False
        self.closed = False

    def close(self):
        self.closed = True


# --- _stop_progress_dialog_thread -------------------------------------------------------

def test_stop_progress_dialog_thread_signals_and_joins_a_running_thread():
    dialog = _FakeDialog()
    ready = threading.Event()
    stop = threading.Event()

    def _run():
        ready.set()
        stop.wait(2)

    src = _bare_sources(progress_dialog=dialog)
    src.progress_thread = threading.Thread(target=_run, name='resolve_progress_dialog')
    src.progress_thread.start()
    ready.wait(1)

    def _close():
        # A real dialog's close() would end the run() loop; the fake fakes that by
        # unblocking the waiting thread once told to close.
        stop.set()
    dialog.close = _close

    alive = src._stop_progress_dialog_thread(timeout=2.0)

    assert dialog.is_canceled is True
    assert alive == []
    assert not src.progress_thread.is_alive()


def test_stop_progress_dialog_thread_logs_but_does_not_hang_on_overrun():
    stop = threading.Event()
    src = _bare_sources(progress_dialog=_FakeDialog())
    src.progress_thread = threading.Thread(target=stop.wait, name='resolve_progress_dialog')
    src.progress_thread.start()
    try:
        start = time.time()
        alive = src._stop_progress_dialog_thread(timeout=0.1)
        elapsed = time.time() - start
        assert alive == ['resolve_progress_dialog']
        assert elapsed < 1.0, 'join must respect the bounded timeout, not the thread lifetime'
    finally:
        stop.set()
        src.progress_thread.join(2)


def test_stop_progress_dialog_thread_with_no_thread_is_a_noop():
    src = _bare_sources()
    assert src._stop_progress_dialog_thread(timeout=1.0) == []


# --- _resolve_sources_wait: StopFlag guards writes after abandonment --------------------

def test_resolve_sources_wait_abandons_before_worker_finishes_and_ignores_late_write():
    release = threading.Event()

    def _slow_resolve_sources(item, meta):
        release.wait(2)
        return {'late': True}

    src = _bare_sources(background=True)
    src.resolve_sources = _slow_resolve_sources
    src._user_cancelled_resolve = lambda: False

    # Force the deadline to have already passed on the worker's very first poll.
    original_time = time.time
    deadlines = {'value': None}

    def _fake_time():
        return original_time()
    # Monkeypatch the module-level time.time used inside sources.py's deadline math by
    # shrinking the wait: call the private method directly with a stub 0-length deadline
    # via the documented background/foreground split (background -> 90s) is too slow for a
    # unit test, so exercise the same code path with a tiny monkeypatched deadline instead.
    import modules.sources as sources_mod
    real_time_time = sources_mod.time.time
    try:
        # First call (deadline calc) reports "now"; every call after reports far in the future
        # so the poll loop's `time.time() >= deadline` trips on the very first iteration.
        calls = {'n': 0}

        def _ticking_time():
            calls['n'] += 1
            return real_time_time() if calls['n'] <= 1 else real_time_time() + 10_000
        sources_mod.time.time = _ticking_time

        result = src._resolve_sources_wait({'name': 'x'}, meta=None, poll_ms=1)
    finally:
        sources_mod.time.time = real_time_time
        release.set()

    assert result is None
    assert 'abandoned after' in src._resolve_note

    # The worker is still running (release not yet set when it observed stop); let it finish
    # and confirm its late write never overwrote anything the caller reads.
    time.sleep(0.05)
    release.set()
    time.sleep(0.05)
    assert src._resolve_note is not None and 'abandoned after' in src._resolve_note


def test_resolve_sources_wait_returns_the_result_when_not_abandoned():
    src = _bare_sources(background=True)
    src.resolve_sources = lambda item, meta: {'ok': True}
    src._user_cancelled_resolve = lambda: False

    result = src._resolve_sources_wait({'name': 'x'}, meta=None, poll_ms=1)

    assert result == {'ok': True}
