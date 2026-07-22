# Prescrape, Autoplay & Priority Settings — Reference

This covers the settings that control **how fast a scrape resolves** and **whether it auto-plays
or shows you the source picker**. All of these live in Redlight's in-app Settings Manager (not
Kodi's native addon settings screen) — open it from the addon's main menu. Screenshots aren't
included here; grab them yourself from the Settings Manager if you want a visual copy alongside
this doc, since this environment has no way to drive the live Kodi UI.

## The new feature: waterfall (sequential) prescrape

**Setting:** `redlight.prescrape.sequential` (boolean, default **off**)

When off (today's behavior): every provider you've enabled "Check Before Full Search" for is
started **in parallel** and the scrape always waits for **all of them** to finish before deciding
anything — a fast Folders hit does not cancel a still-running RD Cloud/TorBox check.

When **on**: providers are grouped into priority tiers (see table below) and run **one tier at a
time**. As soon as a tier finds a result, the next tier is never even started — e.g. a Folders hit
skips RD Cloud and TorBox entirely for that scrape. Within a tier, everything still races in
parallel (so if two providers share the same priority number, both run concurrently and either
can produce the hit).

Turn this on in the Settings Manager to get the "first bite wins" behavior.

## Provider priority numbers (lower runs first)

These already exist in the addon and now double as the waterfall tier order:

| Setting | Default | Provider |
|---|---|---|
| `redlight.folders.priority` | **6** | Local Folders / DebridLibrary `.strm` |
| `redlight.en.priority` | 7 | Easynews |
| `redlight.aio.priority` | 7 | AIOStreams |
| `redlight.nzb.priority` | 7 | NZB / Usenet |
| `redlight.rd.priority` | 10 | Real-Debrid Cloud |
| `redlight.pm.priority` | 10 | Premiumize Cloud |
| `redlight.ad.priority` | 10 | AllDebrid Cloud |
| `redlight.oc.priority` | 10 | Offcloud Cloud |
| `redlight.tb.priority` | 10 | TorBox Cloud |

With the stock defaults, tiers are already: **Folders (6) → Easynews/AIOStreams/NZB (7) → all
debrid clouds including RD/TorBox (10, run together)**. If you want RD Cloud/TorBox checked ahead
of Easynews/AIOStreams/NZB, lower `redlight.rd.priority`/`redlight.tb.priority` below 7 (or raise
the others above 10) in the Settings Manager's per-provider priority control.

## Whether a provider gets a fast-path check at all

**Per provider, both of these must be on** for it to participate in prescrape/autoplay — the
global "Autoplay Next Episode/Movie" switch alone does **not** enable this for anything except
`external` (torrent) scrapers:

| Setting | Default | Meaning |
|---|---|---|
| `redlight.check.<provider>` | off | "Check Before Full Search" — provider runs in the fast prescrape phase at all |
| `redlight.autoplay.<provider>` | off | If prescrape finds a hit here, auto-play it instead of showing the source picker |

`<provider>` = `folders`, `rd_cloud`, `pm_cloud`, `ad_cloud`, `oc_cloud`, `tb_cloud` (also
easynews/aiostreams/nzb have their own check-before-full-search toggle, but no dedicated autoplay
pair the same way).

**Your specific ask (Folders vs RD Cloud):** both providers ship with identical defaults
(`check.*` and `autoplay.*` both `false`). If RD Cloud "already feels fast," it's because you
flipped its two toggles a while back — flip the same two for Folders to get matching behavior.

## Recommended setup for "local file first, then RD/TorBox, stop full scrape if either hits"

1. Settings Manager → turn on `Check Before Full Search` for **Folders**, **RD Cloud**, **TorBox**.
2. Turn on `Autoplay` (prescrape) for whichever of those you want to skip the picker for — leave
   it off for any provider where you still want to choose the source manually.
3. Turn on the new **Sequential Prescrape** toggle (`redlight.prescrape.sequential`).
4. Leave priorities at their defaults (Folders=6 already ahead of RD/TorBox=10), or adjust if you
   want a different order.

With that, a cached local `.strm` match resolves and is offered/played without RD Cloud or TorBox
ever being started for that scrape; if Folders comes up empty, RD Cloud and TorBox then run
together as the next tier.
