# -*- coding: utf-8 -*-
"""#1 update freeze: the service's threads stop on abort, and the stop names any that do not."""
import threading
import time

import service


def test_join_names_a_stuck_thread(monkeypatch):
    logged = []
    monkeypatch.setattr(service.kodi_utils, 'logger', lambda *a: logged.append(a))
    stop = threading.Event()
    monkeypatch.setattr(service, '_SERVICE_THREADS', [])
    service._start_daemon(lambda: None)
    stuck = service._start_daemon(lambda: stop.wait(5))
    started = time.time()
    service._join_service_threads(timeout=0.3)
    assert time.time() - started < 1.5
    assert 'still running' in logged[-1][1] and stuck.name in logged[-1][1]
    stop.set()


def test_pause_loops_check_abort():
    src = open(service.__file__).read()
    assert src.count("and not monitor.abortRequested(): wait_for_abort(10)") == 4
    assert 'xbmc.sleep(1000)' not in src
