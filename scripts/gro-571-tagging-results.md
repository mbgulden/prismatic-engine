# GRO-571 — Photo Tagging System (delivery report)

**Issue**: GRO-571 — Build photo tagging system — activity, location, usage rights
**Agent**: ned (infrastructure)
**Status**: Complete
**Branch**: `ned/GRO-571`
**Depends on**: GRO-570 (Synology photo inventory — moved to In Review 2026-06-26T17:15Z)

## Why now

GRO-571 had been triaged 18+ times as "blocked on GRO-570 + NAS mount empty."
The 2026-06-26 17:13Z Ned routing-triage noted GRO-570 was finalized at 17:15Z
and the photo mount had been repopulated (94 entries). This run is the first
with both blockers cleared.

GRO-570's own finalization report explicitly handed off:
> "GRO-571 (Build photo tagging system): enrich this index with usage rights
> and quality rating. Use the `tags` field as the foundation."

## Deliverables

| Artifact | Path | Size |
|---|---|---|
| Script | `scripts/photo_tagging_system.py` | 390 lines |
| Tests | `tests/test_photo_tagging_system.py` | 329 lines, **26 tests pass** |
| Enriched index | `~/mounts/synology-agentic-context/active-oahu/metadata/photo-tagging-index.json` | 7.28 MiB |
| This report | `scripts/gro-571-tagging-results.md` | this file |

## What it does

The script consumes the GRO-570 inventory (`photo-inventory.json`, 5,727 items)
and adds **two new tag dimensions**:

1. **`usage_rights`** — license/rights inference from path:
   - `aot_owned` (2,833 files) — Tour & Rental package images
   - `aot_owned_blog` (1,587) — Blog Posts subtree
   - `aot_owned_or_licensed` (1,302) — Kailua Photos (vendor licensing needs per-file confirm)
   - `aot_owned_edited` (4) — Edited Photos subtree
   - `portrait_subject_consent_required` (1) — Profile Pics (consent gate before publish)
   - Heuristics default to `unverified`; `rights.verified` stays False until manually approved.

2. **`quality`** — technical rating from size + EXIF + filename heuristics:
   - `hero` (3,647) — ≥3 MiB photos or ≥50 MiB videos
   - `good` (2,068) — 100 KiB – 3 MiB photos, 5–50 MiB videos
   - `low` (9) — <100 KiB
   - `logo` (3) — filename heuristic (logo/icon/favicon/watermark)
   - `unknown` — zero-byte / no size metadata

Activity and location tags from GRO-570 are passed through unchanged.

## Query interface

```
python3 scripts/photo_tagging_system.py query \
  --index <path> \
  [--activity TAG ...] [--location TAG ...] \
  [--rights LABEL ...] [--quality RATING ...] \
  [--year N] [--limit N] [--offset N] [--paths-only]
```

Facets use ANY-match (logical OR within a dimension, AND across dimensions).
Year filter prefers EXIF `captured_at_iso`, falls back to `mtime_iso`.
`--paths-only` streams plain paths for shell pipelines.

### Real-data query examples (against the 5,727-item index)

```bash
# Hero kayaking shots at Kailua in 2024
python3 scripts/photo_tagging_system.py query \
  --index ~/mounts/synology-agentic-context/active-oahu/metadata/photo-tagging-index.json \
  --activity kayaking --location kailua --quality hero --year 2024 --paths-only

# Profile pics needing consent (matched: 1)
python3 scripts/photo_tagging_system.py query \
  --index ... --rights portrait_subject_consent_required
# → Active Oahu's shared workspace/Profile Pics/Tour-Profile-Michael.jpg (1,085,565 bytes, good)

# 2025 snorkel shots, any quality (matched: 3)
#   quality breakdown: {good: 2, hero: 1}
#   rights breakdown: {aot_owned: 3}
```

## Smoke verification (2026-06-26 20:15Z)

```text
[ned/gro-571] wrote .../photo-tagging-index.json (7,631,133 bytes, 5727 items, 0.049s)
```

26/26 tests pass in 0.09s. No regressions in adjacent
`test_universal_asset_indexer.py` (38 tests).

## Design choices / trade-offs

- **Read-mostly.** Module consumes the GRO-570 inventory; it does not re-walk
  the NAS. The re-walk cost (~140s on 5,727 files, multi-hour on 17K+ Camera
  Uploads) belongs in GRO-570's script. GRO-571 just enriches what's there.
- **All rights labels carry `verified=False`.** The label is a heuristic; the
  canonical "verified" gate stays human-driven via a future per-issue review
  workflow. Don't ship rights labels to a publisher without confirmation.
- **Quality is intentionally coarse.** ML sharpness/exposure/face-quality is
  out of scope for this delivery. The current tiers let downstream consumers
  filter out obvious thumbnails and pick print-grade heroes, which is what
  GRO-571's spec ("searchable") actually needs.
- **Schema versioning.** Output is `schema_version: 1` with `upstream_issue`
  pointer. GRO-570 v2 (when Camera Uploads is added) will produce a new
  inventory; rerunning `tag` is idempotent on the same inventory.

## Follow-ups (for Michael / next issue)

- **Camera Uploads (~17K files)** — re-run GRO-570 with
  `--include-dirs "Camera Uploads"` and then re-run this script. Expect
  ~15–30 min inventory + a few seconds tagging. Will surface the bulk of
  the social/UGC content that the current 6-dir subset doesn't reach.
- **Rights verification UI** — once a human has confirmed rights for a batch,
  a flag flip should persist. Out of scope for this autonomous delivery.
- **ML quality pass** — when Active Oahu commissions real QA scoring
  (sharpness, faces, composition), plumb a separate `quality.ml` field so
  the heuristic `quality.rating` and the ML score coexist.

## Source

- Script: `scripts/photo_tagging_system.py`
- Tests: `tests/test_photo_tagging_system.py` (26 passing)
- Branch: `ned/GRO-571` (commit `ff59c54f`)
- Enrichment input: GRO-570 inventory, `ned/GRO-570` branch