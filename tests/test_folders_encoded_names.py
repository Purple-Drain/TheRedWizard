# -*- coding: utf-8 -*-
"""The folders scraper with the URL-encoded names Kodi returns for WebDAV folders (#172).

xbmcvfs.listdir inside Kodi 22 on the TCL, 15.09.26, against zurg:
  __magic__/movies/                      -> 'Hunt%20for%20the%20Wilderpeople%20%282016%29'
  __realdebrid__/Daria%20%281997%29/     -> 'Daria%20%281997%29%20-%20S01e01%20-%20Esteemsters.mkv', ...
Asked for the decoded name ('Hunt for the Wilderpeople (2016)') the same listing came back empty.
Matching on the encoded text kept multi-word titles out of their folders and broke the S/E match
('...%20-%20S01e13...' puts '20' against 'S01e13'): pd.63 found nothing for Hunt for the Wilderpeople
in any of three folder slots, and Daria's episodes were never found.
"""
import time

import scrapers.folders as folders

MAGIC = 'dav://10.1.1.22:9999/dav/__magic__/movies/'
HUNT_DIR = 'Hunt%20for%20the%20Wilderpeople%20%282016%29'
HUNT_FILE = 'Hunt%20for%20the%20Wilderpeople%20%282016%29%20BDRip%201080p%20H.265%20%5BUkr%2CEng%20sub.Eng%5D%20%5BHurtom%5D.mkv'
RD = 'dav://10.1.1.22:9999/dav/__realdebrid__/'
DARIA_DIR = 'Daria%20%281997%29'
DARIA_FILES = ['Daria%20%281997%29%20-%20S01e12%20-%20The%20Teachings%20Of%20Don%20Jake.mkv',
               'Daria%20%281997%29%20-%20S01e13%20-%20The%20Misery%20Chick.mkv']


def _source(monkeypatch, root, listing, media_type, title, year, season=None, episode=None):
    s = folders.source('folder3', 'Magic', root)
    listed = []
    monkeypatch.setattr(s, '_cached_listing', lambda path: listed.append(path) or list(listing.get(path, [])))
    monkeypatch.setattr(s, '_get_size', lambda path: 1.0)
    s.extensions = ['.mkv']
    s.scrape_deadline = time.time() + 5
    s.media_type, s.title, s.year, s.season, s.episode = media_type, title, year, season, episode
    s.aliases, s.filter_title, s.title_check = [], True, None
    s.title_query = folders.source_utils.clean_title(folders.normalize(title))
    s.folder_query = s._season_query_list() if media_type == 'episode' else s._year_query_list()
    return s, listed


def test_multi_word_film_folder_is_entered_through_its_encoded_name(monkeypatch):
    listing = {MAGIC: [(HUNT_DIR, 'folder'), ('The%20Lobster%20%282015%29', 'folder')],
               MAGIC + HUNT_DIR + '/': [(HUNT_FILE, 'file')]}
    s, listed = _source(monkeypatch, MAGIC, listing, 'movie', 'Hunt for the Wilderpeople', 2016)
    s._scrape_directory(MAGIC, first_run=True, below_title=False)
    assert listed == [MAGIC, MAGIC + HUNT_DIR + '/']
    assert s.scrape_results == [('Hunt for the Wilderpeople (2016) BDRip 1080p H.265 [Ukr,Eng sub.Eng] [Hurtom].mkv',
                                 MAGIC + HUNT_DIR + '/' + HUNT_FILE, 1.0)]


def test_episode_matches_on_the_decoded_file_name(monkeypatch):
    listing = {RD: [(DARIA_DIR, 'folder')], RD + DARIA_DIR + '/': [(f, 'file') for f in DARIA_FILES]}
    s, listed = _source(monkeypatch, RD, listing, 'episode', 'Daria', 1997, season=1, episode=13)
    s._scrape_directory(RD, first_run=True, below_title=False)
    assert [r[0] for r in s.scrape_results] == ['Daria (1997) - S01e13 - The Misery Chick.mkv']
    assert s.scrape_results[0][1] == RD + DARIA_DIR + '/' + DARIA_FILES[1]


def test_decoded_names_leave_plain_names_alone(monkeypatch):
    """SMB and local listings return plain names; decoding them is a no-op."""
    listing = {RD: [('Seinfeld.S04.1080p', 'folder')], RD + 'Seinfeld.S04.1080p/': [('Seinfeld.S04E02.The.Pitch.mkv', 'file')]}
    s, _ = _source(monkeypatch, RD, listing, 'episode', 'Seinfeld', 1989, season=4, episode=2)
    s._scrape_directory(RD, first_run=True, below_title=False)
    assert s.scrape_results == [('Seinfeld.S04E02.The.Pitch.mkv', RD + 'Seinfeld.S04.1080p/Seinfeld.S04E02.The.Pitch.mkv', 1.0)]


def test_configured_path_with_an_encoded_title_folder_counts_as_the_title_folder():
    s = folders.source('folder1', 'Real-Debrid', RD + DARIA_DIR + '/')
    s.title_query = 'daria'
    assert s._names_title(RD + DARIA_DIR + '/')
    s.title_query = 'huntforthewilderpeople'
    assert s._names_title(MAGIC + HUNT_DIR)
