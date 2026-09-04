# -*- coding: utf-8 -*-
"""pytest-style example for the #70 harness: imports the real shipped function
directly (stubs come from conftest.py) instead of re-implementing it."""
from modules.source_utils import iter_season_episode_tokens


def test_single_token():
    assert list(iter_season_episode_tokens('Seinfeld.S04E07.720p.mkv')) == [(4, 7)]


def test_combined_double_episode_yields_both():
    assert list(iter_season_episode_tokens('Seinfeld.S03E15E16.mkv')) == [(3, 15), (3, 16)]


def test_no_token():
    assert list(iter_season_episode_tokens('Some.Movie.2020.1080p.mkv')) == []
