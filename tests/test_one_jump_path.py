# -*- coding: utf-8 -*-
"""pd.98: every next-episode jump takes the same path (queue the real file, playnext, adopt)."""
import modules.sources as sources_mod
from modules import player as player_mod
from modules.player import RedLightPlayer


class Props(dict):
    def get_property(self, k): return self.get(k, '')
    def set_property(self, k, v): self[k] = v
    def clear_property(self, k): self.pop(k, None)


class FakePlaylist:
    def __init__(self, items): self.items = list(items)
    def size(self): return len(self.items)
    def getposition(self): return 0
    def remove(self, url): self.items = [i for i in self.items if i != url]
    def add(self, url, listitem=None, index=-1):
        if index < 0: self.items.append(url)
        else: self.items.insert(index, url)


MARKER = 'plugin://marker'


def _wire(monkeypatch, stash, playlist):
    props = Props({player_mod.PROP_SKIP_EPISODE: 'true'})
    for n in ('get_property', 'set_property', 'clear_property'): monkeypatch.setattr(player_mod.ku, n, getattr(props, n))
    for n in ('logger', 'notification'): monkeypatch.setattr(player_mod.ku, n, lambda *a, **k: None)
    monkeypatch.setattr(player_mod.ku, 'make_playlist', lambda kind: playlist)
    monkeypatch.setattr(player_mod.ku, 'build_url', lambda params: MARKER)
    box = [stash]
    monkeypatch.setattr(sources_mod, 'nextep_autoplay_cancelled', lambda: False)
    monkeypatch.setattr(sources_mod, 'nextep_end_play_superseded', lambda *a: False)
    monkeypatch.setattr(sources_mod, 'peek_nextep_autoplay_stash', lambda: box[0])
    def take(clear_only=False):
        s = box[0]; box[0] = None
        return None if clear_only else s
    monkeypatch.setattr(sources_mod, 'take_nextep_autoplay_stash', take)
    stash_plays = []
    monkeypatch.setattr(sources_mod, 'schedule_nextep_stashed_play', lambda s, show_busy=None, adopting=False: stash_plays.append(s) or True)
    p = object.__new__(RedLightPlayer)
    p.media_type, p.autoplay_nextep, p.media_marked = 'episode', True, False
    p._queued_next, p._queued_real, p._own_index = True, None, 0
    p._nextep_stash_play_scheduled = False
    p.media_watched_marker = lambda force_watched=False: None
    p._log_nextep = lambda *a: None
    p._disable_kodi_url_resume = lambda li: None
    stepped, preps = [], []
    p.playnext = lambda: stepped.append(1)
    p._schedule_next_ep = lambda: preps.append(1)
    return p, props, stepped, preps, stash_plays


FOLDERS = {'results': [{'name': 'e', 'scrape_provider': 'folders', 'url_dl': 'dav://z/e.mkv'}], 'meta': {'title': 'F', 'season': 2, 'episode': 7}}
DEBRID = {'results': [{'name': 'e', 'scrape_provider': 'external', 'url_dl': 'magnet:?x'}], 'meta': {'title': 'F', 'season': 2, 'episode': 7}, 'preresolved': None}


def test_skip_with_a_stash_not_yet_queued_queues_then_steps(monkeypatch):
    playlist = FakePlaylist(['cur', MARKER])
    p, props, stepped, preps, stash_plays = _wire(monkeypatch, FOLDERS, playlist)
    assert p._try_skip_to_stash() is True
    assert playlist.items == ['cur', 'dav://z/e.mkv'] and stepped == [1] and stash_plays == []


def test_unqueueable_stash_is_re_prepared_while_playing(monkeypatch):
    playlist = FakePlaylist(['cur', MARKER])
    p, props, stepped, preps, stash_plays = _wire(monkeypatch, DEBRID, playlist)
    assert p._try_skip_to_stash() is True
    assert preps == [1] and stepped == [] and stash_plays == []
    assert props[player_mod.PROP_SKIP_EPISODE_ACK] == 'true' and p._skip_prep_deadline


def test_queue_works_after_the_marker_was_dropped(monkeypatch):
    playlist = FakePlaylist(['cur'])
    p, props, stepped, preps, stash_plays = _wire(monkeypatch, FOLDERS, playlist)
    p._queued_next = False
    assert p._try_skip_to_stash() is True
    assert playlist.items == ['cur', 'dav://z/e.mkv'] and stepped == [1]
