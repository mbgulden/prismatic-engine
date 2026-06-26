#!/usr/bin/env python3
"""
Synology Photo Inventory — mechanical indexer for GRO-570.

Walks a Synology-mounted photo directory, extracts EXIF metadata (where
available), and emits a structured index JSON with file paths, sizes, mtimes,
EXIF (DateTimeOriginal, GPS, Camera), and lightweight activity / location
heuristics derived from directory names and GPS coordinates.

This is a deterministic, mechanical inventory pass. It does NOT modify source
photos. Output is written to the agentic-context NAS (approved write target
per SYNOLOGY_STORAGE_PLAN.md).

Usage:
    python3 scripts/synology_photo_inventory.py \
        --root /home/ubuntu/mounts/synology-photo \
        --out /home/ubuntu/mounts/synology-agentic-context/active-oahu/metadata/photo-inventory.json \
        [--limit 5000] [--include-dirs "Active Oahu's shared workspace" "Camera Uploads"]

GRO-570 — Ned (infrastructure)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

try:
    from PIL import Image, Image as _Image, ExifTags as _ExifTags
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    _Image = None  # type: ignore[assignment]
    _ExifTags = None  # type: ignore[assignment]

# Decompression bomb guard — Synology Camera Uploads often has gigantic panoramas
if PIL_AVAILABLE and _Image is not None:
    _Image.MAX_IMAGE_PIXELS = 250_000_000  # 250 MP cap (up from 89 MP default)


# ── Activity & location heuristics ──────────────────────────────
# Lightweight keyword → activity tag mapping. Curated for Oahu/Hawaii content
# but falls back gracefully for unknown content. Anything not matched is left
# untagged (caller can extend the taxonomy).

ACTIVITY_KEYWORDS: dict[str, list[str]] = {
    "kayaking": ["kayak", "kayaking"],
    "snorkeling": ["snorkel", "snorkeling", "reef"],
    "surfing": ["surf", "surfing", "wave"],
    "hiking": ["hike", "hiking", "trail", "ridge"],
    "swimming": ["swim", "swimming", "beach", "pool"],
    "paddleboarding": ["sup", "paddleboard", "paddle"],
    "diving": ["dive", "diving", "scuba"],
    "sailing": ["sail", "sailing", "boat", "catamaran"],
    "whale_watching": ["whale", "humpback"],
    "dolphin": ["dolphin"],
    "turtle": ["turtle", "honu"],
    "food": ["food", "restaurant", "plate", "meal", "shrimp", "poke"],
    "sunset": ["sunset", "golden hour"],
    "sunrise": ["sunrise"],
    "landscape": ["mountain", "valley", "coast", "ocean", "palm"],
    "portrait": ["portrait", "selfie", "face", "people"],
    "tour": ["tour", "guide", "group"],
    "drone": ["drone", "aerial"],
}

LOCATION_KEYWORDS: dict[str, list[str]] = {
    "north_shore": ["north shore", "haleiwa", "waimea"],
    "lanikai": ["lanikai"],
    "kailua": ["kailua"],
    "waikiki": ["waikiki"],
    "honolulu": ["honolulu", "ala moana", "diamond head"],
    "ko_olina": ["ko olina", "ko'olina"],
    "haleiwa": ["haleiwa"],
    "pipeline": ["pipeline", "ehukai"],
    "waimea": ["waimea"],
    "turtle_bay": ["turtle bay"],
    "chinamans_hat": ["chinamans hat", "mokolii"],
    "hanauma": ["hanauma"],
    "makaha": ["makaha"],
    "sunset_beach": ["sunset beach"],
    "waimanalo": ["waimanalo"],
    "oahu": ["oahu"],
    "hawaii": ["hawaii", "aloha"],
}


def tag_from_path(path: str) -> dict[str, list[str]]:
    """Return activity and location tags inferred from the path."""
    haystack = path.lower()
    activities: list[str] = []
    for tag, kws in ACTIVITY_KEYWORDS.items():
        if any(kw in haystack for kw in kws):
            activities.append(tag)
    locations: list[str] = []
    for tag, kws in LOCATION_KEYWORDS.items():
        if any(kw in haystack for kw in kws):
            locations.append(tag)
    return {"activities": activities, "locations": locations}


# ── EXIF extraction ─────────────────────────────────────────────

def extract_exif(path: Path) -> dict[str, Any]:
    """Best-effort EXIF extraction. Returns {} on failure."""
    if not PIL_AVAILABLE:
        return {}
    try:
        with Image.open(path) as img:
            exif_raw = img.getexif()
            if not exif_raw:
                return {}
            assert _ExifTags is not None  # guarded by PIL_AVAILABLE
            tag_map = {_ExifTags.TAGS.get(k, k): v for k, v in exif_raw.items()}
            out: dict[str, Any] = {}
            for key in ("DateTimeOriginal", "DateTime", "Make", "Model",
                        "Orientation", "Software"):
                if key in tag_map:
                    val = tag_map[key]
                    if isinstance(val, bytes):
                        try:
                            val = val.decode("utf-8", errors="replace").strip("\x00").strip()
                        except Exception:
                            continue
                    out[key] = val
            # GPS
            gps_info = tag_map.get("GPSInfo")
            if gps_info:
                gps: dict[str, Any] = {}
                assert _ExifTags is not None
                for k, v in gps_info.items():
                    name = _ExifTags.GPSTAGS.get(k, str(k))
                    if isinstance(v, bytes):
                        try:
                            v = v.decode("utf-8", errors="replace")
                        except Exception:
                            continue
                    gps[name] = v
                out["GPSInfo"] = gps
            return out
    except Exception:
        return {}


def parse_exif_datetime(s: Any) -> str | None:
    """Normalize EXIF datetime 'YYYY:MM:DD HH:MM:SS' to ISO 8601."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def stat_file(path: Path) -> dict[str, Any]:
    """File-level metadata (size, mtime, sha-prefix)."""
    try:
        st = path.stat()
    except OSError as e:
        return {"error": str(e)}
    return {
        "size_bytes": st.st_size,
        "mtime_iso": dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc).isoformat(),
    }


