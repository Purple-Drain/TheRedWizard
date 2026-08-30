"""Validates tools/audit_false_watched_marks.py (#80) against a fabricated watched.db reproducing
the #79 collision: raw S04E05 marked watched seconds after raw S04E06, matching the shifted-key
"-1 after a fold" signature the real Seinfeld DVD-order group fixture produces for season 4.

Run: python3 tests/test_audit_false_watched_marks.py
"""
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import audit_false_watched_marks as audit  # noqa: E402

TMDB_ID = 1400
with open(os.path.join(HERE, 'fixtures', 'seinfeld_dvd_group.json')) as handle:
	GROUP = json.load(handle)

failures = []

def check(label, got, want):
	ok = got == want
	print('%-72s %s' % (label, 'ok' if ok else 'FAIL  got=%r want=%r' % (got, want)))
	if not ok: failures.append(label)

def make_db(rows):
	"""rows: list of (season, episode, last_played) for tmdb_id TMDB_ID."""
	path = tempfile.mktemp(suffix='.db')
	conn = sqlite3.connect(path)
	conn.execute('CREATE TABLE watched (db_type TEXT, media_id TEXT, season TEXT, episode TEXT, last_played TEXT, title TEXT)')
	for season, episode, last_played in rows:
		conn.execute('INSERT INTO watched VALUES (?, ?, ?, ?, ?, ?)', ('episode', str(TMDB_ID), season, episode, last_played, 'S%02dE%02d' % (season, episode)))
	conn.commit()
	conn.close()
	return path

# --- fabricated false-positive: S04E05 marked watched 8s after S04E06, reproducing #79's
# advance-past-duplicate collision (S04E05's shifted key is S04E04, the "-1 after a fold" shape).
db_path = make_db([
	(4, 1, '2026-08-30 20:00:00'),
	(4, 2, '2026-08-30 20:25:00'),
	(4, 3, '2026-08-30 20:50:00'),
	# raw S04E04 "The Ticket" is relocated into Specials by the group -- never a real S04 watched row.
	(4, 5, '2026-08-30 21:15:00'),  # false positive: never actually played
	(4, 6, '2026-08-30 21:15:08'),  # real play, 8s after -- what actually triggered the mark above
	(4, 7, '2026-08-30 21:45:00'),  # genuine later play, 30 min after S04E06 -- not suspicious
])

rows = audit.load_watched_rows(db_path, TMDB_ID)
check('loads all 6 fabricated rows', len(rows), 6)

candidates = audit.find_candidates(rows, GROUP, set(), window_seconds=300)
flagged = sorted((c['season'], c['episode']) for c in candidates)
check('flags exactly S04E05 as the false positive', flagged, [(4, 5)])

only = candidates[0]
check('shifted key reported as S04E04 (the fold-shift signature)', only['shifted_key'], (4, 4))
check('neighbour identified as S04E06', only['neighbour'], (4, 6))
check('not corroborated (no playback log)', only['corroborated_by_playback_log'], False)

# --- same rows, but a playback log now corroborates S04E05 actually played -> still reported
# (report-only tool), but annotated so the printed DELETE is withheld.
corroborated_candidates = audit.find_candidates(rows, GROUP, {(4, 5)}, window_seconds=300)
check('still listed, but annotated corroborated', len(corroborated_candidates), 1)
check('corroborated flag is marked True, not silently dropped',
	corroborated_candidates[0]['corroborated_by_playback_log'], True)

# --- realistic device-log pattern (#79's actual log shape): E03 marked near end of playback,
# E05 falsely marked seconds later during nextep resolution, E06 only marked ~22 min after that
# once it actually finishes playing. E05's literal predecessor (E04) is relocated and never
# watched, so this only flags if the detector scans back past that gap to E03.
realistic_db = make_db([
	(4, 1, '2026-08-30 20:00:00'),
	(4, 2, '2026-08-30 20:25:00'),
	(4, 3, '2026-08-30 21:14:00'),
	(4, 5, '2026-08-30 21:15:00'),  # false positive, 60s after E03 -- not close to E06
	(4, 6, '2026-08-30 21:37:00'),  # real play, ~22 min after the false E05 mark
])
realistic_rows = audit.load_watched_rows(realistic_db, TMDB_ID)
realistic_candidates = audit.find_candidates(realistic_rows, GROUP, set(), window_seconds=300)
check('realistic device-log pattern still flags S04E05 (scans back past the relocated E04 gap to E03)',
	sorted((c['season'], c['episode']) for c in realistic_candidates), [(4, 5)])
check('neighbour identified as the scanned-back E03, not E06',
	realistic_candidates[0]['neighbour'], (4, 3))
os.remove(realistic_db)

# --- a normal watch cadence (no timestamps within window_seconds of a neighbour) is never flagged.
clean_db = make_db([
	(4, 5, '2026-08-30 20:00:00'),
	(4, 6, '2026-08-30 20:25:00'),  # 25 min later -- a real episode's length, not a collision
])
clean_rows = audit.load_watched_rows(clean_db, TMDB_ID)
check('normal watch cadence is not flagged', audit.find_candidates(clean_rows, GROUP, set(), window_seconds=300), [])

os.remove(db_path)
os.remove(clean_db)

print()
if failures:
	print('%d FAILED: %s' % (len(failures), failures))
	sys.exit(1)
print('all checks passed')
