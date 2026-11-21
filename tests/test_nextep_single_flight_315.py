# -*- coding: utf-8 -*-
"""#315: one Next Episodes build at a time. A request that finds a fresh build marker waits and then
serves the first build's list; a stale marker is ignored; a marker cleared without a store leads to a
build; the build's wrapper releases the marker on every exit. Also the refresher timer restart on the
playing-to-stopped edge."""
import modules.nextep_list_cache as nlc
import modules.kodi_utils as kodi_utils
import service

NAME = nlc.list_name(True, False)
PROP = nlc.BUILD_PROP % NAME


class Env:
	def __init__(self, monkeypatch):
		self.props, self.logs, self.now = {}, [], [1000.0]
		self.serve_results, self.waits = [], []
		monkeypatch.setattr(kodi_utils, 'get_property', lambda k: self.props.get(k, ''))
		monkeypatch.setattr(kodi_utils, 'set_property', lambda k, v: self.props.__setitem__(k, v))
		monkeypatch.setattr(kodi_utils, 'clear_property', lambda k: self.props.pop(k, None))
		monkeypatch.setattr(kodi_utils, 'logger', lambda h, m: self.logs.append(m))
		monkeypatch.setattr(kodi_utils, 'external', lambda: True)
		monkeypatch.setattr(kodi_utils, 'kodi_monitor', lambda: self)
		monkeypatch.setattr(nlc.time, 'time', lambda: self.now[0])
		monkeypatch.setattr(nlc.saved_lists, 'serve', lambda *a, **k: self.serve_results.pop(0))
		nlc._owned.clear()
		self.on_wait = lambda: None

	def waitForAbort(self, t):
		self.waits.append(t)
		self.now[0] += t
		self.on_wait()
		return False


def test_miss_sets_marker_and_release_clears_it(monkeypatch):
	env = Env(monkeypatch)
	env.serve_results = [False]
	assert nlc.serve({}) is False
	assert float(env.props[PROP]) == 1000.0
	nlc.release()
	assert PROP not in env.props


def test_waiter_serves_first_builds_list(monkeypatch):
	env = Env(monkeypatch)
	env.props[PROP] = str(env.now[0] - 2)
	env.serve_results = [True]
	# the first build finishes after 1 s of waiting
	env.on_wait = lambda: env.props.pop(PROP, None) if env.now[0] >= 1001 else None
	assert nlc.serve({}) is True
	assert env.waits and PROP not in env.props
	assert any('waited' in m and 'served its list' in m for m in env.logs)


def test_stale_marker_is_ignored(monkeypatch):
	env = Env(monkeypatch)
	env.props[PROP] = str(env.now[0] - nlc.BUILD_STALE_SEC - 1)
	env.serve_results = [False]
	assert nlc.serve({}) is False
	assert env.waits == []
	assert not any('waited' in m for m in env.logs)


def test_marker_cleared_without_store_leads_to_build(monkeypatch):
	env = Env(monkeypatch)
	env.props[PROP] = str(env.now[0])
	env.serve_results = [False]
	env.on_wait = lambda: env.props.pop(PROP, None)
	assert nlc.serve({}) is False
	assert any('waited' in m and m.endswith('built') for m in env.logs)
	assert PROP in env.props  # this request now owns the marker


def test_wait_is_capped(monkeypatch):
	env = Env(monkeypatch)
	env.props[PROP] = str(env.now[0])
	env.serve_results = [False]
	# marker stays fresh: refresh it so only the cap ends the wait
	env.on_wait = lambda: env.props.__setitem__(PROP, str(env.now[0]))
	nlc.serve({})
	assert nlc.BUILD_WAIT_SEC <= sum(env.waits) <= nlc.BUILD_WAIT_SEC + 1


def test_release_leaves_a_successors_marker(monkeypatch):
	env = Env(monkeypatch)
	env.serve_results = [False]
	nlc.serve({})
	env.props[PROP] = '2000.0'
	nlc.release()
	assert env.props[PROP] == '2000.0'


def test_release_does_nothing_when_not_owned(monkeypatch):
	env = Env(monkeypatch)
	env.props[PROP] = '999.0'
	nlc.release()
	assert env.props[PROP] == '999.0'


def test_build_wrapper_releases_on_every_exit(monkeypatch):
	import indexers.episodes as episodes
	released = []
	monkeypatch.setattr(episodes.nextep_list_cache, 'release', lambda: released.append(1))
	monkeypatch.setattr(episodes, '_build_single_episode', lambda lt, p={}: 'ok')
	assert episodes.build_single_episode('episode.next', {}) == 'ok'
	def boom(lt, p={}): raise RuntimeError('x')
	monkeypatch.setattr(episodes, '_build_single_episode', boom)
	try: episodes.build_single_episode('episode.next', {})
	except RuntimeError: pass
	assert len(released) == 2
	episodes.build_single_episode  # other list types never touch the marker
	monkeypatch.setattr(episodes, '_build_single_episode', lambda lt, p={}: None)
	episodes.build_single_episode('episode.progress', {})
	assert len(released) == 2


def test_refresher_timer_restarts_on_stopped_edge(monkeypatch):
	import time as real_time
	calls, states = [], iter([True, True, False, False])
	clock = [5000.0]
	monkeypatch.setattr(real_time, 'time', lambda: clock[0])

	class Mon:
		n = 0
		def waitForAbort(self, t): return False
		def abortRequested(self):
			Mon.n += 1
			return Mon.n > 5
	r = service.WidgetRefresher()
	monkeypatch.setattr(service.kodi_utils, 'kodi_player', lambda: type('P', (), {'isPlayingVideo': lambda s: next(states)})())
	monkeypatch.setattr(service.kodi_utils, 'service_shutting_down', lambda m: False)
	monkeypatch.setattr(service, 'get_setting', lambda k, d=None: '60')
	r.offset = 60
	r.set_next_refresh = lambda t: calls.append(t)
	r.condition_check = lambda: True
	r.run(Mon())
	# once at start, once on the True -> False edge, no more while stopped
	assert len(calls) == 2
