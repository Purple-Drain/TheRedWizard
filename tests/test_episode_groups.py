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
import modules.metadata as md  # noqa: E402
import modules.watched_status as ws  # noqa: E402


def _load(name):
    with open(os.path.join(HERE, 'fixtures', name)) as handle:
        return json.load(handle)


GROUP = _load('seinfeld_dvd_group.json')
SEASONS = _load('seinfeld_seasons.json')['seasons']
META = {'tmdb_id': 1400, 'season_data': SEASONS}

# Stand in for the TMDb per-season episode fetch: one dict per raw episode, which is all the
# traversal actually reads (it keys off 'episode' and passes the dict straight through).
_RAW = {s['season_number']: [{'season': s['season_number'], 'episode': n, 'title': 'S%02dE%02d' % (s['season_number'], n),
                             'premiered': '2020-01-01'}
                             for n in range(1, s['episode_count'] + 1)]
        for s in SEASONS}
ep.episodes_meta = lambda season, meta: _RAW.get(int(season), [])
# _group_season_episodes() now delegates to metadata.group_season_bucket_episodes(), which calls
# metadata's own episodes_meta reference (not indexers.episodes's) -- patch both, or the delegated
# path hits the real TMDb-backed function and every check below silently sees empty seasons.
md.episodes_meta = ep.episodes_meta


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

# --- season counters must describe the list traversal actually renders ---------------------
counts = md.group_season_counts(GROUP, SEASONS)
check('counter for season 3 matches its listed length', counts.get(3), 21)
check('counter for season 2 matches its listed length', counts.get(2), 13)
check('counter for Specials includes ungrouped episodes', counts.get(0), 172)
check('counters sum to the show total', sum(counts.values()), sum(s['episode_count'] for s in SEASONS))

for season in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9):
    listed = len(ep._group_season_episodes(GROUP, season, META)[0])
    check('season %d: counter == rendered list' % season, counts.get(season), listed)

check('counters are empty when traversal is refused', md.group_season_counts(SPLIT, SPLIT_META['season_data']), {})
check('counters are empty with no group', md.group_season_counts(None, SEASONS), {})


# --- #75: watched numerator must be translated into the same space as the bucket-sized
# denominator, not stay raw-season-keyed against it -------------------------------------------
class _FakeWatchedDB:
    """Stands in for the sqlite connection watched_info_group_season() queries -- returns the
    given raw (season, episode) rows for any SELECT, exactly like watched_info_episode()'s own
    query would from a real 'watched' table."""
    def __init__(self, rows):
        self._rows = rows
    def execute(self, sql, params):
        return self
    def fetchall(self):
        return self._rows

# Watch all 13 items build_episode_list() shows under group season 2 (12 raw season-2 episodes
# plus raw S03E10 "The Stranded", which the group moved in) -- the DB gets 12 rows season=2, 1
# row season=3 (mark_episode() always writes raw season/episode, never the group's).
season2_binge = [(2, e) for e in range(1, 13)] + [(3, 10)]
group_watched = ws.watched_info_group_season(1400, GROUP, _FakeWatchedDB(season2_binge))
check('#75: watched S03E10 counts toward group season 2, not raw season 3', group_watched.get(2), 13)
check('#75: raw season 3 numerator unaffected by the S03E10 row', group_watched.get(3, 0), 0)
check('#75: numerator now reaches the season-2 bucket size (can complete)',
      group_watched.get(2) == md.group_season_counts(GROUP, SEASONS).get(2), True)

# Inverse: 20 of season 3's 21 rendered items, plus S03E10 (rendered under season 2, not 3).
season3_pairs = [(i['season'], i['episode']) for i in ep._group_season_episodes(GROUP, 3, META)[0]]
season3_almost = season3_pairs[:20] + [(3, 10)]
group_watched2 = ws.watched_info_group_season(1400, GROUP, _FakeWatchedDB(season3_almost))
check('#75: S03E10 does not inflate season 3 to a false 21/21', group_watched2.get(3), 20)

check('#75: no group assigned means an empty translated numerator (raw path unaffected)',
      ws.watched_info_group_season(1400, None, _FakeWatchedDB(season2_binge)), {})


