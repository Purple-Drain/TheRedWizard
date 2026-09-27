#!/usr/bin/env bash
# debrid-link-ttl.sh: how long does a resolved RD/TorBox download link keep working? (#1, owner C4)
#
# Red Light keeps a pre-resolved next-episode url for NEXTEP_PRERESOLVE_TTL_SEC (900 s). That number was
# chosen, not measured. Run this on dev-runner (never on a Shield/TCL) with a freshly resolved url, and it
# requests the first byte at 15 min, 1 h, 4 h and 24 h after it starts, logging the HTTP status each time.
#
# Usage: tools/debrid-link-ttl.sh <label> <url>   (runs about 24 h; start it with nohup or in tmux)
# Output: one line per check in ~/debrid-link-ttl/<label>.log. The url itself is never written to the log.
set -euo pipefail
label="${1:?label, e.g. rd or torbox}"; url="${2:?resolved download url}"
log_dir="$HOME/debrid-link-ttl"; mkdir -p "$log_dir"; log="$log_dir/$label.log"
start=$(date +%s)
check() {
	local code
	code=$(curl -s -o /dev/null -w '%{http_code}' -r 0-0 --max-time 30 "$url" || echo 000)
	echo "$(date -Is) label=$label age_s=$(( $(date +%s) - start )) http=$code" | tee -a "$log"
}
check
for offset in 900 3600 14400 86400; do
	sleep $(( start + offset - $(date +%s) ))
	check
done
