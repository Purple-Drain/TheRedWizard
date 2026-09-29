# -*- coding: utf-8 -*-
"""#1 C417: the Next key plays the prepared next episode straight from Kodi's playlist, and the
next Red Light play adopts it (no stop, no home screen, full Red Light behaviour)."""
import time

import modules.sources as sources_mod
from modules import player as player_mod
from modules.player import RedLightPlayer


class Props(dict):
    def get_property(self, k): return self.get(k, '')
    def set_property(self, k, v): self[k] = v
    def clear_property(self, k): self.pop(k, None)


class FakePlaylist:
    def __init__(self, items, position=0):
        self.items, self.position = list(items), position
    def size(self): return len(self.items)
    def getposition(self): return self.position
    def remove(self, url): self.items = [i for i in self.items if i != url]
    def add(self, url, listitem=None, index=-1):
        if index < 0: self.items.append(url)
        else: self.items.insert(index, url)


MARKER = 'plugin://plugin.video.redlight/?mode=playback.queued_next'
TOP = {'name': 'Friends.S02E03.mkv', 'scrape_provider': 'folders', 'url_dl': 'dav://zurg/Friends.S02E03.mkv'}
STASH = {'results': [TOP], 'meta': {'title': 'Friends', 'season': 2, 'episode': 3, 'ep_name': 'x'}, 'preresolved': None}


def _wire(monkeypatch, playlist, stash=STASH):
    props = Props()
    for name in ('get_property', 'set_property', 'clear_property'):
        monkeypatch.setattr(player_mod.ku, name, getattr(props, name))
    monkeypatch.setattr(player_mod.ku, 'logger', lambda *a: None)
    monkeypatch.setattr(player_mod.ku, 'make_playlist', lambda kind: playlist)
    monkeypatch.setattr(player_mod.ku, 'build_url', lambda params: MARKER)
    monkeypatch.setattr(sources_mod, 'nextep_autoplay_cancelled', lambda: False)
    monkeypatch.setattr(sources_mod, 'peek_nextep_autoplay_stash', lambda: stash)
    taken = []
    monkeypatch.setattr(sources_mod, 'take_nextep_autoplay_stash', lambda: taken.append(1) or dict(stash))
    scheduled = []
    monkeypatch.setattr(sources_mod, 'schedule_nextep_stashed_play', lambda s, show_busy=None, adopting=False: scheduled.append((s, adopting)) and False or True)
    player = object.__new__(RedLightPlayer)
    player._queued_next, player._queued_real, player._advanced_to_queued = True, None, False
    player.autoplay_nextep, player._own_index = True, 0
    player._log_nextep = lambda *a: None
    return player, props, scheduled


def test_folders_stash_replaces_the_marker_with_its_file(monkeypatch):
    playlist = FakePlaylist(['dav://zurg/Friends.S02E02.mkv', MARKER])
    player, _, _ = _wire(monkeypatch, playlist)
    player._maybe_queue_real_next()
    assert playlist.items == ['dav://zurg/Friends.S02E02.mkv', TOP['url_dl']]
    assert player._queued_real == {'url': TOP['url_dl'], 'index': 1}


def test_unresolved_debrid_top_keeps_the_marker(monkeypatch):
    playlist = FakePlaylist(['a', MARKER])
    stash = dict(STASH, results=[{'name': 'x', 'scrape_provider': 'external', 'url_dl': 'magnet:?'}])
    player, _, _ = _wire(monkeypatch, playlist, stash)
    player._maybe_queue_real_next()
    assert playlist.items == ['a', MARKER] and player._queued_real is None


def test_advance_hands_over_and_flags_the_adopt(monkeypatch):
    playlist = FakePlaylist(['a', MARKER])
    player, props, scheduled = _wire(monkeypatch, playlist)
    player._maybe_queue_real_next()
    assert player._queued_real_advanced() is False
    playlist.position = 1
    assert player._queued_real_advanced() is True
    assert player._hand_over_to_queued_real() is True
    assert props[player_mod.PROP_ADOPT_QUEUED] == 'true'
    assert scheduled[0][1] is True
    pre = scheduled[0][0]['preresolved']
    assert pre['url'] == TOP['url_dl'] and pre['item_key'] == sources_mod.nextep_preresolve_item_key(TOP)
    assert time.time() - pre['resolved_at'] < 5


def test_adopt_skips_the_open_and_queues_a_new_marker(monkeypatch):
    playlist = FakePlaylist(['a', TOP['url_dl']], position=1)
    player, props, _ = _wire(monkeypatch, playlist)
    props[player_mod.PROP_ADOPT_QUEUED] = 'true'
    player.is_generic, player.media_type = False, 'episode'
    player._queued_next = False
    player.isPlayingVideo = lambda: True
    monkeypatch.setattr(player_mod.xbmcgui, 'ListItem', lambda *a, **k: type('LI', (), {'setProperty': lambda self, k, v: None})())
    assert player._adopt_queued_play() is True
    assert player._own_index == 1 and playlist.items[-1] == MARKER and player._queued_next
    assert player_mod.PROP_ADOPT_QUEUED not in props


