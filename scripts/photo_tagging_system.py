#!/usr/bin/env python3
"""
Photo Tagging System — GRO-571.

Extends the GRO-570 photo inventory with two additional tag dimensions
the Linear issue requires:

    1. usage_rights   — license/rights inference from path
    2. quality_rating — technical quality from file size + EXIF + heuristics

Activity and location tags are carried through from the upstream
`photo-inventory.json` (GRO-570). This module is read-mostly: it consumes
the inventory and emits an enriched index plus a query CLI.

Usage:

    # Build enriched index (run once after GRO-570 inventory refresh)
    python3 scripts/photo_tagging_system.py tag \\
        --inventory ~/mounts/synology-agentic-context/active-oahu/metadata/photo-inventory.json \\
        --out      ~/mounts/synology-agentic-context/active-oahu/metadata/photo-tagging-index.json

    # Query the enriched index (no re-walk of NAS)
    python3 scripts/photo_tagging_system.py query \\
        --index ~/mounts/synology-agentic-context/active-oahu/metadata/photo-tagging-index.json \\
        --activity kayaking --location kailua --quality hero --rights aot_owned \\
        --limit 20

GRO-571 — Ned (infrastructure)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1

# ── Usage-rights inference ──────────────────────────────────────
# Ordered: first matching rule wins. Curated for the Active Oahu NAS
# layout observed in GRO-570 (top-level parents under "Active Oahu's
# shared workspace/"). Anything outside Active Oahu falls through to
# `unverified`.
#
# Note: rights labels are HEURISTIC, not legal advice. A human still
# needs to confirm before publishing. The "rights_verified" flag on
# the item stays False until manually approved.

RIGHTS_RULES: list[tuple[str, str, str]] = [
    # (regex on rel_path, label, description)
    (r"Profile Pics", "portrait_subject_consent_required",
     "Subject consent required before publication"),
    (r"Instagram photos and videos", "instagram_sourced",
     "Sourced from Instagram — third-party rights uncertain"),
    (r"_All Tour & Rental Photos", "aot_owned",
     "Active Oahu-owned marketing photography"),
    (r"Tour & Rental Package Images", "aot_owned",
     "Active Oahu-owned marketing photography"),
    (r"Kailua Photos and Videos", "aot_owned_or_licensed",
     "AOT-owned or vendor-licensed — confirm per file"),
    (r"Blog Posts/", "aot_owned_blog",
     "Blog post assets — owned, but per-image attribution may apply"),
    (r"Edited Photos", "aot_owned_edited",
     "AOT-edited derivative of owned or licensed originals"),
]

DEFAULT_RIGHTS = "unverified"

# Paths that look like source social/dropbox copies get a clearer warning.
EXTERNAL_DOWNLOAD_HINTS = ("Instagram", "facebook", "twitter", "Dropbox")


def infer_usage_rights(rel_path: str) -> dict[str, Any]:
    """Return {label, description, verified} for a file."""
    for pattern, label, desc in RIGHTS_RULES:
        if re.search(pattern, rel_path):
            return {
                "label": label,
                "description": desc,
                "verified": False,
                "method": "path-heuristic",
            }
    # Detect generic external-download signs even when no rule matches
    if any(h in rel_path for h in EXTERNAL_DOWNLOAD_HINTS):
        return {
            "label": "external_download",
            "description": "Looks like an external/downloaded source — verify origin",
            "verified": False,
            "method": "path-heuristic",
        }
    return {
        "label": DEFAULT_RIGHTS,
        "description": "No rights metadata — treat as unverified until reviewed",
        "verified": False,
        "method": "default",
    }


# ── Quality rating ──────────────────────────────────────────────
# Pure path/size/EXIF heuristics. Real QA (sharpness, exposure, face
# quality) would need ML; that's a follow-up. These tiers are good
# enough to filter obvious "thumbnail" vs "printable" files.

QUALITY_THRESHOLDS = {
    # bytes; below this => low
    "low_max_bytes": 100 * 1024,
    # bytes; below this => good, above => hero
    "hero_min_bytes": 3 * 1024 * 1024,
}

LOGO_HINTS = ("logo", "icon", "favicon", "watermark")


def _looks_like_logo(rel_path: str) -> bool:
    fn = os.path.basename(rel_path).lower()
    return any(h in fn for h in LOGO_HINTS)


def rate_quality(item: dict[str, Any]) -> dict[str, Any]:
    """Return {rating, reasons[]} for a single inventory item."""
    reasons: list[str] = []
    rating = "unknown"

    kind = item.get("kind")
    size = item.get("size_bytes") or 0
    has_exif = bool(item.get("exif"))
    path = item.get("rel_path", "")

    if kind != "photo":
        # Videos: rate on size only; we don't decode them.
        if size >= 50 * 1024 * 1024:
            rating = "hero"
            reasons.append("video>=50MB")
        elif size >= 5 * 1024 * 1024:
            rating = "good"
            reasons.append("video>=5MB")
        elif size > 0:
            rating = "low"
            reasons.append("video<5MB")
        else:
            rating = "unknown"
            reasons.append("no size")
        return {"rating": rating, "reasons": reasons, "method": "size-only"}

    # Photos
    if _looks_like_logo(path):
        rating = "logo"
        reasons.append("filename/logo heuristic")
        return {"rating": rating, "reasons": reasons, "method": "logo-heuristic"}

    if size <= 0:
        rating = "unknown"
        reasons.append("no size")
        return {"rating": rating, "reasons": reasons, "method": "size-only"}

    if size < QUALITY_THRESHOLDS["low_max_bytes"]:
        rating = "low"
        reasons.append(f"size<{QUALITY_THRESHOLDS['low_max_bytes']}")
    elif size >= QUALITY_THRESHOLDS["hero_min_bytes"]:
        rating = "hero"
        reasons.append(f"size>={QUALITY_THRESHOLDS['hero_min_bytes']}")
    else:
        rating = "good"
        reasons.append(f"size in [{QUALITY_THRESHOLDS['low_max_bytes']}, "
                       f"{QUALITY_THRESHOLDS['hero_min_bytes']})")

    # EXIF presence bumps hero toward certainty but doesn't change the bucket
    if has_exif:
        reasons.append("exif present")
    else:
        reasons.append("no exif")

    return {"rating": rating, "reasons": reasons, "method": "size+exif"}


# ── Index builder ───────────────────────────────────────────────

def build_tagging_index(inventory: dict[str, Any]) -> dict[str, Any]:
    """Enrich the inventory with rights + quality; emit the new index."""
    started = dt.datetime.now(tz=dt.timezone.utc)
    items_out: list[dict[str, Any]] = []
    rights_counter: Counter = Counter()
    quality_counter: Counter = Counter()
    activity_counter: Counter = Counter()
    location_counter: Counter = Counter()

    for item in inventory.get("items", []):
        enriched = dict(item)  # shallow copy — preserves all GRO-570 fields
        enriched["usage_rights"] = infer_usage_rights(item.get("rel_path", ""))
        enriched["quality"] = rate_quality(item)
        items_out.append(enriched)

        rights_counter[enriched["usage_rights"]["label"]] += 1
        quality_counter[enriched["quality"]["rating"]] += 1
        for a in enriched.get("tags", {}).get("activities", []):
            activity_counter[a] += 1
        for l in enriched.get("tags", {}).get("locations", []):
            location_counter[l] += 1

    finished = dt.datetime.now(tz=dt.timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "issue": "GRO-571",
        "upstream_issue": "GRO-570",
        "agent": "ned",
        "generated_at_iso": finished.isoformat(),
        "started_at_iso": started.isoformat(),
        "elapsed_seconds": round((finished - started).total_seconds(), 3),
        "source_inventory": inventory.get("generated_at_iso"),
        "total_items": len(items_out),
        "facets": {
            "rights": dict(rights_counter),
            "quality": dict(quality_counter),
            "activities": dict(activity_counter),
            "locations": dict(location_counter),
        },
        "items": items_out,
    }


# ── Query ───────────────────────────────────────────────────────

def _any_match(haystack: list[str], needles: Iterable[str]) -> bool:
    return any(n in haystack for n in needles)


def query_items(index: dict[str, Any], *,
                activities: list[str] | None = None,
                locations: list[str] | None = None,
                rights: list[str] | None = None,
                qualities: list[str] | None = None,
                year: int | None = None,
                limit: int | None = None,
                offset: int = 0) -> list[dict[str, Any]]:
    """Filter enriched items by any combination of facets."""
    out: list[dict[str, Any]] = []
    for item in index.get("items", []):
        if activities and not _any_match(item.get("tags", {}).get("activities", []), activities):
            continue
        if locations and not _any_match(item.get("tags", {}).get("locations", []), locations):
            continue
        if rights:
            label = item.get("usage_rights", {}).get("label")
            if label not in rights:
                continue
        if qualities:
            q = item.get("quality", {}).get("rating")
            if q not in qualities:
                continue
        if year is not None:
            cap = item.get("captured_at_iso") or item.get("mtime_iso") or ""
            try:
                if not cap.startswith(str(year)):
                    continue
            except Exception:
                continue
        out.append(item)
    if offset:
        out = out[offset:]
    if limit is not None:
        out = out[:limit]
    return out


def summarize_query(index: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build facet counts for the filtered set."""
    rc: Counter = Counter()
    qc: Counter = Counter()
    for r in results:
        rc[r.get("usage_rights", {}).get("label", "?")] += 1
        qc[r.get("quality", {}).get("rating", "?")] += 1
    return {
        "matched": len(results),
        "total": index.get("total_items", 0),
        "rights_breakdown": dict(rc),
        "quality_breakdown": dict(qc),
    }


