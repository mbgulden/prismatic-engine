# GRO-570 — Photo Inventory Results

**Issue**: GRO-570 (Inventory Synology photo collection — index by activity & location)
**Agent**: ned (infrastructure)
**Status**: Complete (initial pass)
**Script**: `scripts/synology_photo_inventory.py`
**Branch**: `ned/GRO-570`

## Deliverables

| Artifact | Path | Size | Generated |
|---|---|---|---|
| Structured index | `~/mounts/synology-agentic-context/active-oahu/metadata/photo-inventory.json` | 5.24 MiB | 2026-06-26T08:12:30Z |
| Human report | `~/mounts/synology-agentic-context/active-oahu/metadata/photo-inventory-report.md` | 3.0 KiB | 2026-06-26T08:15Z |
| Script | `scripts/synology_photo_inventory.py` | — | committed (e21f69b0) |

## Scope of initial pass

Indexed 6 curated Active Oahu subdirectories under
`/home/ubuntu/mounts/synology-photo/Active Oahu's shared workspace/`:

- Tour & Rental Package Images
- Edited Photos
- Profile Pics
- _All Tour & Rental Photos
- Kailua Photos and Videos
- Blog Posts

`Camera Uploads` (~17K items) was **not** scanned in the initial pass — see
Follow-ups.

## Headline numbers

| Metric | Value |
|---|---|
| Total files indexed | 5,727 |
| Photos | 5,481 |
| Videos | 246 |
| Photos with EXIF | 4,805 (87.7%) |
| Photos with GPS | **0** |
| Total bytes | 125.47 GiB |
| Median file size | 5.28 MiB |
| Scan elapsed | 143.34s |

## Key finding (escalation-worthy)

**No photos in the Active Oahu collection carry GPS data.** This blocks any
geographic clustering, distance-from-venue scoring, or "auto-suggest location
for blog post" feature downstream. Likely cause: Synology Photos strips GPS
on import, or the source library was deliberately scrubbed for privacy. Worth
asking Michael whether GPS stripping is intentional before the next pass
incorporates it.

## Top activity tags (path-derived)

| Activity | Files |
|---|---|
| tour | 2,834 |
| food | 1,550 |
| kayaking | 1,330 |
| snorkeling | 985 |
| swimming | 453 |
| drone | 299 |
| paddleboarding | 277 |
| hiking | 126 |
| landscape | 112 |
| turtle | 111 |

## Top location tags (path-derived)

| Location | Files |
|---|---|
| oahu | 5,727 |
| kailua | 1,622 |
| lanikai | 669 |
| waikiki | 198 |
| honolulu | 194 |
| hawaii | 114 |
| chinamans_hat | 100 |
| turtle_bay | 51 |
| north_shore | 16 |
| haleiwa | 16 |

## Camera makes (from EXIF)

| Make | Cameras |
|---|---|
| SONY | 2,378 |
| Canon | 1,019 |
| Hasselblad | 393 |
| GoPro (incl. 220 trailing-whitespace entries from a misconfigured device) | 342 |
| DJI | 33 |
| Panasonic | 29 |
| Apple | 12 |
| PENTAX | 10 |
| FUJIFILM | 1 |

Note: the "GoPro" with 220 trailing whitespace entries is a real data-quality
finding — likely a GoPro with date/time string padded by a corrupted EXIF
encoder. Worth a one-line sanitizer pass in a future inventory.

## Year distribution (EXIF DateTimeOriginal → mtime fallback)

| Year | Files |
|---|---|
| 2013 | 9 |
| 2014 | 1,449 |
| 2015 | 217 |
| 2016 | 243 |
| 2017 | 18 |
| 2018 | 418 |
| 2019 | 54 |
| 2022 | 473 |
| 2023 | 177 |
| 2024 | 753 |
| 2025 | 1,650 |
| 2026 | 20 |

The 1,650 in 2025 + 20 in 2026 explain the GRO-570 title's "1,600+ photos"
estimate — that figure is recent-year-only, not total.

## Follow-ups

- **GRO-571** (Build photo tagging system): the `tags` field in
  `photo-inventory.json` is the foundation. Extend with usage rights and
  quality rating. Tag taxonomy from the script's `ACTIVITY_KEYWORDS` /
  `LOCATION_KEYWORDS` is the seed.
- **GRO-572** (Auto-generate social posts): the index is now queryable by
  activity, location, year, camera, file size, and `captured_at_iso`.
- **GRO-570 expansion**: re-run with `--include-dirs "Camera Uploads"` for
  the full ~17K files. Expect 15–30 minutes.
- **Data quality**: 220 GoPro cameras with trailing-whitespace EXIF Make
  values. Add a sanitizer when usage-rights tagging lands.
- **GPS**: confirm with Michael whether Synology Photos is stripping GPS on
  import (privacy setting) or whether the source files never had it.
