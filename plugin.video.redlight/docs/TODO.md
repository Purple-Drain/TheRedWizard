# Redlight — Open Items

Snapshot as of 2026-07-23. Session covered: full addon review (~26 findings fixed
across 15+ commits), sequential-prescrape/waterfall feature (opt-in, off by
default), settings reference doc, version bump to 1.9.10, live deploy + log
verification on the Shield, and a post-deploy crash fix (missing
`prescrape_sequential()` accessor + missing settings-UI control for it).

## Open / carried forward

1. **Kodi repository/zip hosting**
   Explore turning this repo into an installable Kodi source (a
   `repository.redlight` addon + hosted `addons.xml`/zips/md5s) so updates land
   via Kodi's own addon updater instead of manual adb pushes. Not scoped yet —
   needs a decision on hosting (GitHub Pages / raw GitHub / other) before any
   code is written.

2. **Debrid-Link native provider support**
   Add Debrid-Link as a first-class scraper/indexer (parallel to RD, AllDebrid,
   Premiumize, Offcloud, TorBox) rather than only being reachable via the
   existing WebDAV Kodi source. Research done: confirmed v2 REST API shape
   (`https://debrid-link.fr/api/v2/`, Bearer auth, `seedbox/*` + `downloader/*`
   endpoints). Decided approach: **static personal API key** (from
   debrid-link.com/token_app) rather than OAuth device-code flow, since the
   device-flow endpoint details couldn't be confirmed and a bad guess would
   silently break. Still to build: `apis/debridlink_api.py` client,
   `scrapers/dl_cloud.py`-style cloud scraper, `indexers/debridlink.py`
   (browse/resolve/account-info/remove), settings entries + settings_manager.xml
   controls, router wiring, cache integration.

3. **`sources.py` busy-flag races** (from the original full review)
   `_clear_stale_resolve_busy`, `_clear_stale_sources_busy`, and the
   `_NEXTEP_AUTOPLAY_STASH` check-then-act race are TOCTOU-style issues that
   need real playback testing on-device (rapid next-episode navigation, cancel
   mid-resolve) rather than static review — flagged but not yet exercised live.

4. **kodi-shield-config: JSON-RPC credential pair in `sanitize.py`**
   Offered adding a named `KODI_JSONRPC_USER`/`PASS` pair (mirroring the WebDAV
   creds pattern) so the kodi/billie web-server login for the Shield
   (10.1.1.30) is captured the same way. Not implemented — would require
   extending `sanitize.py`'s `_HOST_PREFIX`/`_ALWAYS_SECRET_IDS`, not hand-editing
   the auto-regenerated `secrets.env`. Needs your go-ahead before touching that
   repo's masking logic.

5. **Standalone secrets/masking doc for kodi-shield-config**
   Offered a short doc explaining the sanitize.py mask/unmask/extract scheme
   (no encryption — placeholder substitution, secrets.env is gitignored and
   fully regenerated per `backup.sh` run). Answered in-chat; not yet written as
   a file. Low priority, easy to do whenever.

## Verify later (not urgent)

- Re-confirm 1.9.10 shows correctly in Kodi's addon info screen post-deploy
  (pushed and log-verified, but UI display itself wasn't re-checked).