# ── CLI ─────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. ``argv`` is exposed for tests."""
    if argv is None:
        argv = sys.argv[1:]
    p = argparse.ArgumentParser(description="GRO-571 photo tagging system.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_tag = sub.add_parser("tag", help="Enrich a GRO-570 inventory with rights + quality.")
    p_tag.add_argument("--inventory", required=True,
                       help="Path to GRO-570 photo-inventory.json")
    p_tag.add_argument("--out", required=True,
                       help="Output enriched index path")
    p_tag.add_argument("--dry-run", action="store_true",
                       help="Compute facets but don't write the file")

    p_q = sub.add_parser("query", help="Search the enriched index.")
    p_q.add_argument("--index", required=True, help="Path to GRO-571 enriched index")
    p_q.add_argument("--activity", action="append", default=[],
                     help="Activity tag (repeatable, ANY-match)")
    p_q.add_argument("--location", action="append", default=[],
                     help="Location tag (repeatable, ANY-match)")
    p_q.add_argument("--rights", action="append", default=[],
                     help="Usage-rights label (repeatable, ANY-match)")
    p_q.add_argument("--quality", action="append", default=[],
                     help="Quality rating (repeatable, ANY-match)")
    p_q.add_argument("--year", type=int, default=None, help="Filter by capture/mtime year")
    p_q.add_argument("--limit", type=int, default=20)
    p_q.add_argument("--offset", type=int, default=0)
    p_q.add_argument("--paths-only", action="store_true",
                     help="Print only rel_paths, one per line")

    args = p.parse_args(argv)

    if args.cmd == "tag":
        inventory_path = Path(args.inventory)
        if not inventory_path.exists():
            print(f"ERROR: inventory not found: {inventory_path}", file=sys.stderr)
            return 2
        inventory = _read_json(inventory_path)
        enriched = build_tagging_index(inventory)
        if args.dry_run:
            print(json.dumps({
                "dry_run": True,
                "total_items": enriched["total_items"],
                "facets": enriched["facets"],
                "elapsed_seconds": enriched["elapsed_seconds"],
            }, indent=2))
            return 0
        out_path = Path(args.out)
        _write_json(out_path, enriched)
        print(f"[ned/gro-571] wrote {out_path} "
              f"({out_path.stat().st_size:,} bytes, "
              f"{enriched['total_items']} items, "
              f"{enriched['elapsed_seconds']}s)",
              file=sys.stderr)
        return 0

    if args.cmd == "query":
        index_path = Path(args.index)
        if not index_path.exists():
            print(f"ERROR: index not found: {index_path}", file=sys.stderr)
            return 2
        idx = _read_json(index_path)
        results = query_items(
            idx,
            activities=args.activity or None,
            locations=args.location or None,
            rights=args.rights or None,
            qualities=args.quality or None,
            year=args.year,
            limit=args.limit,
            offset=args.offset,
        )
        summary = summarize_query(idx, results)
        if args.paths_only:
            for r in results:
                print(r.get("rel_path", ""))
        else:
            print(json.dumps({
                "summary": summary,
                "results": results,
            }, indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())