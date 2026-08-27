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
# nothing. ---
RESUME_FILE=""
RESUME_POS="0"
if [ "$WS_ENABLED" = "true" ]; then
  ACTIVE=$(jsonrpc '{"jsonrpc":"2.0","id":1,"method":"Player.GetActivePlayers"}')
  PLAYERID=$(printf '%s' "$ACTIVE" | python3 -c "import sys,json
try:
  d=json.load(sys.stdin)['result']
  print(d[0]['playerid'] if d else '')
except Exception: print('')")
  if [ -n "$PLAYERID" ]; then
    ITEM=$(jsonrpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Player.GetItem\",\"params\":{\"playerid\":${PLAYERID},\"properties\":[\"file\",\"title\"]}}")
    PROPS=$(jsonrpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Player.GetProperties\",\"params\":{\"playerid\":${PLAYERID},\"properties\":[\"time\",\"percentage\"]}}")
    read -r RESUME_FILE RESUME_POS RESUME_TITLE <<PYOUT
$(python3 -c "
import json
item = json.loads('''$ITEM''')['result']['item']
props = json.loads('''$PROPS''')['result']
t = props['time']
secs = t['hours']*3600 + t['minutes']*60 + t['seconds']
print(item.get('file',''), secs, item.get('title','').replace(' ','_') or 'playing_item')
" 2>/dev/null)
PYOUT
    if [ -n "$RESUME_FILE" ]; then
      echo "==> Currently playing: ${RESUME_TITLE//_/ } (${RESUME_POS}s in) -- will resume after deploy"
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
  local code
  if [ "$WS_ENABLED" != "true" ]; then
    echo "  webserver reports disabled/unreachable -- force-stopping (no graceful Quit possible)." >&2
  else
    echo "==> Asking Kodi to quit gracefully (JSON-RPC Application.Quit)"
    code=$(curl -sk -o /dev/null -w '%{http_code}' -m 6 -u "${WS_USER}:${WS_PASS}" \
      -X POST "$JSONRPC_URL" -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"Application.Quit"}' 2>/dev/null || true)
    if [ "$code" = "200" ]; then
      for _ in $(seq 1 15); do
        [ -z "$(adb -s "$ADB_TARGET" shell pidof org.xbmc.kodi 2>/dev/null | tr -d '\r')" ] && { echo "  Kodi exited cleanly."; return 0; }
        sleep 1
      done
      echo "  Quit didn't complete in time; falling back to force-stop." >&2
    else
      echo "  JSON-RPC unavailable (HTTP ${code:-none}); falling back to force-stop." >&2
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

# --- Resume whatever was playing before the restart. Best-effort: a
# debrid-signed URL can have expired in the meantime, in which case Kodi just
# won't play it and we say so rather than pretending this always works. ---
if [ -n "$RESUME_FILE" ]; then
  echo "==> Resuming: ${RESUME_TITLE//_/ } at ${RESUME_POS}s"
  RESULT=$(jsonrpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Player.Open\",\"params\":{\"item\":{\"file\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$RESUME_FILE")},\"options\":{\"resume\":${RESUME_POS}}}}")
  case "$RESULT" in
    *'"result":"OK"'*) echo "  resumed." ;;
    *) echo "  WARNING: resume call did not confirm OK (link may have expired) -- resume manually if needed" >&2 ;;
  esac
fi

echo
echo "Done."
