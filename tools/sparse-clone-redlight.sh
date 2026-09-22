#!/usr/bin/env bash
# Set up a git sparse checkout scoped to plugin.video.redlight (+ tools, so
# build-repo.sh still works) instead of pulling every add-on in this repo.
#
# Two ways to use it:
#
#   1. Fresh clone -- pass the repo URL, get a new sparse checkout:
#        ./sparse-clone-redlight.sh https://github.com/Purple-Drain/TheRedWizard.git [dir]
#
#   2. Existing checkout -- run with no args from inside it to narrow the
#      working tree in place (history/objects are untouched, this only
#      changes which paths are checked out):
#        cd TheRedWizard && ./tools/sparse-clone-redlight.sh
#
# Either way, `git sparse-checkout list` afterwards shows the active paths.
set -euo pipefail

PATHS=(plugin.video.redlight tools)

if [ $# -ge 1 ]; then
	url="$1"
	dir="${2:-$(basename "$url" .git)}"
	git clone --filter=blob:none --sparse "$url" "$dir"
	cd "$dir"
else
	cd "$(dirname "$0")/.."
	git sparse-checkout init --cone
fi

git sparse-checkout set "${PATHS[@]}"
git sparse-checkout list
