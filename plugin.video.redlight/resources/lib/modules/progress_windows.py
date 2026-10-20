# -*- coding: utf-8 -*-
"""Every SourcesPlayback window this invocation opened, so none outlives its play (media-stack #52).

A close() that reaches a WindowXMLDialog before its doModal has the window up is dropped by Kodi:
the window then opens anyway, modal, with nothing left holding it, and Kodi refuses fullscreen
video under it. Seen on the Shield 20.10.26 17:16: two sources_playback.xml loads 0.65 s apart,
the first never closed. So windows are registered as they are made, and closing one means
calling close() until its doModal thread has ended, not once.
"""
import time
from threading import Lock

_lock = Lock()
_live = []


def _alive(thread):
	try: return bool(thread is not None and thread.is_alive())
	except Exception: return False


def register(window, thread):
	with _lock:
		_live[:] = [(w, t) for w, t in _live if _alive(t)]
		_live.append((window, thread))


def close_window(window, thread, timeout=3.0, poll=0.1):
	"""close() repeatedly until the window's doModal thread ends. True when it ended."""
	deadline = time.time() + max(0.0, float(timeout or 0))
	while True:
		try:
			window.is_canceled = True
			window.close()
		except Exception: pass
		if not _alive(thread): return True
		if time.time() >= deadline: return False
		thread.join(poll)


def close_all(timeout=3.0, keep=None):
	"""Close every registered window except keep. Returns how many were still open."""
	with _lock:
		targets = [(w, t) for w, t in _live if w is not keep and _alive(t)]
	if not targets: return 0
	deadline = time.time() + max(0.0, float(timeout or 0))
	for window, thread in targets:
		close_window(window, thread, max(0.2, deadline - time.time()))
	with _lock:
		_live[:] = [(w, t) for w, t in _live if _alive(t)]
	return len(targets)


def open_count():
	with _lock:
		return sum(1 for _, t in _live if _alive(t))
