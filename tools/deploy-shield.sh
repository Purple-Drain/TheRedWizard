#!/usr/bin/env bash
# Deploy plugin.video.redlight to the Shield test device over adb, safely.
#
# Safety measures (all learned the hard way — see the comments below for why):
#   1. Snapshots small high-value userdata files before touching Kodi, mirroring
#      kodi-strm-pipeline/scripts/shield-deploy.sh's snapshot_userdata() (added
#      after a hard-kill reverted guisettings.xml to stock, losing 43/59
#      customised settings — kodi-shield-config#43).
#   2. Captures whatever's actively playing BEFORE stopping Kodi, and resumes
#      it after Kodi is back up.
#   3. Shuts Kodi down GRACEFULLY (JSON-RPC Application.Quit, which flushes
#      settings and closes the SQLite DBs cleanly) and only falls back to a
#      hard `am force-stop` if the webserver is off/unreachable.
#
# Pulls the addon from this repo's published gh-pages zip (built by CI on
# merge to main), not from the local working tree — so what lands on the
# device is exactly what a real user's Kodi would install, not whatever happens
# to be checked out (sparse checkouts, uncommitted edits, etc. could otherwise
# ship something no one else has).
#
# Usage:
#   ./tools/deploy-shield.sh                 # 10.1.1.30
#   ./tools/deploy-shield.sh 10.1.1.30

set -uo pipefail

SHIELD_IP="${1:-10.1.1.30}"
ADB_TARGET="${SHIELD_IP}:5555"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADDON_ID="plugin.video.redlight"
KODI_ADDON_DIR="/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/addons/${ADDON_ID}"
KODI_USERDATA="/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/userdata"

# Small, high-value userdata files Kodi holds in memory and rewrites on exit —
# exactly the ones a hard kill can truncate or reset. MUST stay outside any git
# tree: guisettings.xml carries the webserver password, passwords.xml the
# WebDAV creds. Deliberately NOT under $REPO_ROOT for that reason.
BACKUP_FILES="guisettings.xml advancedsettings.xml sources.xml favourites.xml passwords.xml profiles.xml"
SHIELD_BACKUP_DIR="${SHIELD_BACKUP_DIR:-$HOME/.shield-deploy-backups}"

command -v adb >/dev/null 2>&1 || { echo "adb not found." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl not found." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found." >&2; exit 1; }

echo "==> Connecting to ${ADB_TARGET}"
adb connect "$ADB_TARGET" >/dev/null 2>&1 || true
if ! adb -s "$ADB_TARGET" shell true >/dev/null 2>&1; then
  echo "  Cannot reach the Shield at ${ADB_TARGET}." >&2
  exit 1
fi

# --- Resolve the version to deploy from this repo's addon.xml, and fetch the
# matching zip CI already published to gh-pages. Requires the PR to have
# merged and the publish workflow to have completed. ---
VERSION="$(python3 - "$REPO_ROOT/$ADDON_ID/addon.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
print(ET.parse(sys.argv[1]).getroot().get('version'))
PY
)"
[ -n "$VERSION" ] || { echo "Could not read version from $ADDON_ID/addon.xml" >&2; exit 1; }
ZIP_NAME="${ADDON_ID}-${VERSION}.zip"
echo "==> Deploying ${ADDON_ID} ${VERSION}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

( cd "$REPO_ROOT" && git fetch origin gh-pages --quiet )
if ! ( cd "$REPO_ROOT" && git show "origin/gh-pages:${ADDON_ID}/${ZIP_NAME}" > "$WORKDIR/$ZIP_NAME" 2>/dev/null ); then
  echo "  ${ZIP_NAME} not found on gh-pages yet." >&2
  echo "  Has the version-bump PR merged, and has 'Publish Kodi repo' finished?" >&2
  echo "  gh run list --workflow=publish-repo.yml --limit 3" >&2
  exit 1
fi
python3 - "$WORKDIR/$ZIP_NAME" "$WORKDIR/extracted" <<'PY'
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])
PY
[ -d "$WORKDIR/extracted/$ADDON_ID" ] || { echo "Zip did not contain $ADDON_ID/" >&2; exit 1; }
echo "  fetched and extracted $ZIP_NAME"

