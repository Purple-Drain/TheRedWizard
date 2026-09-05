# -*- coding: utf-8 -*-
"""Unit tests for #92's pure parts: JSON shaping and query->tmdb_id resolution.

The glue that actually calls build_single_episode('episode.next', ...) needs a live TMDb/
watched-db environment and is exercised on-device only (documented on the PR); these tests
cover next_episode_payload() and resolve_tmdb_id(), which do not touch Kodi/network at all.
"""
from modules.next_episode_api import next_episode_payload, resolve_tmdb_id


def test_next_episode_payload_shape():
    payload = next_episode_payload(4, 23, 'The Finale',
                                    'plugin://plugin.video.redlight/?mode=playback.media&tmdb_id=1400')
    assert payload == {
        'season': 4, 'episode': 23, 'title': 'The Finale',
        'file': 'plugin://plugin.video.redlight/?mode=playback.media&tmdb_id=1400',
    }


def test_resolve_tmdb_id_from_params():
    assert resolve_tmdb_id({'tmdb_id': '1400'}) == 1400


def test_resolve_tmdb_id_ignores_bad_tmdb_id():
    assert resolve_tmdb_id({'tmdb_id': 'not-a-number'}) is None


def test_resolve_tmdb_id_no_tmdb_id_no_query():
    assert resolve_tmdb_id({}) is None


def test_resolve_tmdb_id_via_query_first_result():
    def fake_search(query):
        assert query == 'Seinfeld'
        return {'results': [{'id': 1400, 'name': 'Seinfeld'}, {'id': 999, 'name': 'Other'}]}

    assert resolve_tmdb_id({'query': 'Seinfeld'}, search_fn=fake_search) == 1400


def test_resolve_tmdb_id_via_query_no_results():
    assert resolve_tmdb_id({'query': 'Nonexistent Show'}, search_fn=lambda q: {'results': []}) is None


def test_resolve_tmdb_id_via_query_search_raises():
    def broken_search(query):
        raise RuntimeError('network down')

    assert resolve_tmdb_id({'query': 'Seinfeld'}, search_fn=broken_search) is None


def test_resolve_tmdb_id_prefers_tmdb_id_over_query():
    assert resolve_tmdb_id({'tmdb_id': '1400', 'query': 'ignored'},
                            search_fn=lambda q: (_ for _ in ()).throw(AssertionError('should not be called'))) == 1400
