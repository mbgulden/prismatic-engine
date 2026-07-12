from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_fixture_site(root: Path) -> Path:
    site = root / "site"
    (site / "kayak").mkdir(parents=True)
    (site / "orphan").mkdir(parents=True)
    (site / "index.html").write_text(
        """
        <html><head><title>Home</title><meta name="description" content="Tours"><script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite","name":"AOT"}</script></head>
        <body><h1>Active Oahu Tours</h1><a href="/kayak/">Kayak</a><a href="/missing/">Missing</a></body></html>
        """,
        encoding="utf-8",
    )
    (site / "kayak" / "index.html").write_text(
        """
        <html><head><title>Kailua Kayak Rentals</title><meta name="description" content="Kayaks"><script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Kayak Rental"}</script></head>
        <body><h1>Kailua Kayak Rentals</h1><a href="/">Home</a></body></html>
        """,
        encoding="utf-8",
    )
    (site / "orphan" / "index.html").write_text(
        "<html><head><title>Orphan</title></head><body><h1>Orphan Page</h1></body></html>",
        encoding="utf-8",
    )
    return site


def run_script(script: str, tmp_path: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PRISMATIC_STATE_DIR": str(tmp_path / "state"), **(extra_env or {})}
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "seo" / script)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )


def test_internal_link_and_schema_audits_write_artifacts(tmp_path: Path) -> None:
    site = write_fixture_site(tmp_path)
    env = {"AOT_SITE_DIR": str(site)}

    link = run_script("internal_link_orphan_audit.py", tmp_path, env)
    assert link.returncode == 0, link.stderr + link.stdout
    graph = json.loads((tmp_path / "state" / "seo" / "internal-links" / "latest_internal_link_graph.json").read_text())
    assert graph["total_pages"] == 3
    assert "/orphan/" in graph["orphans"]
    assert {"source": "/", "target": "/missing/"} in graph["broken_internal_links"]

    schema = run_script("structured_data_drift_audit.py", tmp_path, env)
    assert schema.returncode == 0, schema.stderr + schema.stdout
    drift = json.loads((tmp_path / "state" / "seo" / "schema-drift" / "latest_schema_drift.json").read_text())
    assert drift["schema_type_counts"]["WebSite"] == 1
    assert drift["schema_type_counts"]["Product"] == 1
    assert "/orphan/" in drift["pages_without_schema"]


def test_counter_content_uses_seeded_gsc_and_velocity(tmp_path: Path) -> None:
    gsc_dir = tmp_path / "state" / "seo" / "gsc-export"
    seo_dir = tmp_path / "state" / "seo"
    gsc_dir.mkdir(parents=True)
    seo_dir.mkdir(parents=True, exist_ok=True)
    (gsc_dir / "latest_gsc_query_page.json").write_text(json.dumps({
        "start_date": "2026-01-01",
        "end_date": "2026-04-01",
        "rows": [
            {"keys": ["kailua kayak rental", "https://activeoahutours.com/kayak/"], "clicks": 2, "impressions": 200, "position": 9.0},
            {"keys": ["lanikai snorkel gear", "https://activeoahutours.com/snorkel/"], "clicks": 0, "impressions": 120, "position": 14.0},
        ],
    }))
    (seo_dir / "competitor_baseline.json").write_text(json.dumps({
        "kailuabeachadventures.com": {
            "https://kailuabeachadventures.com/kayak-rentals/": {"title": "Kailua Kayak Rentals", "traffic": 50, "keyword": "kailua kayak rental"},
            "https://kailuabeachadventures.com/lanikai-snorkel/": {"title": "Lanikai Snorkel Gear", "traffic": 25, "keyword": "lanikai snorkel gear"},
        },
        "_meta": {"last_updated": "now"},
    }))

    result = run_script("gsc_ubersuggest_countercontent.py", tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    briefs = json.loads((tmp_path / "state" / "seo" / "counter-content" / "latest_counter_content_briefs.json").read_text())
    assert briefs["brief_count"] >= 2
    topics = {brief["topic"] for brief in briefs["briefs"]}
    assert "kailua" in topics or "kayak" in topics


def test_lighthouse_monitor_static_fallback_writes_artifact_when_lighthouse_missing(tmp_path: Path) -> None:
    site = write_fixture_site(tmp_path)
    env = {"AOT_SITE_DIR": str(site), "PATH": "/usr/bin:/bin"}
    result = run_script("lighthouse_seo_a11y_monitor.py", tmp_path, env)
    assert result.returncode in {0, 1}, result.stderr + result.stdout
    latest = tmp_path / "state" / "seo" / "lighthouse-monitor" / "latest_lighthouse_monitor.json"
    data = json.loads(latest.read_text())
    assert data["mode"] in {"lighthouse", "static_fallback"}
    if data["mode"] == "static_fallback":
        assert "pages" in data
        assert isinstance(data["pages"], list)