# --- Snapshot userdata before anything touches Kodi. Never fails the deploy —
# a backup that turns a working deploy into a failed one is a worse trade than
# proceeding unprotected with a loud warning. ---
snapshot_userdata() {
  local dest f saved=0
  dest="$SHIELD_BACKUP_DIR/$(date +%Y%m%d-%H%M%S)"
  echo "==> Snapshotting userdata before restart"
  if ! mkdir -p "$dest" 2>/dev/null; then
    echo "  WARNING: cannot create $dest -- continuing WITHOUT a backup" >&2
    return 0
  fi
  for f in $BACKUP_FILES; do
    if adb -s "$ADB_TARGET" pull "$KODI_USERDATA/$f" "$dest/$f" >/dev/null 2>&1; then
      saved=$((saved + 1))
    fi
  done
  if [ "$saved" -eq 0 ]; then
    rmdir "$dest" 2>/dev/null || true
    echo "  WARNING: pulled nothing -- continuing WITHOUT a backup" >&2
  else
    echo "  saved $saved file(s) to $dest"
  fi
  return 0
}
snapshot_userdata

# --- Read webserver creds straight off the device, so nothing secret lives in
# this repo. Used both to check what's playing and for the graceful Quit.
#
# ONE retried read of the whole file, parsed locally -- not five separate adb
# shell round-trips. Each one is independently vulnerable to this Shield's
# adb-over-wifi link dropping mid-command (seen repeatedly this session); five
# independent single-shot reads meant a blip on ANY of them silently left
# WS_ENABLED empty, which downgrades kodi_stop() to a hard force-stop with NO
# warning that it happened -- confirmed happening for real on this script's
# second deploy run. One retried read either gets the real settings or the
# script says so explicitly, out loud, before it matters. ---
GS="$KODI_USERDATA/guisettings.xml"
GS_CONTENT=""
for attempt in 1 2 3; do
  GS_CONTENT=$(adb -s "$ADB_TARGET" shell "cat '$GS' 2>/dev/null")
  [ -n "$GS_CONTENT" ] && break
  adb disconnect "$ADB_TARGET" >/dev/null 2>&1 || true
  sleep 2
  adb connect "$ADB_TARGET" >/dev/null 2>&1 || true
done
gs_field() {  # gs_field <setting-id> -> value, or empty
  printf '%s' "$GS_CONTENT" | grep -oE "<setting id=\"$1\"[^>]*>[^<]*" | sed 's/.*>//' | tr -d '\r'
}
if [ -z "$GS_CONTENT" ]; then
  echo "  WARNING: could not read guisettings.xml after 3 attempts -- treating webserver as unavailable, will hard force-stop" >&2
  WS_ENABLED="false"; WS_PORT="8080"; WS_USER="kodi"; WS_PASS=""; WS_SSL="false"
else
  WS_ENABLED=$(gs_field services.webserver)
  WS_PORT=$(gs_field services.webserverport); WS_PORT="${WS_PORT:-8080}"
  WS_USER=$(gs_field services.webserverusername); WS_USER="${WS_USER:-kodi}"
  WS_PASS=$(gs_field services.webserverpassword)
  WS_SSL=$(gs_field services.webserverssl)
fi
JSONRPC_URL="http://${SHIELD_IP}:${WS_PORT}/jsonrpc"
[ "$WS_SSL" = "true" ] && JSONRPC_URL="https://${SHIELD_IP}:${WS_PORT}/jsonrpc"

jsonrpc() {  # jsonrpc '<json body>' -> prints response body
  curl -sk -m 8 -u "${WS_USER}:${WS_PASS}" -X POST "$JSONRPC_URL" \
    -H 'Content-Type: application/json' -d "$1" 2>/dev/null
}

