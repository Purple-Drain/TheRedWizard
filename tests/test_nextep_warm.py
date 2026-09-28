# -*- coding: utf-8 -*-
"""Warm reads only for network folder sources and links; a local folder has nothing to warm."""
from modules import nextep_warm as nw


def test_network_paths():
    for p in ('dav://10.1.1.22:9999/dav/x.mkv', 'smb://nas/tv/x.mkv', 'nfs://nas/x.mkv', 'https://cdn/x', 'HTTP://x'):
        assert nw.is_network_path(p)
    for p in ('/storage/emulated/0/Movies/x.mkv', 'special://home/x.mkv', 'C:\\tv\\x.mkv', '', None):
        assert not nw.is_network_path(p)


def test_local_folder_top_is_not_read(monkeypatch):
    monkeypatch.setattr(nw.settings, 'nextep_warm_read', lambda: True)
    reads, logged = [], []
    monkeypatch.setattr(nw, 'warm_read', lambda path, **k: reads.append(path) or (1, 1, ''))
    monkeypatch.setattr(nw, 'log_summary', lambda *a: logged.append(a))
    results = [{'scrape_provider': 'folders', 'url_dl': '/storage/emulated/0/tv/x.mkv', 'cache_provider': ''}]
    nw.warm_end_of_episode(results, {'title': 't', 'season': 1, 'episode': 2})
    assert reads == [] and 'local folder' in logged[0][-1]
