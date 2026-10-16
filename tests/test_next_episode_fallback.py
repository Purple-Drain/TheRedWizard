"""#266: first-episode fallback for playback.next_episode / next_episode.info."""
import os, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'plugin.video.redlight', 'resources', 'lib'))
import modules
from modules import next_episode_api as api


def _stub(watched_info_episode, monkeypatch):
	ws = types.SimpleNamespace(watched_info_episode=watched_info_episode, get_database=lambda ind: object())
	st = types.SimpleNamespace(watched_indicators=lambda: 0)
	monkeypatch.setitem(sys.modules, 'modules.watched_status', ws)
	monkeypatch.setitem(sys.modules, 'modules.settings', st)
	monkeypatch.setattr(modules, 'watched_status', ws, raising=False)
	monkeypatch.setattr(modules, 'settings', st, raising=False)


def test_first_episode_params_season_one_not_specials():
	p = api.first_episode_row_params('1400')
	assert (p['mode'], p['media_type'], p['tmdb_id'], p['season'], p['episode']) == ('playback.media', 'episode', '1400', '1', '1')


def test_fallback_when_never_watched(monkeypatch):
	_stub(lambda tmdb_id, db: [], monkeypatch)
	url, params, _ = api._first_episode_fallback(1400)
	assert params['season'] == '1' and 'tmdb_id=1400' in url and url.startswith('plugin://plugin.video.redlight/?')


def test_no_fallback_when_history_exists(monkeypatch):
	_stub(lambda tmdb_id, db: [(9, 17)], monkeypatch)
	assert api._first_episode_fallback(1400) is None


def test_read_error_fails_closed(monkeypatch):
	_stub(lambda *a: 1 / 0, monkeypatch)
	assert api._first_episode_fallback(1400) is None
