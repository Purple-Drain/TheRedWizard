# -*- coding: utf-8 -*-
"""Audit tool for #80: find watched rows that #79's shifted scrape key plausibly wrote without
the episode ever playing.

Root cause (see #79 and its follow-up comment): an assigned TMDb episode group that relocates a
raw episode into Specials shifts every later same-season episode's group numbering down by one.
When #54's advance-past-duplicate path (modules/sources.py:_advance_past_duplicate_nextep) saw
that shifted key resolve to the file already playing, it marked the *raw* "next" episode watched
without it ever playing -- e.g. Seinfeld raw S04E05 "The Wallet" got marked watched while raw
S04E03/E04's combined file was actually on screen.

This script does NOT touch the database. It reports candidates and prints the exact SQL to
unmark a confirmed false positive -- a human decides, since this runs against a user's real
watched history.

Detection: a watched row (season S, episode E) is a candidate when
  1. raw (S, E-1) is an episode the assigned group relocates into Specials -- i.e. E is the first
     episode right after a fold point, the specific shape where E's shifted scrape key collides
     with the combined file's own raw number and #54's advance-past-duplicate path fires (#79's
     verified mechanism). Later post-fold episodes (E+1, E+2, ...) also carry a shifted key but
     don't collide with anything already playing, so they are not flagged by this alone.
  2. the nearest OTHER watched episode in the same season -- the closest earlier one (E's actual
     predecessor may itself be relocated and so never watched, e.g. Seinfeld's S04E04, so this
     scans back past gaps) and E+1 -- lands within --window-seconds. A real episode runs long
     enough that two genuine plays don't land that close together. Matches the #79 log shape: the
     false mark fires during next-episode resolution moments after the *predecessor* episode's own
     mark (E03 marked near end of playback, E05 falsely marked seconds later), not necessarily
     close to the episode that eventually plays next (E06, which can be marked much later). Note a
     batch "mark season watched" writes one shared last_played across many episodes and will also
     surface here -- this tool is report-only, so that's an acceptable false-flag risk to review by
     hand rather than a reason to suppress it, and
  3. (if a playback log is available) there is no playback_log row for (S, E) -- i.e. nothing
     corroborates the episode actually played.

Usage:
  python3 tools/audit_false_watched_marks.py --db /path/to/watched.db --tmdb-id 1400 \
      --group-fixture tests/fixtures/seinfeld_dvd_group.json [--season 4] \
      [--playback-log /path/to/playback_log.tsv] [--window-seconds 300]

--db points at whichever backing store is active (get_database() in modules/watched_status.py
dispatches watched_db/trakt_db/simkl_db/mdblist_db/punchplay_db by settings.watched_indicators());
all of them share the same 'watched' table shape (db_type, media_id, season, episode, last_played,
title), so any of the on-device sqlite files under addon_data/plugin.video.redlight/ works here.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime

TIMESTAMP_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.000Z', '%Y-%m-%dT%H:%M:%S')

def parse_timestamp(value):
	for fmt in TIMESTAMP_FORMATS:
		try: return datetime.strptime(value, fmt)
		except (ValueError, TypeError): continue
	return None

def group_relocated_episodes(details):
	"""Raw (season, episode) pairs the group relocates OUT of a regular season INTO Specials
	(group order 0) -- same definition as modules/watched_status.py:group_relocated_episodes(),
	reimplemented standalone. Raw Specials entries are excluded: nothing is being relocated out
	of a season that was already Specials."""
	relocated = set()
	for group in details.get('groups', []):
		if group.get('order') != 0: continue
		for episode in group.get('episodes', []):
			season_number, episode_number = episode.get('season_number'), episode.get('episode_number')
			if season_number in (None, 0) or episode_number is None: continue
			relocated.add((int(season_number), int(episode_number)))
	return relocated

def group_episode_data(details, season_number, episode_number):
	"""Same mapping as modules/metadata.py:group_episode_data(), reimplemented standalone so this
	tool runs without the addon's Kodi-dependent import chain. group order is the 1-indexed
	within-order position, matching TMDb's own episode_group 'order' field."""
	for group in details.get('groups', []):
		for episode in group.get('episodes', []):
			if episode.get('season_number') == season_number and episode.get('episode_number') == episode_number:
				return {'season': group.get('order'), 'episode': episode.get('order', 0) + 1}
	return None

def load_watched_rows(db_path, tmdb_id):
	"""Raw (season, episode, last_played) rows for one show, tried as both str and int media_id
	since mark_episode()/watched_status_mark() write whatever type params.get('tmdb_id') was."""
	conn = sqlite3.connect(db_path)
	try:
		rows = conn.execute(
			"SELECT season, episode, last_played FROM watched WHERE db_type = 'episode' AND media_id IN (?, ?)",
			(str(tmdb_id), int(tmdb_id))).fetchall()
	finally:
		conn.close()
	out = []
	for season, episode, last_played in rows:
		try: season, episode = int(season), int(episode)
		except (TypeError, ValueError): continue
		when = parse_timestamp(last_played)
		if when is None: continue
		out.append({'season': season, 'episode': episode, 'when': when, 'raw_last_played': last_played})
	return out

