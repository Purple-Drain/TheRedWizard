# Hosted Kodi repo for Redlight — status

Goal: serve `plugin.video.redlight` from a fork-owned Kodi repo (working name
`repository.purpledrain`) so the Shield updates from the Purple-Drain fork
instead of upstream's `repository.redwizard`, closing the version-collision
gap. Background: see the `shield-redlight-deploy-constraints` and
`hosted-repo-handoff` memory entries from 2026-07-25.

Original 4-step plan (from the other session that owned this work):
1. Version tie-break test
2. Scaffold `repository.purpledrain`
3. Point the Shield at it
4. Retire `service.redlightpatch`

## Done

- **Step 1 — version tie-break applied.** `plugin.video.redlight/addon.xml` is
  `2.0.3+pd.1` (filename-safe `+pd.N` suffix, not an epoch — colons aren't
  legal in Windows/Android filenames). Confirmed live on the Shield via
  JSON-RPC (`Addons.GetAddonDetails` → `version: 2.0.3+pd.1`, `enabled: true`,
  `broken: false`) as of 2026-07-26.
- **Step 2 (partial) — build tooling merged.** `tools/build-repo.sh` (merged
  to `main` 2026-07-23) builds a Kodi-repo-layout `dist/` (gitignored):
  `dist/addons.xml`, `dist/addons.xml.md5`, per-addon zip + icon/fanart/changelog.
  Regenerated 2026-07-26 for the current `2.0.3+pd.1` build
  (md5 `a26fb2b8882545d4fb115e25e37deec6`).

## Done (2026-07-26, worktree `hosted-repo-pages`)

- **Step 2 — hosting workflow added.** `.github/workflows/publish-repo.yml`
  runs `tools/build-repo.sh` on every push to `main` that touches the addon
  or the build script (plus manual `workflow_dispatch`), and publishes
  `dist/` to an orphan `gh-pages` branch via `peaceiris/actions-gh-pages`.
  **Not yet pushed to `origin`, and GitHub Pages is not yet enabled in repo
  settings** — both are visible/external actions that need an explicit go
  before this becomes a live public URL.

## Not done

- **Hosting not live.** The workflow exists but hasn't run yet — no
  `gh-pages` branch on the remote, GitHub Pages isn't turned on in repo
  settings, so there's no public URL serving `dist/`.
  `repository.purpledrain` as an installable addon doesn't exist yet.
- **Step 3 — Shield not pointed at anything hosted.** It still gets Redlight
  via `adb push` with `general.addonupdates = 2` (Never), confirmed live
  2026-07-26. This is intentional/safe for now — nothing to point it at.
- **Step 4 — `service.redlightpatch` still deployed and doing two jobs**:
  (a) re-healing `folders.py`/`sources.py` .strm patches if Kodi ever
  auto-updates from upstream, and (b) repairing `settings.xml` DebridLibrary
  paths on boot. Job (b) is unrelated to which repo wins and needs a new home
  before (a) can be retired.

## Next steps (tracked as tasks)

1. Decide hosting mechanism (`gh-pages` vs. other) and wire up serving
   `dist/` at a stable URL.
2. Install `repository.purpledrain` on the Shield, verify Kodi resolves
   `plugin.video.redlight` from it (not `repository.redwizard`) at the
   version tie-break, then switch the update source.
3. Relocate the `settings.xml` DebridLibrary-path repair out of
   `service.redlightpatch`, then retire the service.
