# -*- coding: utf-8 -*-
"""Thread lifetime helpers for a plugin invocation (#133).

Kodi runs each plugin invocation in its own sub-interpreter. When the invocation's main code
returns, CPythonInvoker polls every 100 ms until no other thread state is left in that
interpreter, then calls Py_EndInterpreter. Any thread Redlight started and never joined is
therefore either still being waited for (a reusable invoker cannot be reused meanwhile, and a
superseded one cannot be torn down) or is in its final microseconds when the teardown starts.
Three crashes on 05.09.26 died in a Python-named 'Thread-N' at exactly that moment.

Small tools, all pure Python so tests cover them without Kodi:

* join_bounded(threads, timeout, label)  -- one wall-clock deadline for a whole list of threads,
  returns the names still alive, logs them.
* StopFlag                                -- a threading.Event a worker checks before it touches
  Python or Kodi state after its owner gave up waiting for it (deadline-abandoned scraper
  threads, the resolve worker).
* TrackedThreads                          -- start-and-remember: spawn() starts a thread and keeps
  it, join_all() joins the lot against one deadline. For fire-and-forget threads (scrobbles,
  watched marks, cleanup calls) that used to be started with no handle kept.
* live_thread_names() / log_live_threads(label) -- the 'live threads at exit' line, so kodi.log
  names a culprit next time.
"""
import threading
import time


def _logger():
	try:
		from modules.kodi_utils import logger
		return logger
	except Exception:
		return lambda heading, message: None


def join_bounded(threads, timeout, label='threads', log=True):
	"""Join threads against one shared wall-clock deadline (not a timeout per thread).

	Returns the names of the threads still alive when the deadline trips (empty list when
	everything finished). A non-empty result is logged, so an overrun shows in kodi.log.
	"""
	threads = [t for t in (threads or []) if t is not None]
	if not threads: return []
	deadline = time.time() + max(0.0, float(timeout or 0))
	for thread in threads:
		remaining = deadline - time.time()
		if remaining <= 0: break
		try: thread.join(timeout=remaining)
		except Exception: pass
	alive = [thread.name for thread in threads if thread.is_alive()]
	if alive and log:
		_logger()('Red Light', '%s still running after the %.1fs join deadline: %s' % (label, float(timeout or 0), ', '.join(alive)))
	return alive


class StopFlag:
	"""Set by the owner when it stops waiting; checked by the worker before it does anything
	that touches shared state. is_set() is the only question a worker asks."""
	def __init__(self):
		self._event = threading.Event()

	def stop(self):
		self._event.set()

	def is_set(self):
		return self._event.is_set()

	def __bool__(self):
		return self._event.is_set()


class TrackedThreads:
	"""Start threads and keep their handles so the owner can join them before returning."""
	def __init__(self, label='background threads'):
		self.label = label
		self._threads = []
		self._lock = threading.Lock()

	def spawn(self, target, args=(), kwargs=None, name=None, daemon=False):
		thread = threading.Thread(target=target, args=args, kwargs=kwargs or {}, name=name, daemon=daemon)
		thread.start()
		return self.add(thread)

	def add(self, thread):
		"""Track a thread started elsewhere."""
		if thread is None: return thread
		with self._lock:
			self._threads = [t for t in self._threads if t.is_alive()]
			self._threads.append(thread)
		return thread

	def alive(self):
		with self._lock:
			return [t for t in self._threads if t.is_alive()]

	def join_all(self, timeout, log=True):
		"""Join every tracked thread against one deadline; returns the names still alive."""
		with self._lock:
			threads = list(self._threads)
		alive = join_bounded(threads, timeout, self.label, log=log)
		with self._lock:
			self._threads = [t for t in self._threads if t.is_alive()]
		return alive


def live_thread_names(exclude_current=True):
	"""Names of every live thread except the invocation's main thread (and, by default, the
	caller's own). Kodi's invocation thread is Python's MainThread inside the sub-interpreter."""
	main, current = threading.main_thread(), threading.current_thread()
	names = []
	for thread in threading.enumerate():
		if thread is main: continue
		if exclude_current and thread is current: continue
		names.append(thread.name)
	return names


def log_live_threads(label):
	"""One line at the end of an invocation: '<label>: live threads at exit: N [names]'."""
	names = live_thread_names()
	_logger()('Red Light', '%s: live threads at exit: %d [%s]' % (label, len(names), ', '.join(names)))
	return names
