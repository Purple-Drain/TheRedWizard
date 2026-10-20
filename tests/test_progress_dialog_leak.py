"""media-stack #52: the quiet-start sources window must never outlive the play that made it.

Run with pytest (tests/conftest.py puts the add-on's lib on the path).
Pulls _DeferredProgress and Sources._ensure_progress_dialog_dead out of sources.py with ast and
runs them against stub windows, so the real code is what is tested.
"""
import ast
import os
import threading
import time
import types

from modules import progress_windows

SRC = os.path.join(os.path.dirname(__file__), '..', 'plugin.video.redlight', 'resources', 'lib', 'modules', 'sources.py')


class FakeWindow:
	opened = []

	def __init__(self):
		self.closed = threading.Event()
		self.is_canceled = False
		FakeWindow.opened.append(self)

	def run(self): self.closed.wait(5)

	def close(self): self.closed.set()


def load():
	tree = ast.parse(open(SRC).read())
	cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == '_DeferredProgress')
	src_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Sources')
	meth = next(n for n in src_cls.body if isinstance(n, ast.FunctionDef) and n.name == '_ensure_progress_dialog_dead')
	ku = types.SimpleNamespace(sleep=lambda ms: time.sleep(ms / 1000.0), get_property=lambda k: 'true', logger=lambda *a: None)
	ns = {'progress_windows': progress_windows, 'Thread': threading.Thread, 'Lock': threading.Lock, 'kodi_utils': ku,
		'create_window': lambda *a, **k: FakeWindow(),
		'join_bounded': lambda threads, t, name: [x.join(t) for x in threads if x]}
	exec(compile(ast.Module(body=[cls, meth], type_ignores=[]), SRC, 'exec'), ns)
	return ns


def make_sources(ns):
	s = types.SimpleNamespace(meta={}, progress_dialog=None, progress_thread=None)
	s.ensure_dead = types.MethodType(ns['_ensure_progress_dialog_dead'], s)
	return s


def test_deferred_replaced_before_timer_never_opens():
	ns = load(); FakeWindow.opened.clear()
	s = make_sources(ns)
	s.progress_dialog = ns['_DeferredProgress'](s, 300)
	s.ensure_dead()  # the retry path: a new dialog is about to be made
	time.sleep(0.6)
	assert not FakeWindow.opened, 'deferred timer opened a window after the dialog was replaced'


def test_stale_materialize_does_not_take_thread_and_closes():
	ns = load(); FakeWindow.opened.clear()
	s = make_sources(ns)
	d = ns['_DeferredProgress'](s, 10 ** 6)
	current_thread = threading.Thread(target=lambda: None)
	s.progress_dialog, s.progress_thread = object(), current_thread  # moved on to a newer dialog
	assert d.materialize('test') is None
	assert s.progress_thread is current_thread, 'stale window took the current thread slot'
	assert FakeWindow.opened and FakeWindow.opened[0].closed.is_set(), 'stale window left open'


def test_current_materialize_still_shows():
	ns = load(); FakeWindow.opened.clear()
	s = make_sources(ns)
	s.progress_dialog = d = ns['_DeferredProgress'](s, 10 ** 6)
	assert d.materialize('test') is FakeWindow.opened[0]
	assert s.progress_thread is not None
	s.ensure_dead()
	assert FakeWindow.opened[0].closed.is_set()


if __name__ == '__main__':
	for name, fn in sorted(globals().items()):
		if name.startswith('test_'):
			fn(); print('ok', name)
