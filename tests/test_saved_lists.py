# -*- coding: utf-8 -*-
"""The shared saved-list engine (#163): what a second spec needs beyond Next Episodes' own tests.

A stand-in spec checks folder rows, the in-addon-only view, and that rebuild requests are per list:
one list being behind must never make another rebuild.
"""
import sys
import json
import sqlite3

import pytest

import caches.widget_cache as wc
import modules.kodi_utils as kodi_utils
import modules.nextep_list_cache as nlc
import modules.saved_lists as sl
from modules import settings


class _NoClose:
	def __init__(self, c): self._c = c
	def __getattr__(self, name): return getattr(self._c, name)
	def close(self): pass


class _Clock:
	def __init__(self): self.now = 1000.0
	def time(self): return self.now


class _Monitor:
	def __init__(self, clock, limit=400): self.clock, self.calls, self.limit = clock, 0, limit
	def abortRequested(self): return self.calls >= self.limit
	def waitForAbort(self, seconds):
		self.calls += 1
		self.clock.now += seconds
		return self.calls >= self.limit


@pytest.fixture
def env(monkeypatch):
	conn = sqlite3.connect(':memory:', check_same_thread=False)
	conn.isolation_level = None
	conn.execute('CREATE TABLE maincache (id text unique, data text, expires integer)')
	monkeypatch.setattr(wc, 'connect_database', lambda name: _NoClose(conn))
	props, directory, state = {}, {}, {'key': 'k1'}
	monkeypatch.setattr(kodi_utils, 'get_property', lambda name: props.get(name, ''))
	monkeypatch.setattr(kodi_utils, 'set_property', lambda name, value: props.__setitem__(name, value))
	monkeypatch.setattr(kodi_utils, 'external', lambda: True)
	monkeypatch.setattr(kodi_utils, 'make_listitem', lambda offscreen=True: None)
	monkeypatch.setattr(kodi_utils, 'kodi_actor', lambda: None)
	monkeypatch.setattr(kodi_utils, 'add_items', lambda handle, items: directory.__setitem__('items', items))
	monkeypatch.setattr(kodi_utils, 'set_content', lambda handle, content: directory.__setitem__('content', content))
	monkeypatch.setattr(kodi_utils, 'set_category', lambda handle, category: directory.__setitem__('category', category))
	monkeypatch.setattr(kodi_utils, 'end_directory', lambda handle, cacheToDisc=True: directory.__setitem__('ended', cacheToDisc))
	monkeypatch.setattr(kodi_utils, 'set_view_mode', lambda *a, **k: directory.__setitem__('view', a))
	monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: directory.setdefault('log', []).append(message))
	monkeypatch.setattr(settings, 'watched_indicators', lambda: 3)
	monkeypatch.setattr(sys, 'argv', ['plugin://plugin.video.redlight/', '1', ''])
	spec = sl.Spec('dummy', 'dummy_list', 'Dummy', 'tvshows', key=lambda is_external, variant: state['key'],
		render=lambda row, make_listitem, kodi_actor: ('listitem', row['label']), category='Dummy', view='view.tvshows',
		view_when_external=False)
	monkeypatch.setitem(sl._SPECS, 'dummy', spec)
	return spec, props, directory, state, conn


def test_folder_rows_round_trip_and_the_view_is_set_in_addon_only(env, monkeypatch):
	spec, props, directory, state, conn = env
	sl.store(spec, 'k1', True, False, [('u1', {'label': 'A'}, True), ('u2', {'label': 'B'}, False)], 'In Progress')
	assert sl.serve(spec, {})
	assert directory['items'] == [('u1', ('listitem', 'A'), True), ('u2', ('listitem', 'B'), False)]
	assert (directory['content'], directory['category'], directory['ended']) == ('tvshows', 'In Progress', False)
	assert 'view' not in directory  # a home widget: the TV show indexer sets no view there either
	assert 'Dummy list cache hit: 2 listed' in directory['log'][-1]
	monkeypatch.setattr(kodi_utils, 'external', lambda: False)
	sl.store(spec, 'k1', False, False, [('u1', {'label': 'A'}, True)], 'In Progress')
	assert sl.serve(spec, {}) and directory['view'] == ('view.tvshows', 'tvshows', False)


def test_a_rebuild_request_belongs_to_one_list(env):
	spec, props, directory, state, conn = env
	sl.store(spec, 'k1', True, False, [('u1', {'label': 'A'}, True)], 'In Progress')
	props[sl.REVALIDATE_PROP % spec.list_name(True)] = sl.REBUILD
	assert not sl.serve(spec, {})
	assert props[sl.REVALIDATE_PROP % spec.list_name(True)] == sl.DONE
	assert nlc.REVALIDATE_PROP not in props  # Next Episodes' own request is untouched


def test_the_service_asks_only_the_list_that_is_behind(env, monkeypatch):
	import time as time_module
	import service
	spec, props, directory, state, conn = env
	clock, refreshed = _Clock(), []
	monkeypatch.setattr(time_module, 'time', clock.time)
	monkeypatch.setattr(kodi_utils, 'kodi_refresh', lambda: refreshed.append(clock.now))
	monkeypatch.setattr(kodi_utils, 'service_shutting_down', lambda monitor=None: False)
	monkeypatch.setattr(kodi_utils, 'kodi_player', lambda: type('P', (), {'isPlayingVideo': lambda self: False})())
	monkeypatch.setattr(nlc, 'cache_key', lambda is_external, anime=False: 'n1')
	props[sl.SERVED_PROP % nlc.list_name(True)] = json.dumps({'key': 'n1', 'external': True, 'anime': False})
	props[sl.SERVED_PROP % spec.list_name(True)] = json.dumps({'key': 'k0', 'external': True, 'anime': False})
	service.SavedListsRevalidate().run(_Monitor(clock, limit=8))  # stops before REBUILD_WAIT
	assert len(refreshed) == 1
	assert props[sl.REVALIDATE_PROP % spec.list_name(True)] == sl.REBUILD
	assert nlc.REVALIDATE_PROP not in props
	assert 'dummy_list_widget shown from an older state' in directory['log'][-1]


def test_forget_all_drops_every_registered_list(env):
	spec, props, directory, state, conn = env
	sl.store(spec, 'k1', True, False, [('u1', {'label': 'A'}, True)], 'In Progress')
	conn.execute("INSERT INTO maincache VALUES (?, ?, ?)", (wc.LIST_PREFIX + nlc.list_name(True), json.dumps({'key': 'x', 'data': {}}), 2 ** 40))
	sl.forget_all()
	assert conn.execute("SELECT COUNT(*) FROM maincache").fetchone()[0] == 0
