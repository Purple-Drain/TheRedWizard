# -*- coding: utf-8 -*-
"""Which folders the folders scraper goes into, and when it opens a file (#172).

On the TCL on 14.09.26, with folder2 = zurg's dav://.../__torbox__/, a Clerks (1994) search went into
Friends 1994-2004 [S01-10] and Muriel's Wedding (1994) because their names hold the year, opened every
video file there for its size (146 x 401), and hit the 10 s deadline with nothing found. Every matching
show folder was also listed a second time as <folder>/<same name> (a 404), because subfolder paths had
no trailing slash and Kodi reads zurg's entry for the folder itself as a subfolder.
"""
import time

import pytest

import scrapers.folders as folders
import modules.kodi_utils as kodi_utils

ROOT = 'dav://10.1.1.22:9999/dav/__torbox__/'
FILM_LISTING = {
    ROOT: [('Friends 1994-2004 [S01-10]', 'folder'), ("Muriel's Wedding (1994)", 'folder'), ('Clerks (1994)', 'folder'),
           ('Clerks.1994.1080p.BluRay.mkv', 'file'), ('Mallrats.1995.1080p.mkv', 'file')],
    ROOT + 'Clerks (1994)/': [('Clerks.1994.Remastered.mkv', 'file'), ('Extras 1994', 'folder')],
    ROOT + 'Clerks (1994)/Extras 1994/': [('Clerks.1994.Commentary.mkv', 'file')],
}


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: lines.append((heading, message)))
    return lines


def _film_source(monkeypatch, listing=FILM_LISTING, filter_title=True):
    s = folders.source('folder2', 'TorBox', ROOT)
    listed, sized = [], []
    monkeypatch.setattr(s, '_cached_listing', lambda path: listed.append(path) or list(listing.get(path, [])))
    monkeypatch.setattr(s, '_get_size', lambda path: sized.append(path) or 1.0)
    s.extensions = ['.mkv']
    s.scrape_deadline = time.time() + 5
    s.media_type, s.title, s.year, s.season, s.episode = 'movie', 'Clerks', 1994, None, None
    s.aliases, s.filter_title, s.title_check = [], filter_title, None
    s.title_query, s.folder_query = 'clerks', s._year_query_list()
    return s, listed, sized


def test_film_search_skips_folders_that_match_only_the_year(monkeypatch):
    s, listed, _ = _film_source(monkeypatch)
    s._scrape_directory(ROOT, first_run=True, below_title=False)
    assert not any('Friends' in p or 'Muriel' in p for p in listed)
    assert ROOT + 'Clerks (1994)/' in listed


def test_year_folder_inside_the_title_folder_is_still_searched(monkeypatch):
    s, listed, _ = _film_source(monkeypatch)
    s._scrape_directory(ROOT, first_run=True, below_title=False)
    assert ROOT + 'Clerks (1994)/Extras 1994/' in listed


def test_subfolder_paths_end_with_a_slash(monkeypatch):
    s, listed, _ = _film_source(monkeypatch)
    s._scrape_directory(ROOT, first_run=True, below_title=False)
    assert listed and all(p.endswith('/') for p in listed)
    assert not any(p.endswith('Clerks (1994)/Clerks (1994)/') for p in listed)


def test_film_files_are_title_checked_before_their_size_is_read(monkeypatch):
    s, _, sized = _film_source(monkeypatch)
    s._scrape_directory(ROOT, first_run=True, below_title=False)
    assert sorted(p.rsplit('/', 1)[-1] for p in sized) == ['Clerks.1994.1080p.BluRay.mkv', 'Clerks.1994.Commentary.mkv', 'Clerks.1994.Remastered.mkv']
    assert sorted(r[0] for r in s.scrape_results) == ['Clerks.1994.1080p.BluRay.mkv', 'Clerks.1994.Commentary.mkv', 'Clerks.1994.Remastered.mkv']


def test_filter_by_name_off_still_reads_every_film_file(monkeypatch):
    s, _, sized = _film_source(monkeypatch, filter_title=False)
    s._scrape_directory(ROOT, first_run=True, below_title=False)
    assert any(p.endswith('Mallrats.1995.1080p.mkv') for p in sized)


