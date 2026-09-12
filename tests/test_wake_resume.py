# -*- coding: utf-8 -*-
"""Service-side watched mark for a Redlight play Kodi resumed itself on wake (#143).

Kodi's RestorePlayerState reopens the file with no RedLightPlayer behind it, so nothing marked
it watched at EOF. modules/wake_resume.py tracks such a play from the service's JSON-RPC
Player.OnAVStart / Player.OnStop notifications. These tests drive it with a fake window-property
store, a fake JSON-RPC and a synchronous thread starter.
"""
import json
import types

import pytest

import modules.kodi_utils as kodi_utils
import modules.wake_resume as wake_resume

URL = 'https://store-034.wnam.tb-cdn.io/dld/a59f63bd-seinfeld-s04e01.mkv'
RECORD = {'url': URL, 'media_type': 'episode', 'tmdb_id': 1400, 'tvdb_id': 79169, 'imdb_id': 'tt0098904',
          'title': 'Seinfeld', 'year': 1989, 'season': 4, 'episode': 1}


def _time(seconds):
    seconds = int(seconds)
    return {'hours': seconds // 3600, 'minutes': seconds % 3600 // 60, 'seconds': seconds % 60, 'milliseconds': 0}


class Env:
    def __init__(self, monkeypatch):
        self.props, self.marks, self.checkpoints, self.logs = {}, [], [], []
        self.item = {'file': URL, 'uniqueid': {'tmdb': '1400'}, 'season': 4, 'episode': 1}
        self.position = (600, 1380)
        monkeypatch.setattr(kodi_utils, 'get_property', lambda k: self.props.get(k, ''))
        monkeypatch.setattr(kodi_utils, 'set_property', lambda k, v: self.props.__setitem__(k, v))
        monkeypatch.setattr(kodi_utils, 'logger', lambda h, m: self.logs.append(m))
        monkeypatch.setattr(kodi_utils, 'get_jsonrpc', self._jsonrpc)
        monkeypatch.setattr(wake_resume, '_watched_functions', lambda: (
            lambda p: self.marks.append(('episode', p)),
            lambda p: self.marks.append(('movie', p)),
            lambda p: self.checkpoints.append(p)))

    def _jsonrpc(self, request):
        if request['method'] == 'Player.GetItem':
            return {'item': self.item}
        if request['method'] == 'Player.GetProperties':
            curr, total = self.position
            return {'time': _time(curr), 'totaltime': _time(total)}
        return None

    def watcher(self, waits=(False,)):
        """waits: successive waitForAbort results. Default: grace passes, one poll, then the
        watch loop ends (as if Kodi were exiting) so the synchronous thread returns."""
        script = list(waits)
        wait = lambda seconds: script.pop(0) if script else True
        return wake_resume.WakeResumeWatcher(wait, start_thread=lambda target: target())


@pytest.fixture
def env(monkeypatch):
    e = Env(monkeypatch)
    e.props[wake_resume.LAST_PLAY_RECORD_PROP] = json.dumps(RECORD)
    return e


def _episode_marks(env):
    return [p for kind, p in env.marks if kind == 'episode']


def test_unclaimed_resume_marks_watched_once_at_eof(env):
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', json.dumps({'player': {'playerid': 1}}))
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    marks = _episode_marks(env)
    assert len(marks) == 1
    assert marks[0] == {'action': 'mark_as_watched', 'tmdb_id': 1400, 'title': 'Seinfeld', 'year': 1989, 'season': 4,
                        'episode': 1, 'tvdb_id': 79169, 'from_playback': 'true'}


def test_unclaimed_resume_writes_checkpoint_while_playing(env):
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    assert len(env.checkpoints) == 1
    cp = env.checkpoints[0]
    assert (cp['tmdb_id'], cp['season'], cp['episode'], cp['curr_time'], cp['total_time']) == (1400, 4, 1, 600, 1380)


def test_play_claimed_by_redlight_player_is_left_alone(env):
    env.props[wake_resume.ACTIVE_PLAYBACK_KEY_PROP] = '1400_4_1'
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert env.marks == [] and env.checkpoints == []


def test_redlight_claiming_after_grace_stops_tracking(env):
    watcher = env.watcher(waits=(False, False))
    original_poll = watcher._poll

    def poll_then_claim(tracked):
        original_poll(tracked)
        env.props[wake_resume.ACTIVE_PLAYBACK_KEY_PROP] = '1400_4_1'
    watcher._poll = poll_then_claim
    watcher.on_notification('Player.OnAVStart', '{}')
    env.props.pop(wake_resume.ACTIVE_PLAYBACK_KEY_PROP)
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert env.marks == []


def test_url_mismatch_does_nothing(env):
    env.item = {'file': 'https://elsewhere.example/other.mkv', 'uniqueid': {'tmdb': '999'}, 'season': 1, 'episode': 1}
    started = []
    watcher = wake_resume.WakeResumeWatcher(lambda s: True, start_thread=lambda target: started.append(target))
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert started == [] and env.marks == []


def test_fresh_start_synthetic_path_matches_on_tmdb_season_episode(env):
    env.item = {'file': 'Seinfeld S04E01 Seinfeld.S04E01.1080p', 'uniqueid': {'tmdb': '1400'}, 'season': 4, 'episode': 1}
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert len(_episode_marks(env)) == 1


def test_same_show_other_episode_does_not_match(env):
    env.item = {'file': 'https://elsewhere.example/s04e02.mkv', 'uniqueid': {'tmdb': '1400'}, 'season': 4, 'episode': 2}
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert env.marks == []


def test_stop_before_end_does_not_mark_watched(env):
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': False}))
    assert env.marks == []


def test_poll_past_ninety_percent_marks_once_even_with_eof_after(env):
    env.position = (1300, 1380)
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert len(_episode_marks(env)) == 1
    assert env.checkpoints == []


def test_movie_record_uses_mark_movie(env):
    env.props[wake_resume.LAST_PLAY_RECORD_PROP] = json.dumps(dict(RECORD, media_type='movie', season=None, episode=None))
    env.item = {'file': URL}
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert [kind for kind, _ in env.marks] == ['movie']


@pytest.mark.parametrize('raw', ['', '{not json', '[]', json.dumps({'url': URL}), json.dumps(dict(RECORD, season='x'))])
def test_missing_or_malformed_record_is_ignored(env, raw):
    env.props[wake_resume.LAST_PLAY_RECORD_PROP] = raw
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert env.marks == [] and env.checkpoints == []


def test_malformed_notification_data_does_not_raise(env):
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{broken')
    watcher.on_notification('Player.OnStop', '{broken')
    assert env.marks == []


def test_new_av_start_resets_tracking(env):
    watcher = env.watcher()
    watcher.on_notification('Player.OnAVStart', '{}')
    env.item = {'file': 'https://elsewhere.example/other.mkv'}
    watcher.on_notification('Player.OnAVStart', '{}')
    watcher.on_notification('Player.OnStop', json.dumps({'end': True}))
    assert env.marks == []


# --- write_last_play_record: what player.py persists -------------------------------------------

def _player(**overrides):
    base = dict(is_generic=False, media_type='episode', tmdb_id=1400, tvdb_id=79169, imdb_id='tt0098904', title='Seinfeld',
                year=1989, season=4, episode=1, url=URL + '|User-Agent=Kodi',
                playing_filename='Seinfeld.S04E01.The.Trip.Part.1.1080p.WEB-DL.mkv')
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_write_record_strips_headers_and_round_trips(env):
    env.props.clear()
    wake_resume.write_last_play_record(_player())
    record = wake_resume.read_last_play_record()
    assert record['url'] == URL and record['season'] == 4 and record['tmdb_id'] == 1400
    assert record['release'] == 'Seinfeld.S04E01.The.Trip.Part.1.1080p.WEB-DL.mkv'


@pytest.mark.parametrize('overrides', [{'is_generic': True}, {'media_type': 'video'}, {'tmdb_id': None}, {'url': ''}])
def test_write_record_skips_plays_with_nothing_to_mark(env, overrides):
    env.props.clear()
    wake_resume.write_last_play_record(_player(**overrides))
    assert wake_resume.LAST_PLAY_RECORD_PROP not in env.props


def test_active_playback_prop_matches_player():
    from modules import player
    assert wake_resume.ACTIVE_PLAYBACK_KEY_PROP == player.PROP_ACTIVE_PLAYBACK_KEY
