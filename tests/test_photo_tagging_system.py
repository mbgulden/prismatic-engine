"""
Tests for GRO-571 photo_tagging_system.

Covers:
- usage_rights inference (all known path patterns)
- quality rating thresholds (hero/good/low/logo/unknown)
- index builder (facets, item count, all GRO-570 fields preserved)
- query CLI semantics (any-match, year filter, pagination, facets in summary)
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# Make scripts/ importable
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import photo_tagging_system as pts  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────


def _item(*, rel_path: str, kind: str = "photo", size_bytes: int = 1_000_000,
          exif: dict | None = None, captured_at_iso: str | None = None,
          mtime_iso: str | None = "2024-06-15T12:00:00+00:00",
          activities: list[str] | None = None,
          locations: list[str] | None = None) -> dict:
    return {
        "rel_path": rel_path,
        "abs_path": f"/fake/{rel_path}",
        "kind": kind,
        "ext": ".jpg" if kind == "photo" else ".mp4",
        "filename": os.path.basename(rel_path),
        "parent_dir": os.path.dirname(rel_path),
        "size_bytes": size_bytes,
        "mtime_iso": mtime_iso,
        "tags": {
            "activities": activities or [],
            "locations": locations or [],
        },
        **({"exif": exif} if exif else {}),
        **({"captured_at_iso": captured_at_iso} if captured_at_iso else {}),
    }


def _inventory(items: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "issue": "GRO-570",
        "agent": "ned",
        "generated_at_iso": "2026-06-26T08:12:30+00:00",
        "root": "/fake",
        "include_dirs": "all",
        "counters": {"photo": sum(1 for i in items if i["kind"] == "photo"),
                     "video": sum(1 for i in items if i["kind"] == "video"),
                     "with_exif": 0, "with_gps": 0, "errors": 0, "other": 0},
        "elapsed_seconds": 1.0,
        "total_indexed": len(items),
        "items": items,
    }


# ── Rights inference ────────────────────────────────────────────


def test_rights_profile_pics_requires_consent():
    r = pts.infer_usage_rights("Active Oahu's shared workspace/Profile Pics/jess.jpg")
    assert r["label"] == "portrait_subject_consent_required"
    assert r["verified"] is False
    assert "consent" in r["description"].lower()


def test_rights_tour_photos_marked_aot_owned():
    r = pts.infer_usage_rights("Active Oahu's shared workspace/_All Tour & Rental Photos/jess/IMG_1234.jpg")
    assert r["label"] == "aot_owned"
    assert r["method"] == "path-heuristic"


def test_rights_instagram_source_flagged():
    r = pts.infer_usage_rights("PC/Downloads/Instagram photos and videos/active_oahu_001.jpg")
    assert r["label"] == "instagram_sourced"


def test_rights_blog_post_path():
    r = pts.infer_usage_rights("Active Oahu's shared workspace/Blog Posts/Beaches/Lanikai/IMG.jpg")
    assert r["label"] == "aot_owned_blog"


def test_rights_unknown_path_defaults_to_unverified():
    r = pts.infer_usage_rights("Camera Uploads/2024-06-15/IMG_9999.heic")
    assert r["label"] == "unverified"
    assert r["method"] == "default"


def test_rights_external_download_hint_flagged():
    r = pts.infer_usage_rights("Dropbox backup 7.24.18/facebook_archive/image.jpg")
    assert r["label"] == "external_download"


# ── Quality rating ──────────────────────────────────────────────


def test_quality_hero_for_large_photo_with_exif():
    item = _item(rel_path="foo/IMG.jpg", size_bytes=5_000_000,
                 exif={"DateTimeOriginal": "2024:01:01 12:00:00", "Make": "Sony"})
    q = pts.rate_quality(item)
    assert q["rating"] == "hero"
    assert "exif present" in q["reasons"]


def test_quality_good_midrange():
    item = _item(rel_path="foo/IMG.jpg", size_bytes=1_500_000, exif=None)
    q = pts.rate_quality(item)
    assert q["rating"] == "good"


def test_quality_low_below_threshold():
    item = _item(rel_path="foo/IMG.jpg", size_bytes=20_000, exif=None)
    q = pts.rate_quality(item)
    assert q["rating"] == "low"


def test_quality_logo_filename_heuristic():
    # Even a huge logo file is marked logo, not hero
    item = _item(rel_path="Active Oahu's shared workspace/Tour & Rental Package Images/Logo Large-01.jpg",
                 size_bytes=10_000_000, exif={"Make": "Sony"})
    q = pts.rate_quality(item)
    assert q["rating"] == "logo"
    assert q["method"] == "logo-heuristic"


def test_quality_video_size_only():
    item = _item(rel_path="vids/clip.mp4", kind="video", size_bytes=200_000_000, exif=None)
    q = pts.rate_quality(item)
    assert q["rating"] == "hero"
    assert q["method"] == "size-only"


def test_quality_unknown_when_no_size():
    item = _item(rel_path="foo/IMG.jpg", size_bytes=0, exif=None)
    q = pts.rate_quality(item)
    assert q["rating"] == "unknown"


def test_quality_zero_size_no_crash():
    item = _item(rel_path="foo/IMG.jpg", size_bytes=0, exif=None)
    q = pts.rate_quality(item)
    assert "rating" in q


# ── Index builder ───────────────────────────────────────────────


def test_build_preserves_all_upstream_fields():
    items = [
        _item(rel_path="foo.jpg", size_bytes=2_000_000,
              activities=["kayaking"], locations=["lanikai"]),
    ]
    inv = _inventory(items)
    enriched = pts.build_tagging_index(inv)
    assert enriched["total_items"] == 1
    e = enriched["items"][0]
    # upstream fields present
    assert e["rel_path"] == "foo.jpg"
    assert e["size_bytes"] == 2_000_000
    assert e["tags"]["activities"] == ["kayaking"]
    # new fields present
    assert "usage_rights" in e
    assert "quality" in e


def test_build_facets_count_correctly():
    items = [
        _item(rel_path="Active Oahu's shared workspace/Profile Pics/a.jpg"),
        _item(rel_path="Active Oahu's shared workspace/Profile Pics/b.jpg"),
        _item(rel_path="Active Oahu's shared workspace/_All Tour & Rental Photos/c.jpg"),
    ]
    enriched = pts.build_tagging_index(_inventory(items))
    assert enriched["facets"]["rights"]["portrait_subject_consent_required"] == 2
    assert enriched["facets"]["rights"]["aot_owned"] == 1


def test_build_records_upstream_metadata():
    items = [_item(rel_path="foo.jpg")]
    inv = _inventory(items)
    enriched = pts.build_tagging_index(inv)
    assert enriched["upstream_issue"] == "GRO-570"
    assert enriched["issue"] == "GRO-571"
    assert enriched["source_inventory"] == inv["generated_at_iso"]
    assert enriched["schema_version"] == pts.SCHEMA_VERSION


# ── Query ───────────────────────────────────────────────────────


def test_query_any_match_activities():
    items = [
        _item(rel_path="a.jpg", activities=["kayaking"]),
        _item(rel_path="b.jpg", activities=["snorkeling"]),
        _item(rel_path="c.jpg", activities=["kayaking", "snorkeling"]),
    ]
    enriched = pts.build_tagging_index(_inventory(items))
    out = pts.query_items(enriched, activities=["kayaking"])
    paths = [i["rel_path"] for i in out]
    assert paths == ["a.jpg", "c.jpg"]


def test_query_filter_combined_facets():
    items = [
        _item(rel_path="kailua_kayak.jpg", activities=["kayaking"], locations=["kailua"]),
        _item(rel_path="waikiki_kayak.jpg", activities=["kayaking"], locations=["waikiki"]),
        _item(rel_path="kailua_snorkel.jpg", activities=["snorkeling"], locations=["kailua"]),
    ]
    enriched = pts.build_tagging_index(_inventory(items))
    out = pts.query_items(enriched, activities=["kayaking"], locations=["kailua"])
    paths = [i["rel_path"] for i in out]
    assert paths == ["kailua_kayak.jpg"]


def test_query_year_filter_uses_captured_then_mtime():
    items = [
        _item(rel_path="a.jpg", captured_at_iso="2024-06-15T12:00:00", mtime_iso="2020-01-01T00:00:00"),
        _item(rel_path="b.jpg", captured_at_iso=None, mtime_iso="2024-07-01T00:00:00"),
        _item(rel_path="c.jpg", captured_at_iso="2023-12-31T00:00:00", mtime_iso="2024-01-01T00:00:00"),
    ]
    enriched = pts.build_tagging_index(_inventory(items))
    out = pts.query_items(enriched, year=2024)
    paths = sorted(i["rel_path"] for i in out)
    assert paths == ["a.jpg", "b.jpg"]


def test_query_limit_and_offset():
    items = [_item(rel_path=f"f{i}.jpg") for i in range(10)]
    enriched = pts.build_tagging_index(_inventory(items))
    out = pts.query_items(enriched, limit=3, offset=2)
    assert len(out) == 3
    assert out[0]["rel_path"] == "f2.jpg"


def test_query_summary_breakdowns():
    items = [
        _item(rel_path="Active Oahu's shared workspace/Profile Pics/a.jpg", size_bytes=5_000_000),
        _item(rel_path="Active Oahu's shared workspace/Profile Pics/b.jpg", size_bytes=20_000),
    ]
    enriched = pts.build_tagging_index(_inventory(items))
    out = pts.query_items(enriched, rights=["portrait_subject_consent_required"])
    summary = pts.summarize_query(enriched, out)
    assert summary["matched"] == 2
    assert summary["total"] == 2
    assert summary["rights_breakdown"]["portrait_subject_consent_required"] == 2
    assert summary["quality_breakdown"]["hero"] == 1
    assert summary["quality_breakdown"]["low"] == 1


# ── CLI smoke ───────────────────────────────────────────────────


def test_cli_tag_dry_run_does_not_write(tmp_path):
    inv_path = tmp_path / "inv.json"
    out_path = tmp_path / "out.json"
    inv_path.write_text(json.dumps(_inventory([
        _item(rel_path="Active Oahu's shared workspace/Profile Pics/a.jpg"),
    ])))
    rc = pts.main([
        "tag", "--inventory", str(inv_path), "--out", str(out_path), "--dry-run",
    ])
    assert rc == 0
    assert not out_path.exists()


def test_cli_tag_writes_enriched_index(tmp_path):
    inv_path = tmp_path / "inv.json"
    out_path = tmp_path / "out.json"
    inv_path.write_text(json.dumps(_inventory([
        _item(rel_path="Active Oahu's shared workspace/_All Tour & Rental Photos/a.jpg",
              size_bytes=5_000_000),
    ])))
    rc = pts.main([
        "tag", "--inventory", str(inv_path), "--out", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert data["issue"] == "GRO-571"
    assert data["items"][0]["usage_rights"]["label"] == "aot_owned"
    assert data["items"][0]["quality"]["rating"] == "hero"


def test_cli_query_paths_only(tmp_path):
    inv_path = tmp_path / "inv.json"
    out_path = tmp_path / "out.json"
    inv_path.write_text(json.dumps(_inventory([
        _item(rel_path="a.jpg", activities=["kayaking"], locations=["kailua"]),
        _item(rel_path="b.jpg", activities=["snorkeling"], locations=["lanikai"]),
    ])))
    pts.main(["tag", "--inventory", str(inv_path), "--out", str(out_path)])
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(io.StringIO()):
        rc = pts.main([
            "query", "--index", str(out_path),
            "--activity", "kayaking", "--paths-only",
        ])
    assert rc == 0
    assert buf.getvalue().strip() == "a.jpg"


def test_cli_query_missing_index_returns_2(tmp_path):
    rc = pts.main([
        "query", "--index", str(tmp_path / "missing.json"),
        "--activity", "kayaking",
    ])
    assert rc == 2


def test_cli_tag_missing_inventory_returns_2(tmp_path):
    rc = pts.main([
        "tag", "--inventory", str(tmp_path / "missing.json"),
        "--out", str(tmp_path / "out.json"),
    ])
    assert rc == 2