# The faux-fork pattern for third-party Kodi add-ons

Reference doc, not a status log. For one add-on's rollout history and
current state, see `docs/HOSTED_REPO_STATUS.md` (Redlight) or that
add-on's own repo. This file exists because the same pattern has now been
used three times (Redlight, the Arctic Fuse 3 skin, beIN Sports Connect)
and shouldn't have to be re-derived from conversation each time.

## What problem this solves

A third-party Kodi add-on needs a local patch (a bug fix, a restored
feature, new routes) that upstream either hasn't shipped or has
deliberately removed. Two ways to keep that patch alive on the Shield:

1. A boot-time patch service (`service.redlightpatch`,
   `service.beinpatch`): re-applies string edits to the installed add-on's
   files every time Kodi starts. Works, but every edit is a diff against
   whatever upstream currently ships, so an upstream update can shift the
   anchor text and silently stop the patch from applying.
2. **The faux fork**: install our own build of the add-on instead of
   upstream's, from our own Kodi repository. No patching at boot, because
   the patch is already baked into the file on disk.

This doc is about option 2, which is where every instance ends up once
the patch is more than a couple of lines.

## The mechanism, in order

1. **Baseline commit.** Vendor upstream's own released zip, unmodified,
   as the first commit(s), with a message crediting the original author
   and the exact source (repo, version). This is what makes the
   difference from upstream a clean, reviewable diff, and what an
   eventual upstream PR would look like.
2. **Our changes on top**, as ordinary commits. No patch markers, no
   re-apply-at-boot machinery, because this file *is* the source now.
3. **Version bump**: `<upstream-version>+pd.N` (e.g. `2.0.3+pd.1`,
   `0.5.2+pd.1`). Not a colon-based epoch; colons aren't legal in
   Windows/Android filenames, and the zip filename carries the version.
   Kodi's own version comparison sorts `+pd.1` above the bare upstream
   version, which is the entire trick: two repositories can both offer the
   same add-on id, and Kodi installs the higher one. Confirmed empirically
   for Redlight on 2026-07-26 (`Addons33.db` flipped `origin` to
   `repository.purpledrain` on its own).
4. **Build**: `tools/build-repo.sh <addon-folder> repository.purpledrain`
   in this repo produces a Kodi-repo-shaped `dist/` (gitignored):
   `addons.xml`, `addons.xml.md5`, and per-addon
   `<id>/<id>-<version>.zip` plus icon/fanart/changelog.
5. **Publish**: `.github/workflows/publish-repo.yml` runs the build and
   pushes `dist/` to an orphan `gh-pages` branch
   (`peaceiris/actions-gh-pages`, `force_orphan: true`, action pinned to a
   commit SHA). GitHub Pages serves it at
   `https://purple-drain.github.io/TheRedWizard/`.
6. **`repository.purpledrain`** (its own tiny addon, `addon.xml` in this
   repo) is the Kodi repository add-on installed on-device; it points at
   that Pages URL. Installing it is a one-time device step; after that,
   Kodi's own repo-refresh cycle keeps pulling whatever this pipeline
   publishes.
7. **When upstream ships a new version**: rebase our commits onto it,
   bump to `<new-version>+pd.1`, rebuild. Until that rebase lands, set
   `general.addonupdates` away from automatic for that add-on (or rely on
   the version tie-break, which only protects us once our version is
   actually higher than whatever upstream just shipped) so an upstream
   release can't silently reinstall over ours in the gap.
8. **Retire the boot-patch service**, if one existed, only after
   production has confirmed the hosted version is actually installed and
   working, not just built. `service.redlightpatch` was kept running past
   its main job for a second, unrelated reason (a settings repair) — check
   for that kind of incidental second duty before deleting a patch service
   outright.

## Two shapes the source repo takes

- **Committed directly into `TheRedWizard`** (Redlight): simplest, one
  repo, one workflow. Only sensible when the source itself is fine being
  public — it will be served publicly as a zip regardless, but this shape
  also makes the raw, browsable source public.
- **A separate repo** (the AF3 skin fork, `github.com/Purple-Drain/skin.arctic.fuse.3`;
  beIN's private fork, `github.com/Purple-Drain/plugin.video.beinsports.apac`):
  needed when the source itself should stay private, or when it's a
  genuinely separate project better tracked on its own. That repo does
  its own baseline+changes+version-bump (steps 1-3 above); only the
  **built zip** and a minimal `addon.xml` for `build-repo.sh` to read ever
  land in `TheRedWizard`, never the raw source — otherwise a "private"
  fork's source becomes public the moment it's added to this repo's
  tracked paths.

## Standing rules this pattern must respect

- **`TheRedWizard`'s `gh-pages` is a Production Deploy** (real devices
  install from it). A merge to `main` here publishes live. The user runs
  that merge; hand them the exact `gh pr merge` line rather than running
  it. See the global `CLAUDE.md` decision on this (#390).
- **Never install, restart Kodi, or otherwise touch the Shield/TCL
  directly** from a session working on this. The device is single-operator
  (the media stack tracker); hand it exact steps instead.
- A source-code question about whether a fork "needs permission" from the
  upstream author: check for an actual licence or notice first (most of
  these add-ons ship with an empty `<license>` tag and no `LICENSE` file);
  don't assume either way from vibes.
