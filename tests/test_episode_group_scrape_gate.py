"""Regression test for #79: an assigned TMDb episode group must not silently become the
scrape/search key.

Seinfeld's "DVD order" group folds raw S04E04 ("The Ticket") into the Specials bucket, which
shifts every later same-season episode's group number down by one. Before the fix,
Sources.check_episode_group() applied that shifted numbering as the scrape key whenever a show
had an assigned group, so playing raw S04E07 "The Bubble Boy" scraped/searched for S04E06 "The
Watch" -- a different, real episode, so the wrong file was found and played, and (see #79's
follow-up comment) could falsely mark an unplayed episode watched via the #54 combined-file path.

The fix (modules/settings.py: episode_group_scrape_remap(), default off) makes an explicit
per-show group assignment display-only for scraping unless the user opts in, matching the
existing "playback/watched/scrape params stay on raw numbering" design already documented in
indexers/episodes.py's _group_display_numbers(). The anime Seasons-order fallback keeps its own
separate, pre-existing opt-in (anime_seasons_episode_group_fallback) and is unaffected: it only
ever supplies a group when there is no explicit per-show assignment, so check_episode_group()
checking the assignment cache directly cannot swallow it.

Run: python3 tests/test_episode_group_scrape_gate.py
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
import modules.sources as sources  # noqa: E402
import modules.metadata as metadata  # noqa: E402
import modules.settings as settings  # noqa: E402
from caches.episode_groups_cache import episode_groups_cache  # noqa: E402

with open(os.path.join(HERE, 'fixtures', 'seinfeld_dvd_group.json')) as handle:
    GROUP = json.load(handle)

failures = []


def check(label, got, want):
    ok = got == want
    print('%-72s %s' % (label, 'ok' if ok else 'FAIL  got=%r want=%r' % (got, want)))
    if not ok: failures.append(label)


def _instance(season, episode):
    s = sources.Sources.__new__(sources.Sources)
    s.custom_season, s.custom_episode = None, None
    s.params = {}
    s.tmdb_id, s.episode_id = 1400, None
    s.season, s.episode = season, episode
    s.episode_group_used, s.episode_group_label = False, ''
    return s


# --- explicit per-show group assignment (the Seinfeld DVD-order case) ----------------------
episode_groups_cache.get = lambda tmdb_id: GROUP
metadata.resolve_assigned_episode_group = lambda tmdb_id: GROUP

settings.episode_group_scrape_remap = lambda: False
raw = _instance(4, 7)
raw.check_episode_group()
check('gate off: raw S04E07 keeps raw numbering as the scrape key (no S04E06 collision)',
      (raw.custom_season, raw.custom_episode), (None, None))
check('gate off: episode_group_used stays False', raw.episode_group_used, False)

settings.episode_group_scrape_remap = lambda: True
remapped = _instance(4, 7)
remapped.check_episode_group()
check('gate on (explicit opt-in): raw S04E07 remaps to the group\'s 4x06',
      (remapped.custom_season, remapped.custom_episode), (4, 6))
check('gate on: episode_group_used is True', remapped.episode_group_used, True)

# --- skip_episode_group_check still bypasses the remap outright, gate on or off ------------
settings.episode_group_scrape_remap = lambda: True
skipped = _instance(4, 7)
skipped.params = {'skip_episode_group_check': True}
skipped.check_episode_group()
check('skip_episode_group_check bypasses the remap even with the setting on',
      (skipped.custom_season, skipped.custom_episode), (None, None))

# --- anime Seasons-order fallback keeps its own opt-in, independent of the new setting ------
# No explicit per-show assignment (empty cache row, as EpisodeGroupsCache.get returns on a
# miss) -- resolve_assigned_episode_group() only returns a group here via the anime fallback,
# so the new gate must not require episode_group_scrape_remap() for this path.
episode_groups_cache.get = lambda tmdb_id: {}
settings.episode_group_scrape_remap = lambda: False
anime = _instance(4, 7)
anime.check_episode_group()
check('no explicit assignment + anime-fallback group still remaps with the new setting off',
      (anime.custom_season, anime.custom_episode), (4, 6))

print()
if failures:
    print('%d FAILED: %s' % (len(failures), failures))
    sys.exit(1)
print('all checks passed')