# --- #76: "next episode" must walk the group's own order, not raw (season, episode) ----------
md.resolve_assigned_episode_group = lambda tmdb_id: GROUP

# Reproduction: the user binges every item build_episode_list() shows under group season 2 (see
# season2_binge above). raw S03E10 is the highest RAW (season, episode) watched, so
# get_next_episodes()'s "ORDER BY season DESC, episode DESC" picks it as "last watched" -- but in
# group order it's the *9th of 13* items in season 2's bucket, watched alongside the rest of that
# season, not after it.
seed = ws.group_corrected_next_seed(META, season2_binge, 3, 10)
check('#76: corrected seed is the actual last-in-group-order watched pair, not raw (3, 10)', seed, (2, 9))

naive_next = ws.get_next(3, 10, season2_binge, SEASONS, 0, META)
check('#76: walking on from (3, 10) itself follows group order (within-bucket), not raw increment',
      naive_next, (2, 6))

corrected_next = ws.get_next(seed[0], seed[1], season2_binge, SEASONS, 0, META)
check('#76: next after the corrected seed is S03E01, not S03E11 (raw S03E01-09 no longer skipped)',
      corrected_next, (3, 1))

check('#76: mode 1 (find-next-unwatched) also lands on S03E01, not skips over it',
      ws._find_next_unwatched_episode(2, 9, season2_binge, SEASONS, META), (3, 1))

md.resolve_assigned_episode_group = lambda tmdb_id: None
check('#76: with no group assigned, get_next() falls back to the raw walk unchanged',
      ws.get_next(3, 10, [], SEASONS, 0, META), (3, 11))
check('#76: seed correction is a no-op with no assigned group', ws.group_corrected_next_seed(META, season2_binge, 3, 10), (3, 10))
md.resolve_assigned_episode_group = lambda tmdb_id: GROUP


# --- #77: a still-airing season's group bucket must not count its own unaired episodes -------
AIRING_GROUP = {'groups': [{'order': 5, 'episodes': [{'season_number': 5, 'episode_number': n, 'order': n - 1} for n in range(1, 25)]}]}
AIRING_SEASON_DATA = [{'season_number': 5, 'episode_count': 24}]
AIRING_META = {'tmdb_id': 4242, 'season_data': AIRING_SEASON_DATA, 'status': 'Returning Series'}
_AIRING_EPS = [{'season': 5, 'episode': n, 'premiered': ('2026-01-%02d' % n) if n <= 6 else None} for n in range(1, 25)]
_prev_episodes_meta = md.episodes_meta
md.episodes_meta = lambda season, meta: (_AIRING_EPS if int(season) == 5 else _prev_episodes_meta(season, meta))
from datetime import date as _date
aired_count = ws.count_aired_group_season(AIRING_META, AIRING_GROUP, 5, current_date=_date(2026, 6, 1), adjust_hours=0)
check('#77: current-season bucket counts only the 6 aired episodes, not the full 24', aired_count, 6)
check('#77: the raw bucket size alone (pre-fix denominator) would have been the whole 24 -- confirms the bug this replaces',
      md.group_season_counts(AIRING_GROUP, AIRING_SEASON_DATA).get(5), 24)
md.episodes_meta = _prev_episodes_meta

# seasons.py's branch for #77 only routes through count_aired_group_season() when
# season_number == total_seasons (the same "current/latest season" discriminator the pre-existing
# non-group elif/else already used, per #66's changelog: Seinfeld's own S9 -- fully aired, Ended --
# already went through the date-counting branch pre-#69). Ended shows have real premiered dates
# for every past episode, so this must still land on the full bucket size, not undercount it.
ENDED_META = dict(META, total_seasons=9, status='Ended')
from datetime import date as _date2
season9_aired = ws.count_aired_group_season(ENDED_META, GROUP, 9, current_date=_date2(2026, 6, 1), adjust_hours=0)
check('#77: ended show, final season -- date-counting still reaches the full bucket size (22), no regression',
      season9_aired, md.group_season_counts(GROUP, SEASONS).get(9))

print()
if failures:
    print('%d FAILED: %s' % (len(failures), failures))
    sys.exit(1)
print('all checks passed')