def test_no_adopt_flag_opens_normally(monkeypatch):
    playlist = FakePlaylist([])
    player, _, _ = _wire(monkeypatch, playlist)
    assert player._adopt_queued_play() is False


def test_marker_index_follows_the_adopted_position(monkeypatch):
    playlist = FakePlaylist(['a', 'b', MARKER], position=1)
    player, _, _ = _wire(monkeypatch, playlist)
    player._own_index = 1
    player._maybe_queue_real_next()
    assert playlist.items == ['a', 'b', TOP['url_dl']] and player._queued_real['index'] == 2


def test_adopted_play_counts_as_started_so_a_stop_is_a_stop(monkeypatch):
    playlist = FakePlaylist(['a', TOP['url_dl']], position=1)
    player, props, _ = _wire(monkeypatch, playlist)
    props[player_mod.PROP_ADOPT_QUEUED] = 'true'
    player.is_generic, player.media_type, player._queued_next = False, 'episode', False
    player._cb_started = False
    player.isPlayingVideo = lambda: True
    monkeypatch.setattr(player_mod.xbmcgui, 'ListItem', lambda *a, **k: type('LI', (), {'setProperty': lambda self, k, v: None})())
    player._adopt_queued_play()
    assert player._cb_started is True


def test_adopting_schedule_ignores_the_preps_own_busy_flag(monkeypatch):
    """W-280926-8: the Next key came while the prep's warm read still held sources_busy."""
    calls = []
    monkeypatch.setattr(sources_mod, 'nextep_autoplay_cancelled', lambda: False)
    monkeypatch.setattr(sources_mod, 'nextep_end_play_superseded', lambda *a: True)
    monkeypatch.setattr(sources_mod, '_nextep_stash_play_in_flight', lambda: False)
    monkeypatch.setattr(sources_mod, 'persist_nextep_play_stash', lambda s: True)
    monkeypatch.setattr(sources_mod, '_set_nextep_stash_play_in_flight', lambda a: None)
    monkeypatch.setattr(sources_mod.kodi_utils, 'logger', lambda *a: None)
    monkeypatch.setattr(sources_mod.kodi_utils, 'run_plugin', lambda p, block=False: calls.append(p))
    monkeypatch.setattr(sources_mod.settings, 'playback_key', lambda: 'media')
    stash = {'meta': {'title': 'Friends', 'season': 2, 'episode': 3}}
    assert sources_mod.schedule_nextep_stashed_play(stash, show_busy=False) is False
    assert sources_mod.schedule_nextep_stashed_play(stash, show_busy=False, adopting=True) is True
    assert len(calls) == 1


def test_real_queued_file_stays_to_the_end(monkeypatch):
    """end-keep: no 20 s drop for a real file, so a natural end flows into it."""
    playlist = FakePlaylist(['a', TOP['url_dl']])
    player, _, _ = _wire(monkeypatch, playlist)
    player._queued_real = {'url': TOP['url_dl'], 'index': 1}
    player.getTotalTime, player.getTime = (lambda: 1364.0), (lambda: 1350.0)
    player._maybe_drop_queued_next()
    assert playlist.items == ['a', TOP['url_dl']] and player._queued_real


def test_cancel_removes_the_real_queued_file(monkeypatch):
    playlist = FakePlaylist(['a', TOP['url_dl']])
    player, _, _ = _wire(monkeypatch, playlist)
    monkeypatch.setattr(sources_mod, 'nextep_autoplay_cancelled', lambda: True)
    player._queued_real = {'url': TOP['url_dl'], 'index': 1}
    player._maybe_drop_queued_next()
    assert playlist.items == ['a'] and player._queued_real is None


def test_premature_eof_is_not_an_end(monkeypatch):
    """C447: a broken stream 3 min in made Kodi move to the queued file; that is no natural end."""
    playlist = FakePlaylist(['a', TOP['url_dl']], position=1)
    player, _, _ = _wire(monkeypatch, playlist)
    player._queued_real = {'url': TOP['url_dl'], 'index': 1}
    player.total_time, player.curr_time, player._cb_ended = 1300.0, 180.0, True
    stopped = []
    player.stop = lambda: stopped.append(1)
    assert player._queued_real_advanced() is False
    assert player._premature_end and stopped == [1] and player._queued_real is None


def test_natural_end_and_own_step_still_advance(monkeypatch):
    for curr, stepping in ((1290.0, False), (200.0, True)):
        playlist = FakePlaylist(['a', TOP['url_dl']], position=1)
        player, _, _ = _wire(monkeypatch, playlist)
        player._queued_real = {'url': TOP['url_dl'], 'index': 1}
        player.total_time, player.curr_time, player._cb_ended = 1300.0, curr, True
        player._stepping = stepping
        assert player._queued_real_advanced() is True
