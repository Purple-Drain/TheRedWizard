"""Regression test for #89: cloud/folder episode matching must be verified by the episode title
carried in the file name, because a library's SxxEyy numbering need not be TMDb's.

Seinfeld season 4 exists in three numberings at once. TMDb aired order: E20 The Junior Mint,
E21 The Smelly Car, E22 The Handicap Spot. The assigned "DVD order" group (tests/fixtures/
seinfeld_dvd_group.json): #19 Handicap Spot, #20 Junior Mint, #21 Smelly Car. The TorBox files
seen in the Shield's kodi.log on 03.09.26: "Seinfeld S04E20 The Handicap Spot mkv", "Seinfeld
S04E21 The Junior Mint mkv" (TVDB's DVD order). A number-only match on either key lands on a
real, different episode, so on 03.09.26 raw S04E20 played The Handicap Spot and autoplay's
S04E21 played The Junior Mint.

Run: python3 tests/test_cloud_title_match.py
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
import modules.source_utils as su  # noqa: E402

with open(os.path.join(HERE, 'fixtures', 'seinfeld_dvd_group.json')) as handle:
    GROUP = json.load(handle)

# Raw TMDb season 4 titles, recovered from the group fixture's raw (season_number, episode_number, name).
SEASON4 = {}
for grp in GROUP['groups']:
    for ep in grp['episodes']:
        if ep['season_number'] == 4: SEASON4[ep['episode_number']] = ep['name']
assert SEASON4[20] == 'The Junior Mint' and SEASON4[21] == 'The Smelly Car' and SEASON4[22] == 'The Handicap Spot'

FILES = {
    'handicap': 'Seinfeld S04E20 The Handicap Spot mkv',
    'handicap_synd': 'Seinfeld S04E20 The Handicap Spot (Syndicated Jerry Stiller Version) mkv',
    'junior': 'Seinfeld S04E21 The Junior Mint mkv',
    'smelly': 'Seinfeld S04E22 The Smelly Car mkv',
    'boyfriend': 'Seinfeld S03E15 E16 The Boyfriend mkv',
    'watch': 'Seinfeld S04E06 The Watch mkv',
    'pack': 'Seinfeld S04 UHD BluRay 2160p DTS HD MA 5 1 HDR HEVC REMUX',
    'bare': 'Seinfeld.S04E20.1080p.BluRay.x265-GROUP.mkv',
}

failures = []


def check(label, got, want):
    ok = got == want
    print('%-78s %s' % (label, 'ok' if ok else 'FAIL  got=%r want=%r' % (got, want)))
    if not ok: failures.append(label)


def matcher(episode):
    return su.EpisodeTitleCheck(SEASON4[episode], 4, SEASON4.values())


def matches(episode, name, remap_episode=None):
    """cloud_episode_matches with the key the addon would use: raw (default) or a remapped number."""
    key = remap_episode if remap_episode is not None else episode
    return su.cloud_episode_matches(4, key, name, None, matcher(episode))


print('--- the 03.09.26 incident, raw key (pd.20 default) ---')
check('raw S04E20 Junior Mint no longer accepts "S04E20 The Handicap Spot"', matches(20, FILES['handicap']), False)
check('raw S04E20 Junior Mint rejects the syndicated Handicap Spot cut too', matches(20, FILES['handicap_synd']), False)
check('raw S04E20 Junior Mint accepts "S04E21 The Junior Mint" by title', matches(20, FILES['junior']), True)
check('raw S04E21 Smelly Car no longer accepts "S04E21 The Junior Mint"', matches(21, FILES['junior']), False)
check('raw S04E21 Smelly Car accepts "S04E22 The Smelly Car" by title', matches(21, FILES['smelly']), True)
check('raw S04E22 Handicap Spot accepts "S04E20 The Handicap Spot" by title', matches(22, FILES['handicap']), True)

print('--- same library, group key (episode_group_scrape_remap on: Handicap Spot = 19) ---')
check('group key 19 for Handicap Spot still finds "S04E20 The Handicap Spot"', matches(22, FILES['handicap'], remap_episode=19), True)
check('group key 20 for Junior Mint still finds "S04E21 The Junior Mint"', matches(20, FILES['junior'], remap_episode=20), True)

print('--- no title evidence: numeric behaviour unchanged ---')
check('bare release, matching number, still matches', matches(20, FILES['bare']), True)
check('bare release, other number, still rejected', matches(21, FILES['bare']), False)
check('season pack without an episode token is still not an episode match', matches(20, FILES['pack']), False)
check('no title_check argument keeps the old numeric result', su.cloud_episode_matches(4, 20, FILES['handicap']), True)

print('--- combined and out-of-season files ---')
check('title hit in another season is not accepted (season token disagrees)',
      su.EpisodeTitleCheck('The Watch', 3, ())(FILES['watch']), None)
boyfriend_titles = ['The Boyfriend (1)', 'The Boyfriend (2)', 'The Fix-Up', 'The Limo']
check('"The Boyfriend (2)" accepts the combined S03E15 E16 file',
      su.EpisodeTitleCheck('The Boyfriend (2)', 3, boyfriend_titles)(FILES['boyfriend']), True)
check('"The Fix-Up" vetoes the Boyfriend file even at a matching number',
      su.EpisodeTitleCheck('The Fix-Up', 3, boyfriend_titles)(FILES['boyfriend']), False)

print('--- titles that must never accept or veto ---')
for title in ('Pilot', 'Episode 3', 'Part 2', 'Ep 12', 'Web', ''):
    check('generic/short target %r is dropped' % title, bool(su.EpisodeTitleCheck(title, 1, ())), False)
check('generic titles in the season list never veto',
      su.EpisodeTitleCheck('The Watch', 4, ['Pilot', 'Episode 3', 'The Watch'])('Show S04E01 Pilot mkv'), None)
check('the target title is never in its own veto set', 'the junior mint' in matcher(20).others, False)
check('accent folding: "The Cafe" file matches title "The Café"',
      su.EpisodeTitleCheck('The Café', 3, ())('Seinfeld S03E07 The Cafe mkv'), True)
check('punctuation folding: "The FixUp" file matches title "The Fix-Up"',
      su.EpisodeTitleCheck('The Fix-Up', 3, ())('Seinfeld S03E17 The FixUp mkv'), True)

print('--- episode_title_check() builds from search_info and tolerates an empty cache ---')
info = {'media_type': 'episode', 'tmdb_id': 1400, 'season': 4, 'episode': 20, 'ep_name': 'The Junior Mint'}
built = su.episode_title_check(info)
check('returns a check when only ep_name is available', built is not None and built.target, 'the junior mint')
check('movie search_info gets no check', su.episode_title_check({'media_type': 'movie', 'title': 'Heat'}), None)
check('episode without a usable title gets no check', su.episode_title_check(dict(info, ep_name='Pilot')), None)

print()
if failures:
    print('%d FAILED: %s' % (len(failures), failures))
    sys.exit(1)
print('all cloud-title-match checks passed')