# --- Capture what's playing, if anything, BEFORE we stop Kodi. Resolves the
# active player's raw stream URL + position, not a Redlight-specific handle:
# after restart we hand that URL straight back to Player.Open with a resume
# option, which works regardless of what resolved it. Caveat: a debrid-signed
# URL can expire before the restart completes; that's a best-effort resume,
# not a guarantee, and we say so if it fails rather than silently doing
# nothing.
#
# Confirmed happening for real: on a run where the webserver was unreachable
# for this ENTIRE check (not just flaky -- genuinely down for the run), the
# single-shot GetActivePlayers call came back empty and the script proceeded
# as if nothing were playing. It force-stopped Kodi mid-episode with nothing
# captured and nothing to resume. The script's own output gave no sign this
# happened differently from "nothing was playing" -- both look identical
# unless this is made loud on purpose. Retry like everything else touching
# this Shield's link, and if it STILL can't tell, say so unmissably and (when
# run at an interactive terminal) require an explicit yes before continuing
# rather than guessing. ---
RESUME_FILE=""
RESUME_POS="0"
RESUME_MODE=""       # 'redlight' (preferred) or 'raw' (fallback); set unconditionally --
RESUME_TMDB=""       # set -u would abort on an unbound-variable read below otherwise,
RESUME_MEDIA_TYPE="" # e.g. when WS_ENABLED is genuinely "false" (webserver disabled in
RESUME_SEASON=""     # settings, not just unreachable) and the whole capture block below
RESUME_EPISODE=""    # never runs at all.
if [ "$WS_ENABLED" = "true" ]; then
  ACTIVE=""
  for attempt in 1 2 3; do
    ACTIVE=$(jsonrpc '{"jsonrpc":"2.0","id":1,"method":"Player.GetActivePlayers"}')
    [ -n "$ACTIVE" ] && break
    adb disconnect "$ADB_TARGET" >/dev/null 2>&1 || true
    sleep 2
    adb connect "$ADB_TARGET" >/dev/null 2>&1 || true
  done
  if [ -z "$ACTIVE" ]; then
    # JSON-RPC is down, but plain adb (what got us this far) doesn't need it --
    # a screenshot is a real, independent signal a human (or whoever's running
    # this) can actually look at before deciding, rather than guessing blind.
    SCREENSHOT_PATH="${TMPDIR:-/tmp}/deploy-shield-playback-check.png"
    if adb -s "$ADB_TARGET" shell screencap -p /sdcard/deploy_check.png >/dev/null 2>&1 \
       && adb -s "$ADB_TARGET" pull /sdcard/deploy_check.png "$SCREENSHOT_PATH" >/dev/null 2>&1; then
      adb -s "$ADB_TARGET" shell rm /sdcard/deploy_check.png >/dev/null 2>&1 || true
      SCREENSHOT_NOTE="  A screenshot was saved to: ${SCREENSHOT_PATH}"
    else
      SCREENSHOT_NOTE="  (screenshot capture also failed -- adb link may be down too)"
    fi
    echo >&2
    echo "  ############################################################" >&2
    echo "  # WARNING: could not reach Kodi's webserver to check for   #" >&2
    echo "  # active playback, after 3 attempts. If something IS       #" >&2
    echo "  # playing right now, restarting Kodi WILL interrupt it and #" >&2
    echo "  # it will NOT be captured or resumed.                      #" >&2
    echo "  ############################################################" >&2
    echo "$SCREENSHOT_NOTE" >&2
    echo >&2
    if [ -t 0 ]; then
      read -r -p "  Continue anyway? [y/N] " CONFIRM
      case "$CONFIRM" in
        y|Y|yes|YES) ;;
        *) echo "  Aborting. Nothing was touched." >&2; exit 1 ;;
      esac
    else
      echo "  Not an interactive terminal -- proceeding, but this was your warning." >&2
    fi
  fi
  PLAYERID=$(printf '%s' "$ACTIVE" | python3 -c "import sys,json
try:
  d=json.load(sys.stdin)['result']
  print(d[0]['playerid'] if d else '')
except Exception: print('')")
  if [ -n "$PLAYERID" ]; then
    # Ask for enough to reconstruct a Redlight plugin:// play call (tmdb id +
    # season/episode), not just the resolved stream URL -- see the comment
    # above. Falls back to the raw file+seek approach only when an item isn't
    # Redlight-tagged (no tmdb uniqueid), e.g. played from somewhere else.
    ITEM=$(jsonrpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Player.GetItem\",\"params\":{\"playerid\":${PLAYERID},\"properties\":[\"file\",\"title\",\"showtitle\",\"season\",\"episode\",\"uniqueid\"]}}")
    PROPS=$(jsonrpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Player.GetProperties\",\"params\":{\"playerid\":${PLAYERID},\"properties\":[\"time\",\"percentage\"]}}")
    read -r RESUME_MODE RESUME_FILE RESUME_TMDB RESUME_MEDIA_TYPE RESUME_SEASON RESUME_EPISODE RESUME_POS RESUME_TITLE <<PYOUT
$(python3 -c "
import json
item = json.loads('''$ITEM''')['result']['item']
props = json.loads('''$PROPS''')['result']
t = props['time']
secs = t['hours']*3600 + t['minutes']*60 + t['seconds']
title = (item.get('showtitle') or item.get('title') or 'playing_item').replace(' ', '_')
tmdb = (item.get('uniqueid') or {}).get('tmdb') or ''
season, episode = item.get('season'), item.get('episode')
fileurl = item.get('file') or '-'
# '-' placeholders for unused fields -- bash 'read' collapses runs of
# whitespace, so a printed EMPTY field (vs. a present-but-blank one) does not
# survive as its own token and silently shifts every field after it.
if tmdb and season not in (None, -1) and episode not in (None, -1):
    print('redlight', fileurl, tmdb, 'episode', season, episode, secs, title)
elif tmdb:
    print('redlight', fileurl, tmdb, 'movie', '-', '-', secs, title)
else:
    print('raw', fileurl, '-', '-', '-', '-', secs, title)
" 2>/dev/null)
PYOUT
    [ "$RESUME_FILE" = "-" ] && RESUME_FILE=""
    [ "$RESUME_TMDB" = "-" ] && RESUME_TMDB=""
    [ "$RESUME_SEASON" = "-" ] && RESUME_SEASON=""
    [ "$RESUME_EPISODE" = "-" ] && RESUME_EPISODE=""
    if [ -n "$RESUME_FILE" ] || [ "$RESUME_MODE" = "redlight" ]; then
      if [ "$RESUME_MODE" = "redlight" ]; then
        echo "==> Currently playing: ${RESUME_TITLE//_/ } (${RESUME_POS}s in, tmdb ${RESUME_TMDB}) -- will resume via Redlight after deploy"
      else
        echo "==> Currently playing: ${RESUME_TITLE//_/ } (${RESUME_POS}s in, not Redlight-tagged) -- will resume via raw URL after deploy"
      fi
    fi
  fi
fi

# --- Push the new addon, clearing __pycache__ first so a stale .pyc never
# shadows a changed .py (a running Kodi process holds the old module in
# memory regardless, hence the restart below).
#
# This adb-over-wifi link has dropped mid-command repeatedly in practice
# ("adb: device offline" / "error: closed") -- retry with a reconnect between
# attempts, and HARD-FAIL the whole deploy if the push never lands. Silently
# continuing to restart Kodi on a failed/partial push means "successfully"
# redeploying the OLD version -- confirmed happening for real: a prior run of
# this script did exactly that (device offline mid-push, script pressed on,
# post-restart version check caught it after the fact). Better to stop here
# than restart Kodi for nothing. ---
echo "==> Pushing ${ADDON_ID} to ${KODI_ADDON_DIR}"
PUSH_OK=0
for attempt in 1 2 3 4 5; do
  if adb -s "$ADB_TARGET" shell "find '$KODI_ADDON_DIR' -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; echo done" >/dev/null 2>&1 \
     && adb -s "$ADB_TARGET" push "$WORKDIR/extracted/$ADDON_ID/." "$KODI_ADDON_DIR" >/dev/null 2>&1; then
    PUSH_OK=1
    break
  fi
  echo "  push attempt ${attempt} failed (adb link drop?) -- reconnecting and retrying" >&2
  adb disconnect "$ADB_TARGET" >/dev/null 2>&1 || true
  sleep 2
  adb connect "$ADB_TARGET" >/dev/null 2>&1 || true
  adb -s "$ADB_TARGET" wait-for-device >/dev/null 2>&1 || true
done
[ "$PUSH_OK" = "1" ] || { echo "  ABORTING: could not push ${ADDON_ID} after 5 attempts. Kodi was NOT touched." >&2; exit 1; }
# Verify the push actually landed the right content before going anywhere near
# a restart -- push can exit 0 having transferred a partial tree if the link
# drops mid-transfer.
PUSHED_VERSION=$(adb -s "$ADB_TARGET" shell "grep -m1 -oE 'version=\"[^\"]*\"' '$KODI_ADDON_DIR/addon.xml'" 2>/dev/null | sed 's/version="//;s/"//' | tr -d '\r')
[ "$PUSHED_VERSION" = "$VERSION" ] || { echo "  ABORTING: pushed but device now reports '${PUSHED_VERSION:-nothing}', expected ${VERSION}. Kodi was NOT restarted." >&2; exit 1; }
echo "  pushed and verified ${VERSION}."

# --- Shut Kodi down gracefully; force-stop only as a last resort. ---
kodi_stop() {
  local code attempt quit_confirmed
  if [ "$WS_ENABLED" != "true" ]; then
    echo "  webserver reports disabled/unreachable -- force-stopping (no graceful Quit possible)." >&2
  else
    # Application.Quit tears down the whole process; it does not guarantee
    # Redlight's own onPlayBackStopped handling (which writes the resume
    # bookmark, in a background thread, possibly a network call to
    # Trakt/Simkl/MDBList/PunchPlay) finishes first. An explicit Player.Stop
    # goes through Kodi's normal stop lifecycle and gives that thread time to
    # actually land the write before anything gets torn down -- without this,
    # "graceful" shutdown can still lose the resume point exactly like a hard
    # force-stop does.
    if [ -n "${PLAYERID:-}" ]; then
      echo "==> Stopping playback first (Player.Stop) so the resume bookmark saves"
      jsonrpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Player.Stop\",\"params\":{\"playerid\":${PLAYERID}}}" >/dev/null
      sleep 3
    fi
    echo "==> Asking Kodi to quit gracefully (JSON-RPC Application.Quit)"
    # The Quit POST itself has hit transient HTTP 000 on this Shield's flaky
    # link (not just a slow response) -- retry the POST a few times before
    # giving up on graceful shutdown, not just once with a short timeout.
    quit_confirmed=0
    for attempt in 1 2 3; do
      code=$(curl -sk -o /dev/null -w '%{http_code}' -m 10 -u "${WS_USER}:${WS_PASS}" \
        -X POST "$JSONRPC_URL" -H 'Content-Type: application/json' \
        -d '{"jsonrpc":"2.0","id":1,"method":"Application.Quit"}' 2>/dev/null || true)
      [ "$code" = "200" ] && { quit_confirmed=1; break; }
      echo "  Quit POST attempt ${attempt} got HTTP ${code:-none}; retrying" >&2
      sleep 2
    done
    if [ "$quit_confirmed" = "1" ]; then
      for _ in $(seq 1 30); do
        [ -z "$(adb -s "$ADB_TARGET" shell pidof org.xbmc.kodi 2>/dev/null | tr -d '\r')" ] && { echo "  Kodi exited cleanly."; return 0; }
        sleep 1
      done
      echo "  Quit didn't complete in 30s; falling back to force-stop." >&2
    else
      echo "  JSON-RPC unavailable after 3 attempts; falling back to force-stop." >&2
    fi
  fi
  adb -s "$ADB_TARGET" shell am force-stop org.xbmc.kodi
  sleep 2
}
echo "==> Restarting Kodi"
kodi_stop

# A graceful Quit can drop the adb-over-wifi session along with Kodi; reconnect
# and wait for the device before relaunching, retrying so the shutdown method
# never costs us the restart.
for _ in 1 2 3 4 5; do
  adb connect "$ADB_TARGET" >/dev/null 2>&1 || true
  adb -s "$ADB_TARGET" wait-for-device >/dev/null 2>&1 || true
  if adb -s "$ADB_TARGET" shell monkey -p org.xbmc.kodi -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1; then
    break
  fi
  adb disconnect "$ADB_TARGET" >/dev/null 2>&1 || true
  sleep 2
done

echo "==> Waiting for Kodi to come back up"
for _ in $(seq 1 30); do
  PONG=$(jsonrpc '{"jsonrpc":"2.0","id":1,"method":"JSONRPC.Ping"}' 2>/dev/null)
  case "$PONG" in *pong*) break ;; esac
  sleep 2
done

INSTALLED_VERSION=$(adb -s "$ADB_TARGET" shell "grep -m1 -oE 'version=\"[^\"]*\"' '$KODI_ADDON_DIR/addon.xml'" | sed 's/version="//;s/"//' | tr -d '\r')
echo "==> Deployed. Device now reports: ${INSTALLED_VERSION:-unknown}"
if [ "$INSTALLED_VERSION" != "$VERSION" ]; then
  echo "  WARNING: expected ${VERSION}, device reports ${INSTALLED_VERSION:-nothing}" >&2
fi

# --- Nothing was captured live before the restart (webserver was down at
# that point, or genuinely nothing was playing) -- fall back to Redlight's
# OWN persisted lists instead of giving up. These are independent of
# anything captured pre-restart: "In Progress" refreshes from whatever
# watched-status provider is active (confirmed: this device's is MDBList,
# and get_in_progress_episodes() pulls fresh from its API when active), so
# as long as Player.Stop above actually landed the bookmark, this finds it
# here even though the live capture never ran. Falls further to "what's
# next" if nothing is even in progress. Bounded timeout each -- a plugin
# directory listing does real work (TMDb fetches etc.), seen taking a while
# elsewhere this session; this must not hang the whole deploy over a nice-to-have. ---
fallback_resume_lookup() {  # fallback_resume_lookup <plugin-directory> -> "tmdb season episode resume_secs title", or nothing
  local resp
  resp=$(curl -sk -m 25 -u "${WS_USER}:${WS_PASS}" -X POST "$JSONRPC_URL" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Files.GetDirectory\",\"params\":{\"directory\":\"$1\",\"media\":\"video\",\"properties\":[\"title\",\"showtitle\",\"season\",\"episode\",\"uniqueid\",\"resume\"]}}" 2>/dev/null)
  [ -z "$resp" ] && return 1
  python3 -c "
import json
try:
    files = json.loads('''$resp''')['result']['files']
    if not files: raise SystemExit(1)
    f = files[0]
    tmdb = (f.get('uniqueid') or {}).get('tmdb') or ''
    season, episode = f.get('season'), f.get('episode')
    resume = int((f.get('resume') or {}).get('position') or 0)
    title = (f.get('showtitle') or f.get('title') or 'item').replace(' ', '_')
    if not (tmdb and season not in (None, -1) and episode not in (None, -1)): raise SystemExit(1)
    print(tmdb, season, episode, resume, title)
except Exception:
    raise SystemExit(1)
" 2>/dev/null
}
if [ -z "$RESUME_MODE" ] && [ "$WS_ENABLED" = "true" ]; then
  echo "==> Nothing captured live before the restart -- checking Redlight's own lists"
  FALLBACK=$(fallback_resume_lookup "plugin://plugin.video.redlight/?mode=build_in_progress_episode")
  FALLBACK_SOURCE="the most recent in-progress episode"
  if [ -z "$FALLBACK" ]; then
    FALLBACK=$(fallback_resume_lookup "plugin://plugin.video.redlight/?mode=build_next_episode")
    FALLBACK_SOURCE="the next episode up (nothing was in progress)"
  fi
  if [ -n "$FALLBACK" ]; then
    read -r RESUME_TMDB RESUME_SEASON RESUME_EPISODE RESUME_POS RESUME_TITLE <<< "$FALLBACK"
    RESUME_MODE="redlight"; RESUME_MEDIA_TYPE="episode"
    echo "  found via Redlight: ${RESUME_TITLE//_/ } -- ${FALLBACK_SOURCE}"
  else
    echo "  nothing found in Redlight's in-progress or next-episode lists either -- nothing to resume."
  fi
fi

# --- Resume whatever was playing before the restart, OR found via the
# fallback lookup above. Best-effort either way.
#
# Preferred path ('redlight' mode): re-open through Redlight's own plugin://
# playback route (mode=playback.media, playback_key() is hardcoded to 'media'
# in settings.py -- not user-configurable, so this isn't a guess) instead of
# replaying the raw resolved stream URL. Redlight re-resolves the source
# fresh, so a since-expired debrid-signed URL is a non-issue, and it goes
# through the addon's own scrape/episode-group/watched-status machinery
# rather than bypassing it. Falls back to the raw file only for a
# non-Redlight-tagged item (no tmdb uniqueid on it).
#
# Either way, Player.Open's "resume" option only resumes a stored LIBRARY
# bookmark -- it does nothing for an ad-hoc/plugin item (confirmed for real: a
# prior run "resumed" but started from 0:00, silently ignoring the position
# passed there). So both paths still finish with an explicit Player.Seek to
# an absolute Global.Time once a player exists, as a safety net regardless of
# whether Redlight's own resume prompt kicks in first. ---
if [ "$RESUME_MODE" = "redlight" ] || [ "$RESUME_MODE" = "raw" ]; then
  if [ "$RESUME_MODE" = "redlight" ]; then
    if [ "$RESUME_MEDIA_TYPE" = "episode" ]; then
      OPEN_ITEM="{\"file\":\"plugin://plugin.video.redlight/?mode=playback.media&media_type=episode&tmdb_id=${RESUME_TMDB}&season=${RESUME_SEASON}&episode=${RESUME_EPISODE}\"}"
    else
      OPEN_ITEM="{\"file\":\"plugin://plugin.video.redlight/?mode=playback.media&media_type=movie&tmdb_id=${RESUME_TMDB}\"}"
    fi
    echo "==> Resuming via Redlight: ${RESUME_TITLE//_/ } at ${RESUME_POS}s"
  else
    OPEN_ITEM="{\"file\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$RESUME_FILE")}"
    echo "==> Resuming raw URL: ${RESUME_TITLE//_/ } at ${RESUME_POS}s"
  fi
  RESULT=$(jsonrpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Player.Open\",\"params\":{\"item\":${OPEN_ITEM}}}")
  case "$RESULT" in
    *'"result":"OK"'*)
      RESUME_PLAYERID=""
      for _ in $(seq 1 20); do
        ACTIVE2=$(jsonrpc '{"jsonrpc":"2.0","id":1,"method":"Player.GetActivePlayers"}')
        RESUME_PLAYERID=$(printf '%s' "$ACTIVE2" | python3 -c "import sys,json
try:
  d=json.load(sys.stdin)['result']
  print(d[0]['playerid'] if d else '')
except Exception: print('')")
        [ -n "$RESUME_PLAYERID" ] && break
        sleep 1
      done
      if [ -n "$RESUME_PLAYERID" ] && [ "$RESUME_POS" -gt 5 ] 2>/dev/null; then
        H=$((RESUME_POS / 3600)); M=$(((RESUME_POS % 3600) / 60)); S=$((RESUME_POS % 60))
        SEEK=$(jsonrpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Player.Seek\",\"params\":{\"playerid\":${RESUME_PLAYERID},\"value\":{\"hours\":${H},\"minutes\":${M},\"seconds\":${S},\"milliseconds\":0}}}")
        case "$SEEK" in
          *'"error"'*) echo "  WARNING: playing, but seek to ${RESUME_POS}s failed -- it's at 0:00, seek manually" >&2 ;;
          *) echo "  resumed at ${RESUME_POS}s." ;;
        esac
      elif [ -n "$RESUME_PLAYERID" ]; then
        echo "  resumed (near the start, no seek needed)."
      else
        echo "  WARNING: opened but no active player found to seek within 20s -- check it manually" >&2
      fi
      ;;
    *)
      if [ "$RESUME_MODE" = "redlight" ]; then
        echo "  WARNING: Redlight resume call did not confirm OK -- resume manually if needed" >&2
      else
        echo "  WARNING: resume call did not confirm OK (link may have expired) -- resume manually if needed" >&2
      fi
      ;;
  esac
fi

echo
echo "Done."
