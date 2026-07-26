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

- **Step 2 — hosting is LIVE.** `.github/workflows/publish-repo.yml` builds
  `dist/` via `tools/build-repo.sh` (invoked with `bash` — the script isn't
  marked executable in this Windows-originated checkout) and publishes it to
  an orphan `gh-pages` branch via `peaceiris/actions-gh-pages`, pinned to
  commit SHAs (not mutable tags — closes a `ci-cd-supply-chain` finding from
  automated review). GitHub Pages enabled via `gh api` sourced from
  `gh-pages` root. Confirmed serving `2.0.3+pd.1`'s `addons.xml` at
  **https://purple-drain.github.io/TheRedWizard/** as of 2026-07-26.
  Reviewed for leaks before enabling: repo was already public, built zip and
  addon source contain no credentials (debrid tokens/WebDAV creds live only
  in `kodi-shield-config`'s gitignored `secrets.env` / on-device
  `settings.xml`, untouched by this workflow).

## Not done

## Done (2026-07-26, `repository.purpledrain` test)

- **`repository.purpledrain` addon created** (`repository.purpledrain/addon.xml`,
  merged to `main`) — points Kodi at the gh-pages `addons.xml`/zips above.
- **Version tie-break empirically confirmed** — installed on the **kodi22 test
  instance only** (`net.kodinerds.maven.kodi22`, a clone of production on the
  same Shield; production `org.xbmc.kodi` was never touched and wasn't even
  running at the time, so nothing was interrupted). After Kodi booted and
  refreshed repos, `Addons33.db` shows `plugin.video.redlight`'s `origin`
  auto-flipped from unset to `repository.purpledrain`, over `repository.redwizard`
  (both present). Confirms `2.0.3+pd.1` sorts above upstream's `2.0.3` in
  Kodi's version comparison — the exact thing the deploy-constraints memory
  flagged as untested.
- **Known minor issue**: kodi22's log shows a 404 fetching
  `.../plugin.video.redlight/resources/media/addon_icons/icon.png` — a path
  convention mismatch (our `dist/` puts the icon at
  `plugin.video.redlight/icon.png`, not nested under `resources/media/addon_icons/`).
  Cosmetic only (doesn't block the repo resolving or the addon installing);
  not yet fixed.

## Not done

- **Production Shield (`org.xbmc.kodi`) untouched.** Still gets Redlight via
  `adb push` with `general.addonupdates = 2` (Never) — this was a deliberate
  test-only exercise on kodi22, not yet rolled to production. Awaiting a
  decision on rollout.
- **Step 4 — `service.redlightpatch` still deployed and doing two jobs**:
  (a) re-healing `folders.py`/`sources.py` .strm patches if Kodi ever
  auto-updates from upstream, and (b) repairing `settings.xml` DebridLibrary
  paths on boot. Job (b) is unrelated to which repo wins and needs a new home
  before (a) can be retired. Not safe to retire (a) until production is
  actually switched over, not just tested.

## Next steps (tracked as tasks)

1. ~~Decide hosting mechanism and wire up serving `dist/` at a stable URL.~~ Done — https://purple-drain.github.io/TheRedWizard/
2. ~~Verify the version tie-break resolves in the fork's favor.~~ Done, on kodi22 (test only).
   **Remaining:** install `repository.purpledrain` on production
   (`org.xbmc.kodi`) and decide whether/when to flip `general.addonupdates`
   off `Never`.
3. Relocate the `settings.xml` DebridLibrary-path repair out of
   `service.redlightpatch`, then retire the service — only after production
   is confirmed on the hosted repo.