def load_playback_log(path, tmdb_id):
	"""(season, episode) pairs the playback log corroborates for this show. Best-effort: the log
	is opt-in (redlight.playback_log / settings.playback_log_enabled()) and may not exist."""
	corroborated = set()
	if not path: return corroborated
	try:
		with open(path, encoding='utf-8') as handle:
			header = handle.readline().rstrip('\n').split('\t')
			try:
				season_i, episode_i, tmdb_i = header.index('season'), header.index('episode'), header.index('tmdb_id')
			except ValueError:
				return corroborated
			for line in handle:
				fields = line.rstrip('\n').split('\t')
				if len(fields) <= max(season_i, episode_i, tmdb_i): continue
				if fields[tmdb_i] != str(tmdb_id): continue
				try: corroborated.add((int(fields[season_i]), int(fields[episode_i])))
				except (ValueError, IndexError): continue
	except OSError:
		pass
	return corroborated

def find_candidates(rows, details, playback_corroborated, window_seconds, only_season=None):
	by_season_episode = {(r['season'], r['episode']): r for r in rows}
	relocated = group_relocated_episodes(details)
	candidates = []
	for row in rows:
		season, episode = row['season'], row['episode']
		if only_season is not None and season != only_season: continue
		if (season, episode - 1) not in relocated:
			continue  # not the episode immediately after a fold point -- no collision opportunity
		shifted = group_episode_data(details, season, episode)
		if not shifted or shifted.get('season') != season or shifted.get('episode') != episode - 1:
			continue  # not the "-1 after a fold" signature -- no shift, or a different show shape
		# Nearest earlier watched episode in this season, scanning back past any relocated/unwatched
		# gap (E's literal predecessor is usually relocated and so never has its own row), plus the
		# literal next one forward.
		earlier_key = None
		for candidate_episode in range(episode - 1, 0, -1):
			if (season, candidate_episode) in by_season_episode:
				earlier_key = (season, candidate_episode)
				break
		neighbours = [k for k in (earlier_key, (season, episode + 1)) if k is not None]
		close_neighbour = None
		for key in neighbours:
			neighbour = by_season_episode.get(key)
			if neighbour is None: continue
			delta = abs((row['when'] - neighbour['when']).total_seconds())
			if delta <= window_seconds:
				close_neighbour = (key, delta)
				break
		if close_neighbour is None: continue
		corroborated = (season, episode) in playback_corroborated
		candidates.append({
			'season': season, 'episode': episode, 'last_played': row['raw_last_played'],
			'shifted_key': (shifted['season'], shifted['episode']),
			'neighbour': close_neighbour[0], 'neighbour_delta_seconds': close_neighbour[1],
			'corroborated_by_playback_log': corroborated,
		})
	return candidates

def main():
	parser = argparse.ArgumentParser(description="Audit watched rows for #79's shifted-scrape-key false positives (#80). Report only, never modifies the database.")
	parser.add_argument('--db', required=True, help='sqlite watched-status DB (whichever backing store is active)')
	parser.add_argument('--tmdb-id', required=True, type=int)
	parser.add_argument('--group-fixture', required=True, help='TMDb episode-group details JSON (same shape as resolve_assigned_episode_group()/group_details())')
	parser.add_argument('--season', type=int, default=None, help='limit to one season')
	parser.add_argument('--playback-log', default=None, help='playback_log.tsv path, if redlight.playback_log was enabled')
	parser.add_argument('--window-seconds', type=int, default=300, help='max gap between neighbouring watched timestamps to treat as suspicious (default 300)')
	args = parser.parse_args()

	with open(args.group_fixture, encoding='utf-8') as handle:
		details = json.load(handle)

	rows = load_watched_rows(args.db, args.tmdb_id)
	if not rows:
		print('No episode watched rows found for tmdb_id %s in %s.' % (args.tmdb_id, args.db))
		return 0

	corroborated = load_playback_log(args.playback_log, args.tmdb_id)
	candidates = find_candidates(rows, details, corroborated, args.window_seconds, args.season)

	if not candidates:
		print('No candidate false-positive watched marks found (%d watched row(s) checked).' % len(rows))
		return 0

	print('%d candidate false-positive watched mark(s):\n' % len(candidates))
	for c in candidates:
		flag = 'CORROBORATED BY PLAYBACK LOG -- likely a real play, not a false positive' if c['corroborated_by_playback_log'] else 'no playback log corroboration'
		print('Show tmdb_id=%s S%02dE%02d  watched at %s' % (args.tmdb_id, c['season'], c['episode'], c['last_played']))
		print('  reason: shifted scrape key -> S%02dE%02d (this group\'s "-1 after a fold" shape),'
			' watched only %.0fs from neighbour S%02dE%02d' % (
				c['shifted_key'][0], c['shifted_key'][1], c['neighbour_delta_seconds'], c['neighbour'][0], c['neighbour'][1]))
		print('  %s' % flag)
		if not c['corroborated_by_playback_log']:
			print('  to unmark, after confirming by hand:')
			print("    DELETE FROM watched WHERE db_type = 'episode' AND media_id = '%s' AND season = %d AND episode = %d;" % (
				args.tmdb_id, c['season'], c['episode']))
			print('  if this DB is trakt_db/simkl_db/mdblist_db/punchplay_db (an external watched'
				' indicator), that DELETE is local cache only -- the service resync'
				' (trakt_indicators_tv() etc.) will silently rewrite the row back. Unmark it'
				' through the addon\'s "Mark as Unwatched" (which also calls the service) or'
				' directly in the external service instead.')
		print()
	return 0

if __name__ == '__main__':
	sys.exit(main())