def test_unreadable_size_is_logged_and_the_file_dropped(monkeypatch, logged):
    s, _, _ = _film_source(monkeypatch, listing={ROOT: [('Clerks.1994.1080p.BluRay.mkv', 'file')]})
    def boom(path): raise IOError('401')
    monkeypatch.setattr(s, '_get_size', boom)
    s._scrape_directory(ROOT, first_run=True, below_title=False)
    assert s.scrape_results == []
    assert logged == [('Red Light', 'folders: dropped %sClerks.1994.1080p.BluRay.mkv, its size could not be read (401)' % ROOT)]


# --- episodes: season folders count only inside the show's folder --------------------------

SHOWS = '/mnt/zurg/shows/'
SEINFELD = '/mnt/zurg/shows/Seinfeld/'
EPISODE_LISTING = {
    SHOWS: [('Seinfeld', 'folder'), ('Season 03', 'folder')],
    SEINFELD: [('Season 03', 'folder')],
    SEINFELD + 'Season 03/': [('Seinfeld.S03E01.The.Note.mkv', 'file')],
    SHOWS + 'Season 03/': [('Other.Show.S03E01.mkv', 'file')],
}


def _episode_source(monkeypatch, root):
    s = folders.source('folder1', 'Real-Debrid', root)
    listed = []
    monkeypatch.setattr(s, '_cached_listing', lambda path: listed.append(path) or list(EPISODE_LISTING.get(path, [])))
    monkeypatch.setattr(s, '_get_size', lambda path: 1.0)
    s.extensions = ['.mkv']
    s.scrape_deadline = time.time() + 5
    s.media_type, s.title, s.year, s.season, s.episode = 'episode', 'Seinfeld', 1989, 3, 1
    s.aliases, s.filter_title, s.title_check = [], True, None
    s.title_query, s.folder_query = 'seinfeld', s._season_query_list()
    return s, listed


def test_bare_season_folder_at_the_library_top_is_skipped(monkeypatch):
    s, listed = _episode_source(monkeypatch, SHOWS)
    s._scrape_directory(SHOWS, first_run=True, below_title=s._names_title(SHOWS))
    assert SHOWS + 'Season 03/' not in listed
    assert [r[0] for r in s.scrape_results] == ['Seinfeld.S03E01.The.Note.mkv']


def test_season_folder_at_the_top_of_a_show_folder_path_is_searched(monkeypatch):
    s, listed = _episode_source(monkeypatch, SEINFELD)
    assert s._names_title(SEINFELD)
    s._scrape_directory(SEINFELD, first_run=True, below_title=True)
    assert SEINFELD + 'Season 03/' in listed
    assert [r[0] for r in s.scrape_results] == ['Seinfeld.S03E01.The.Note.mkv']


@pytest.mark.parametrize('root', ['dav://10.1.1.22:9999/dav/__realdebrid__/', 'dav://10.1.1.22:9999/dav/__torbox__/',
                                  'dav://10.1.1.22:9999/dav/__magic__/tv/', 'dav://10.1.1.22:9999/dav/__magic__/movies/'])
@pytest.mark.parametrize('title_query', ['seinfeld', 'daria', 'clerks', 'real', 'magic', 'movies'])
def test_whole_library_paths_in_use_never_count_as_the_title_folder(root, title_query):
    """The four folder slots set on the Shield and the TCL (14/15.09.26). A title whose clean name
    is inside the slot's own folder name (Real, Magic) is the edge: '__realdebrid__' cleans to
    'realdebrid', which holds 'real', so a show called Real would let season folders count at the
    top of that slot. Only season-named folders are affected, and zurg's slot roots hold releases."""
    s = folders.source('folder1', 'Real-Debrid', root)
    s.title_query = title_query
    cleaned = {'__realdebrid__': 'realdebrid', '__torbox__': 'torbox', 'tv': 'tv', 'movies': 'movies'}[root.rstrip('/').rsplit('/', 1)[-1]]
    assert s._names_title(root) == (title_query in cleaned)


def test_scrape_without_results_setup_uses_safe_defaults(monkeypatch):
    """_film_file_matches reads title/aliases/filter_title; results() sets them, __init__ defaults them."""
    s = folders.source('folder2', 'TorBox', ROOT)
    assert (s.title, s.aliases, s.filter_title) == ('', [], True)


def test_as_dir_adds_one_slash_only_when_missing():
    s = folders.source('folder1', 'Real-Debrid', ROOT)
    assert s._as_dir('dav://h/dav/shows/Daria (1997)') == 'dav://h/dav/shows/Daria (1997)/'
    assert s._as_dir('dav://h/dav/shows/') == 'dav://h/dav/shows/'
    assert s._as_dir('smb://h/share\\') == 'smb://h/share\\'
