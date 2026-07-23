#!/usr/bin/env bash
# Build Kodi-installable release zips for every add-on in this repo, plus the
# addons.xml / addons.xml.md5 index that a Kodi repository serves.
#
# Output (gitignored) lands in dist/, in the standard Kodi repo layout:
#
#   dist/addons.xml
#   dist/addons.xml.md5
#   dist/<addon.id>/<addon.id>-<version>.zip     <- zip contains a top-level <addon.id>/ dir
#   dist/<addon.id>/icon.png, fanart.jpg, changelog.txt   (when the add-on has them)
#
# The zips are directly installable via Kodi's "Install from zip file", and the
# dist/ tree is drop-in servable as a Kodi repository if it's ever hosted.
#
# NOTE: this machine's checkout may be sparse (git sparse-checkout list) -- only
# add-on directories actually present on disk get built. That's intentional, not
# a bug: pass explicit ids to build a subset, or widen the sparse-checkout for all.
#
# Usage:
#   ./tools/build-repo.sh                          # every add-on dir present
#   ./tools/build-repo.sh plugin.video.redlight    # just these
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
OUT="$ROOT/dist"

# `zip` isn't on a stock Git Bash; fall back to python's zipfile, which is always
# there (we already depend on python for the .strm patch tooling).
zip_dir() {  # zip_dir <src_dir> <dest_zip> ; stores paths as <basename>/...
	python - "$1" "$2" <<'PY'
import os, sys, zipfile
src, dest = sys.argv[1], sys.argv[2]
base = os.path.basename(src.rstrip('/\\'))
skip_dirs = {'__pycache__', '.git'}
with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
	for root, dirs, files in os.walk(src):
		dirs[:] = sorted(d for d in dirs if d not in skip_dirs)
		for f in sorted(files):
			if f.endswith(('.pyc', '.pyo')) or f == '.DS_Store':
				continue
			full = os.path.join(root, f)
			z.write(full, os.path.join(base, os.path.relpath(full, src)).replace('\\', '/'))
PY
}

addon_version() {  # read version off the <addon id=...> element, NOT the xml decl
	python - "$1" <<'PY'
import sys, xml.etree.ElementTree as ET
print(ET.parse(sys.argv[1]).getroot().get('version'))
PY
}

if [ "$#" -gt 0 ]; then
	ADDONS=("$@")
else
	ADDONS=()
	for d in "$ROOT"/*/; do
		[ -f "${d}addon.xml" ] && ADDONS+=("$(basename "$d")")
	done
fi

[ "${#ADDONS[@]}" -eq 0 ] && { echo "no add-on directories found (sparse checkout?)" >&2; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"
printf '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' > "$OUT/addons.xml"

for id in "${ADDONS[@]}"; do
	src="$ROOT/$id"
	[ -f "$src/addon.xml" ] || { echo "skip $id (no addon.xml)" >&2; continue; }
	ver="$(addon_version "$src/addon.xml")"
	mkdir -p "$OUT/$id"
	zip_dir "$src" "$OUT/$id/$id-$ver.zip"

	# Kodi reads these from the repo dir, not the zip, when rendering the addon
	# browser. Art can sit at the root or behind <assets>/<icon>/<fanart> (which
	# is where anything modern puts it), so check both.
	for extra in icon.png fanart.jpg changelog.txt; do
		[ -f "$src/$extra" ] && cp "$src/$extra" "$OUT/$id/$extra"
	done
	python - "$src" "$OUT/$id" <<'PY'
import os, shutil, sys, xml.etree.ElementTree as ET
src, out = sys.argv[1:3]
root = ET.parse(os.path.join(src, 'addon.xml')).getroot()   # trusted local input
for meta in root.findall("./extension[@point='xbmc.addon.metadata']/assets/*"):
	if meta.tag not in ('icon', 'fanart') or not (meta.text or '').strip():
		continue
	rel = meta.text.strip()
	full = os.path.join(src, rel.replace('/', os.sep))
	if os.path.isfile(full):
		shutil.copy(full, os.path.join(out, meta.tag + os.path.splitext(rel)[1]))
PY
	[ -f "$src/resources/text/changelog.txt" ] && cp "$src/resources/text/changelog.txt" "$OUT/$id/changelog.txt"
	[ -f "$src/resources/changelog.txt" ] && cp "$src/resources/changelog.txt" "$OUT/$id/changelog.txt"

	# append this add-on's manifest, minus its own <?xml ...?> declaration
	python - "$src/addon.xml" >> "$OUT/addons.xml" <<'PY'
import re, sys
t = open(sys.argv[1], encoding='utf-8').read()
t = re.sub(r'^\s*<\?xml[^>]*\?>\s*', '', t).rstrip()
print('\n'.join('\t' + l if l.strip() else l for l in t.splitlines()))
PY
	echo "  built $id-$ver.zip"
done

printf '</addons>\n' >> "$OUT/addons.xml"

# Kodi wants the bare md5 hex, no filename column.
python - "$OUT/addons.xml" "$OUT/addons.xml.md5" <<'PY'
import hashlib, sys
open(sys.argv[2], 'w').write(hashlib.md5(open(sys.argv[1], 'rb').read()).hexdigest())
PY

# Fail loudly rather than shipping an index Kodi will reject at parse time. The
# path goes through argv, not -c: MSYS rewrites path-shaped *arguments* into
# Windows form for a native python.exe, but leaves a -c script body alone, so an
# inlined /c/... path arrives unconverted and fails to open.
python - "$OUT/addons.xml" <<'PY' || { echo "generated addons.xml is not well-formed" >&2; exit 1; }
import sys, xml.etree.ElementTree as ET
ET.parse(sys.argv[1])   # trusted local input: our own add-on manifests
PY

echo "==> dist/ ready ($(ls "$OUT" | wc -l) entries, md5 $(cat "$OUT/addons.xml.md5"))"
