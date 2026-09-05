# -*- coding: utf-8 -*-
"""thread_tools.py, the #133 helpers for joining threads before a plugin invocation ends.

Kodi tears an invocation's sub-interpreter down shortly after its main code returns; a
thread Redlight started and never joined can still be touching Python state when that
happens. These are pure-Python and need no Kodi stub.
"""
import threading
import time

from modules.thread_tools import StopFlag, TrackedThreads, join_bounded, live_thread_names


def _sleeper(seconds, flag=None):
    if flag is not None:
        flag.set()
    time.sleep(seconds)


# --- join_bounded ------------------------------------------------------------------------

def test_join_bounded_returns_empty_when_everything_finishes():
    started = threading.Event()
    t = threading.Thread(target=_sleeper, args=(0.01, started), name='quick')
    t.start()
    assert join_bounded([t], 1.0) == []


def test_join_bounded_returns_names_still_alive_at_deadline():
    started = threading.Event()
    t = threading.Thread(target=_sleeper, args=(1.0, started), name='slow-one')
    t.start()
    started.wait(1.0)
    alive = join_bounded([t], 0.05)
    assert alive == ['slow-one']
    t.join(2.0)  # don't leak the thread into later tests


def test_join_bounded_shares_one_deadline_across_threads():
    # Two threads, each sleeping longer than the shared deadline: the deadline is wall-clock
    # for the whole list, not per-thread, so this must not take ~2x the per-thread timeout.
    threads = [threading.Thread(target=_sleeper, args=(1.0,), name='a'),
               threading.Thread(target=_sleeper, args=(1.0,), name='b')]
    for t in threads: t.start()
    start = time.time()
    alive = join_bounded(threads, 0.1)
    elapsed = time.time() - start
    assert sorted(alive) == ['a', 'b']
    assert elapsed < 0.5
    for t in threads: t.join(2.0)


def test_join_bounded_ignores_none_and_empty_input():
    assert join_bounded([], 1.0) == []
    assert join_bounded(None, 1.0) == []
    assert join_bounded([None], 1.0) == []


# --- StopFlag ------------------------------------------------------------------------------

def test_stop_flag_starts_unset():
    flag = StopFlag()
    assert flag.is_set() is False
    assert bool(flag) is False


def test_stop_flag_stop_is_observed():
    flag = StopFlag()
    flag.stop()
    assert flag.is_set() is True
    assert bool(flag) is True


# --- TrackedThreads --------------------------------------------------------------------------

def test_tracked_threads_spawn_runs_the_target():
    tracked = TrackedThreads('test')
    result = {}
    tracked.spawn(lambda: result.__setitem__('ran', True), name='w')
    tracked.join_all(1.0)
    assert result.get('ran') is True


def test_tracked_threads_join_all_returns_still_alive_names():
    tracked = TrackedThreads('test')
    started = threading.Event()
    tracked.spawn(_sleeper, args=(1.0, started), name='slow')
    started.wait(1.0)
    alive = tracked.join_all(0.05)
    assert alive == ['slow']
    tracked.join_all(2.0)  # drain it so it doesn't leak into later tests


def test_tracked_threads_alive_reflects_running_threads():
    tracked = TrackedThreads('test')
    started = threading.Event()
    tracked.spawn(_sleeper, args=(1.0, started), name='running')
    started.wait(1.0)
    assert [t.name for t in tracked.alive()] == ['running']
    tracked.join_all(2.0)
    assert tracked.alive() == []


def test_tracked_threads_add_tracks_a_thread_started_elsewhere():
    tracked = TrackedThreads('test')
    result = {}
    t = threading.Thread(target=lambda: result.__setitem__('ran', True), name='external')
    t.start()
    tracked.add(t)
    assert tracked.join_all(1.0) == []
    assert result.get('ran') is True


# --- live_thread_names -----------------------------------------------------------------------

def test_live_thread_names_excludes_main_and_current():
    names = live_thread_names()
    assert threading.current_thread().name not in names
    assert threading.main_thread().name not in names


def test_live_thread_names_includes_other_live_threads():
    started = threading.Event()
    t = threading.Thread(target=_sleeper, args=(1.0, started), name='named-worker')
    t.start()
    started.wait(1.0)
    try:
        assert 'named-worker' in live_thread_names()
    finally:
        t.join(2.0)
