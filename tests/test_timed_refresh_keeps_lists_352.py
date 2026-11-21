# -*- coding: utf-8 -*-
"""The timed WidgetRefresher does not make saved widget lists due; a manual refresh does (#352).

Run with pytest (tests/test_saved_lists.py supplies the in-memory cache fixture).
"""
import time

import modules.kodi_utils as kodi_utils
import modules.saved_lists as sl
from test_saved_lists import env  # noqa: F401  (pytest fixture)


def _stub_refresh(monkeypatch):
	monkeypatch.setattr(kodi_utils, 'service_shutting_down', lambda monitor=None: False)
	monkeypatch.setattr(kodi_utils, 'kodi_refresh', lambda: None)
	monkeypatch.setattr(kodi_utils, 'home', lambda: False)
	monkeypatch.setattr(kodi_utils, 'notification', lambda *a, **k: None)
	import caches.random_widgets_cache as rwc
	import caches.lists_cache as lc
	monkeypatch.setattr(rwc.RandomWidgets, 'delete_like', lambda self, pattern: None)
	monkeypatch.setattr(lc.lists_cache, 'delete_like', lambda pattern: None)


def _kind(spec):
	return sl._choose(spec, True, False, sl.DONE)


def test_a_timed_refresh_leaves_a_saved_list_a_hit(env, monkeypatch):
	_stub_refresh(monkeypatch)
	spec, props, directory, state, conn = env
	sl.store(spec, 'k1', True, False, [('u1', {'label': 'A'}, True)], 'Dummy')
	time.sleep(1.1)
	kodi_utils.refresh_widgets(silent=True, mark_due=False)
	assert sl.REFRESHED_PROP not in props
	kind, _, _, _, reason = _kind(spec)
	assert kind == 'hit' and reason == ''


def test_a_manual_refresh_still_makes_a_saved_list_a_miss(env, monkeypatch):
	_stub_refresh(monkeypatch)
	spec, props, directory, state, conn = env
	sl.store(spec, 'k1', True, False, [('u1', {'label': 'A'}, True)], 'Dummy')
	time.sleep(1.1)
	kodi_utils.refresh_widgets(silent=True)
	assert props[sl.REFRESHED_PROP]
	kind, _, _, _, reason = _kind(spec)
	assert kind is None and reason == 'widgets refreshed since'


def test_a_timed_refresh_still_rebuilds_when_the_key_changed(env, monkeypatch):
	_stub_refresh(monkeypatch)
	spec, props, directory, state, conn = env
	sl.store(spec, 'k1', True, False, [('u1', {'label': 'A'}, True)], 'Dummy')
	state['key'] = 'k2'
	kodi_utils.refresh_widgets(silent=True, mark_due=False)
	kind, _, _, _, reason = _kind(spec)
	assert kind is None and reason == 'state changed since it was stored'
