"""Tests for TMDb episode-group handling in indexers/episodes.py.

These import and call the real functions -- see #70 for why that needed a stub harness.
Fixtures are captured TMDb payloads for Seinfeld (tmdb_id 1400) and its "DVD order with double
episodes in single files" group, so the tests are hermetic and do not hit the API.

Run: python3 tests/test_episode_groups.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
import kodi_stub  # noqa: E402
kodi_stub.install()

sys.path.insert(0, os.path.join(ROOT, 'plugin.video.redlight', 'resources', 'lib'))
import indexers.episodes as ep  # noqa: E402


def _load(name):
    with open(os.path.join(HERE, 'fixtures', name)) as handle:
        return json.load(handle)


GROUP = _load('seinfeld_dvd_group.json')
SEASONS = _load('seinfeld_seasons.json')['seasons']
META = {'tmdb_id': 1400, 'season_data': SEASONS}

# Stand in for the TMDb per-season episode fetch: one dict per raw episode, which is all the
# traversal actually reads (it keys off 'episode' and passes the dict straight through).
_RAW = {s['season_number']: [{'season': s['season_number'], 'episode': n, 'title': 'S%02dE%02d' % (s['season_number'], n)}
                             for n in range(1, s['episode_count'] + 1)]
        for s in SEASONS}
ep.episodes_meta = lambda season, meta: _RAW.get(int(season), [])


def _pairs(items):
    return [(i['season'], i['episode']) for i in items]


failures = []


def check(label, got, want):
    ok = got == want
    print('%-58s %s' % (label, 'ok' if ok else 'FAIL  got=%r want=%r' % (got, want)))
    if not ok: failures.append(label)


# --- reachability gate -------------------------------------------------------------------
check('gate accepts a group whose buckets match raw seasons',
      ep._group_traversal_reachable(GROUP, META), True)

# Anime-style restructure: one long raw season split into several buckets. Buckets 2 and 3 have
# no raw season row, so traversal would strand them -- the gate must refuse.
SPLIT = {'groups': [{'order': n, 'episodes': [{'season_number': 1, 'episode_number': n, 'order': 0}]}
                    for n in (1, 2, 3)]}
SPLIT_META = {'tmdb_id': 999, 'season_data': [{'season_number': 1, 'episode_count': 3}]}
check('gate refuses a group with buckets that have no season row',
      ep._group_traversal_reachable(SPLIT, SPLIT_META), False)

check('gate refuses when the show has no season_data',
      ep._group_traversal_reachable(GROUP, {'tmdb_id': 1400, 'season_data': []}), False)

# --- season 3: an episode moved out to Specials and one moved to another season ------------
items, raw_seasons = ep._group_season_episodes(GROUP, 3, META)
check('season 3 lists 21 episodes', len(items), 21)
check('S03E18 (moved to Specials) is gone from season 3', (3, 18) in _pairs(items), False)
check('S03E10 (moved to season 2) is gone from season 3', (3, 10) in _pairs(items), False)
check('S03E17 still in season 3', (3, 17) in _pairs(items), True)
check('S03E19 still in season 3', (3, 19) in _pairs(items), True)

# --- season 2: gains the episode the group moved in ---------------------------------------
items2, raw2 = ep._group_season_episodes(GROUP, 2, META)
check('season 2 lists 13 episodes', len(items2), 13)
check('S03E10 "The Stranded" now appears in season 2', (3, 10) in _pairs(items2), True)
check('season 2 pulls metadata from raw seasons 2 and 3', sorted(raw2), [2, 3])
check('season 2 is in group order, not raw order',
      _pairs(items2)[:4], [(2, 1), (2, 2), (2, 12), (2, 10)])

# --- Specials: gains relocated episodes AND keeps ones the group never placed --------------
items0, _ = ep._group_season_episodes(GROUP, 0, META)
check('Specials lists 172 (168 bucket + 4 ungrouped)', len(items0), 172)
check('S03E18 is reachable in Specials', (3, 18) in _pairs(items0), True)
for orphan in (19, 61, 67, 163):
    check('ungrouped special S00E%-3d is still listed' % orphan, (0, orphan) in _pairs(items0), True)

# --- nothing is lost across the whole show -------------------------------------------------
total = sum(len(ep._group_season_episodes(GROUP, s['season_number'], META)[0]) for s in SEASONS)
check('every episode still reachable across all seasons', total, sum(s['episode_count'] for s in SEASONS))

# --- display mapping ------------------------------------------------------------------------
cache = {1400: GROUP}
check('display numbers map S03E10 to 2x09',
      ep._group_display_numbers(1400, 3, 10, None, cache), {'season': 2, 'episode': 9})
check('display numbers map S04E05 to 4x04',
      ep._group_display_numbers(1400, 4, 5, None, cache), {'season': 4, 'episode': 4})
check('display numbers leave raw Specials alone',
      ep._group_display_numbers(1400, 0, 5, None, cache), None)
check('display numbers are a no-op with no assigned group',
      ep._group_display_numbers(1400, 3, 10, None, {1400: None}), None)

print()
if failures:
    print('%d FAILED: %s' % (len(failures), failures))
    sys.exit(1)
print('all checks passed')
