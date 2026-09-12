# -*- coding: utf-8 -*-
"""The playback log's start and end rows (#141 needs stalls counted per provider; #72 redaction).

Pins the row shape, the end row's outcome and position columns, the SMB userinfo strip in both
link modes, and the one-time move of a file written under the old 11-column header.
"""
import os

import pytest

from modules import playback_log


class _Player(object):
    media_type, title, season, episode, tmdb_id = 'episode', 'Veep', 2, 9, 2947
    playing_filename = 'Veep.S02E09.mkv'
    playing_item = {'scrape_provider': 'tb_cloud', 'quality': '1080p', 'size': 6.84}
    url = 'https://store-026.example/dld/abc?token=SECRET'
    curr_time, total_time = 1287.4, 1703.0
    end_outcome = 'stall'


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / 'playback_log.tsv'
    monkeypatch.setattr(playback_log, '_log_path', lambda: str(path))
    from modules import settings
    monkeypatch.setattr(settings, 'playback_log_enabled', lambda: True)
    links = {'full': False}
    monkeypatch.setattr(settings, 'playback_log_include_links', lambda: links['full'])
    return path, links


def _rows(path):
    lines = path.read_text(encoding='utf-8').splitlines()
    header = lines[0].split('\t')
    return header, [dict(zip(header, line.split('\t'))) for line in lines[1:]]


def test_start_and_end_rows_share_identity_and_differ_in_event(log):
    path, _ = log
    playback_log.log_playback(_Player())
    playback_log.log_playback_end(_Player())
    header, rows = _rows(path)
    assert header == list(playback_log._COLUMNS)
    assert [r['event'] for r in rows] == ['start', 'end']
    assert rows[0]['outcome'] == '' and rows[0]['position'] == ''
    assert rows[1]['outcome'] == 'stall'
    assert (rows[1]['position'], rows[1]['total']) == ('1287', '1703')
    assert rows[0]['provider'] == rows[1]['provider'] == 'tb_cloud'
    assert 'SECRET' not in path.read_text(encoding='utf-8')


def test_end_row_without_an_outcome_says_unknown(log):
    path, _ = log
    player = _Player()
    player.end_outcome = None
    playback_log.log_playback_end(player)
    assert _rows(path)[1][0]['outcome'] == 'unknown'


def test_full_link_mode_keeps_the_token_but_never_the_share_login(log):
    path, links = log
    links['full'] = True
    player = _Player()
    player.url = 'smb://someuser:sharepw@10.1.1.22/debrid/shows/Veep/Veep.S02E09.mkv'
    playback_log.log_playback(player)
    text = path.read_text(encoding='utf-8')
    assert 'sharepw' not in text and 'someuser' not in text
    assert 'smb://10.1.1.22/debrid/shows/Veep/Veep.S02E09.mkv' in text


def test_old_header_file_is_moved_aside_once(log):
    path, _ = log
    old_header = '\t'.join(playback_log._COLUMNS[:11])
    path.write_text(old_header + '\nold row\n', encoding='utf-8')
    playback_log.log_playback(_Player())
    legacy = path.parent / playback_log._LEGACY_NAME
    assert legacy.read_text(encoding='utf-8') == old_header + '\nold row\n'
    header, rows = _rows(path)
    assert header == list(playback_log._COLUMNS) and len(rows) == 1
    # A current-header file is appended to, not moved.
    playback_log.log_playback_end(_Player())
    assert len(_rows(path)[1]) == 2
    assert sorted(os.listdir(str(path.parent))) == sorted([path.name, legacy.name])


def test_logging_off_writes_nothing(log, monkeypatch):
    path, _ = log
    from modules import settings
    monkeypatch.setattr(settings, 'playback_log_enabled', lambda: False)
    playback_log.log_playback(_Player())
    playback_log.log_playback_end(_Player())
    assert not path.exists()