# ── File-type classifier ────────────────────────────────────────

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".tiff", ".tif"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp"}


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return "other"


# ── Walker ──────────────────────────────────────────────────────

def iter_media(root: Path, include_dirs: list[str] | None = None) -> Iterator[Path]:
    """Yield media files under root (optionally filtered to specific subdirs)."""
    if include_dirs:
        for sub in include_dirs:
            base = root / sub
            if not base.exists():
                continue
            yield from _walk(base)
    else:
        yield from _walk(root)


def _walk(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Skip Synology metadata dirs to keep index clean
        dirnames[:] = [d for d in dirnames if d not in {"@eaDir", "#recycle", ".tmp.drivedownload"}]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in PHOTO_EXT or ext in VIDEO_EXT:
                yield Path(dirpath) / name


# ── Index builder ───────────────────────────────────────────────

def build_index(root: Path, include_dirs: list[str] | None, limit: int | None,
                progress_every: int = 1000) -> dict[str, Any]:
    started = time.time()
    items: list[dict[str, Any]] = []
    counters = {"photo": 0, "video": 0, "other": 0, "with_exif": 0, "with_gps": 0, "errors": 0}

    for i, path in enumerate(iter_media(root, include_dirs)):
        if limit and i >= limit:
            break

        kind = classify(path)
        if kind in counters:
            counters[kind] += 1
        else:
            counters["other"] += 1

        rel = str(path.relative_to(root))
        entry: dict[str, Any] = {
            "rel_path": rel,
            "abs_path": str(path),
            "kind": kind,
            "ext": path.suffix.lower(),
            "filename": path.name,
            "parent_dir": str(path.parent.relative_to(root)),
            **stat_file(path),
            "tags": tag_from_path(rel),
        }

        if kind == "photo":
            exif = extract_exif(path)
            if exif:
                counters["with_exif"] += 1
                entry["exif"] = exif
                iso = parse_exif_datetime(exif.get("DateTimeOriginal") or exif.get("DateTime"))
                if iso:
                    entry["captured_at_iso"] = iso
                if "GPSInfo" in exif:
                    counters["with_gps"] += 1
                    entry["has_gps"] = True

        items.append(entry)

        if progress_every and (i + 1) % progress_every == 0:
            elapsed = time.time() - started
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"[ned/gro-570] progress: {i+1} files ({counters['photo']} photo / "
                  f"{counters['video']} video / {counters['with_exif']} w-exif) "
                  f"{rate:.1f} files/s", file=sys.stderr, flush=True)

    elapsed = time.time() - started
    return {
        "schema_version": 1,
        "issue": "GRO-570",
        "agent": "ned",
        "generated_at_iso": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "root": str(root),
        "include_dirs": include_dirs or "all",
        "counters": counters,
        "elapsed_seconds": round(elapsed, 2),
        "total_indexed": len(items),
        "items": items,
    }


# ── CLI ─────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Inventory a Synology photo collection (GRO-570).")
    p.add_argument("--root", default="/home/ubuntu/mounts/synology-photo",
                   help="Photo root directory (default: Synology photo mount).")
    p.add_argument("--out", default="/home/ubuntu/mounts/synology-agentic-context/active-oahu/metadata/photo-inventory.json",
                   help="Output JSON path (default: agentic-context NAS).")
    p.add_argument("--include-dirs", nargs="*", default=None,
                   help="Restrict scan to these top-level subdirs (relative to --root).")
    p.add_argument("--limit", type=int, default=None,
                   help="Stop after N files (for smoke tests).")
    p.add_argument("--progress-every", type=int, default=1000,
                   help="Log progress every N files (default 1000; 0 to disable).")
    p.add_argument("--dry-run", action="store_true", help="Walk but don't write output.")
    args = p.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: root does not exist: {root}", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[ned/gro-570] scanning {root} (include_dirs={args.include_dirs}, limit={args.limit})", file=sys.stderr)
    index = build_index(root, args.include_dirs, args.limit, progress_every=args.progress_every)
    print(f"[ned/gro-570] indexed={index['total_indexed']} "
          f"photo={index['counters']['photo']} video={index['counters']['video']} "
          f"with_exif={index['counters']['with_exif']} with_gps={index['counters']['with_gps']} "
          f"elapsed={index['elapsed_seconds']}s", file=sys.stderr)

    if args.dry_run:
        print(json.dumps({"dry_run": True, "total_indexed": index["total_indexed"],
                          "counters": index["counters"]}, indent=2))
        return 0

    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp_path, out_path)
    print(f"[ned/gro-570] wrote {out_path} ({out_path.stat().st_size:,} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())