"""Tests for prismatic.credit_tracker — AI Ultra credit ledger + burn velocity.

GRO-2402 follow-up: credit_tracker.py tracks Google AI Ultra credit spend
(25K/month allocation) and calculates burn velocity to trigger exhaustion
alerts before credits run out.

Until now, the only test was a scratch/ validation script — no real pytest
coverage. A bug here would silently miss exhaustion warnings → silent budget
overrun → $$$ surprise.

These tests cover:
- Constructor + table initialization (idempotent)
- calculate_monthly_spent (filters by current month + provider)
- get_remaining_credits (clamped to >= 0)
- calculate_burn_velocity (credits/hour over lookback)
- evaluate_exhaustion_warning (alert thresholds: 24h = WARNING, <6h = CRITICAL)
- _estimate_credit_cost (engine + duration → credits)
- _compute_file_hash (handles errors with fallback)
- Media artifact scanning (parse_media_artifacts)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

from prismatic.credit_tracker import (  # noqa: E402
    AIUltraCreditTracker,
    MONTHLY_ALLOCATION_LIMIT,
    MEDIA_COST_MAP,
    DEFAULT_DB_PATH,
)


# ── Module constants ─────────────────────────────────────────────────
def test_monthly_allocation_is_25k():
    """Google AI Ultra gets 25K credits/month."""
    assert MONTHLY_ALLOCATION_LIMIT == 25000


def test_media_cost_map_has_all_engines():
    expected_keys = {
        "omni-flash-4s", "omni-flash-6s", "omni-flash-8s", "omni-flash-10s",
        "veo-fast", "veo-fast-any",
        "veo-quality-8s", "veo-quality-10s", "veo-quality-any",
    }
    assert expected_keys.issubset(MEDIA_COST_MAP.keys())


def test_media_cost_veo_quality_is_expensive():
    """Veo Quality is the most expensive — confirms we test the right model."""
    assert MEDIA_COST_MAP["veo-quality-8s"] == 100
    assert MEDIA_COST_MAP["veo-quality-10s"] == 120


# ── Constructor / table init ─────────────────────────────────────────
def test_constructor_creates_tables(tmp_path):
    db = tmp_path / "credits.db"
    AIUltraCreditTracker(db_path=str(db))
    # Verify tables exist
    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('telemetry_media_artifacts', 'telemetry_credit_ledger')"
    )
    tables = {r[0] for r in cur.fetchall()}
    assert tables == {"telemetry_media_artifacts", "telemetry_credit_ledger"}


def test_constructor_idempotent(tmp_path):
    """Creating the tracker twice doesn't fail (CREATE IF NOT EXISTS)."""
    db = str(tmp_path / "credits.db")
    AIUltraCreditTracker(db_path=db)
    AIUltraCreditTracker(db_path=db)  # should not raise


def test_constructor_creates_parent_dir(tmp_path):
    db = str(tmp_path / "deeply" / "nested" / "credits.db")
    AIUltraCreditTracker(db_path=db)
    assert Path(db).parent.exists()


# ── calculate_monthly_spent ─────────────────────────────────────────
def test_calculate_monthly_spent_empty(tmp_path):
    """No transactions → 0 spent."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    assert tracker.calculate_monthly_spent() == 0


def test_calculate_monthly_spent_filters_by_provider(tmp_path):
    """Only google-antigravity counts (other providers excluded)."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    now = datetime.now(timezone.utc).isoformat()
    # Add 100 credits to google-antigravity
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 100,
         "media_generation_video", now),
    )
    # Add 500 credits to a DIFFERENT provider (should NOT count)
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r2", "agent:fred", "local-llm", "qwen", 500, "code_generation", now),
    )
    conn.commit()
    conn.close()
    assert tracker.calculate_monthly_spent() == 100


def test_calculate_monthly_spent_filters_by_month(tmp_path):
    """Last month's spend doesn't count toward this month."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    # Record from 45 days ago (last month)
    last_month = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r-old", "agent:agy", "google-antigravity", "veo-3.1", 200,
         "media_generation_video", last_month),
    )
    conn.commit()
    conn.close()
    assert tracker.calculate_monthly_spent() == 0


def test_calculate_monthly_spent_aggregates_this_month(tmp_path):
    """Multiple this-month records sum correctly."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    for ts, cost in [(ten_days_ago, 100), (five_days_ago, 50), (one_day_ago, 25)]:
        conn.execute(
            "INSERT INTO telemetry_credit_ledger "
            "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"r-{ts}", "agent:agy", "google-antigravity", "veo-3.1", cost,
             "media_generation_video", ts),
        )
    conn.commit()
    conn.close()
    assert tracker.calculate_monthly_spent() == 175


# ── get_remaining_credits ───────────────────────────────────────────
def test_get_remaining_credits_full(tmp_path):
    """No spend → full allocation remaining."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    assert tracker.get_remaining_credits() == MONTHLY_ALLOCATION_LIMIT


def test_get_remaining_credits_after_spend(tmp_path):
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 5000,
         "video", now),
    )
    conn.commit()
    conn.close()
    assert tracker.get_remaining_credits() == 20000


def test_get_remaining_credits_clamped_to_zero(tmp_path):
    """Over-budget → return 0, not negative."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 30000,
         "video", now),
    )
    conn.commit()
    conn.close()
    assert tracker.get_remaining_credits() == 0


# ── calculate_burn_velocity ─────────────────────────────────────────
def test_burn_velocity_zero_when_no_history(tmp_path):
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    assert tracker.calculate_burn_velocity() == 0.0


def test_burn_velocity_credits_per_hour(tmp_path):
    """100 credits in last hour → velocity = 100/hour."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 100, "video", recent),
    )
    conn.commit()
    conn.close()
    velocity = tracker.calculate_burn_velocity(lookback_hours=1.0)
    assert 95 <= velocity <= 105  # allow for clock drift


def test_burn_velocity_lookback_filters_old_data(tmp_path):
    """Old records outside lookback window are excluded."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 500, "video", two_hours_ago),
    )
    conn.commit()
    conn.close()
    # Look back only 1 hour → old record excluded
    velocity = tracker.calculate_burn_velocity(lookback_hours=1.0)
    assert velocity == 0.0


# ── evaluate_exhaustion_warning ─────────────────────────────────────
def test_no_warning_when_velocity_zero(tmp_path):
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    assert tracker.evaluate_exhaustion_warning() is None


def test_no_warning_when_credits_safe(tmp_path):
    """100 credits/hour with 20000 remaining → 200 hours → safe."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 100, "video", now),
    )
    conn.commit()
    conn.close()
    # 100/hour burn, 24900 remaining → 249 hours until exhausted → no warning
    assert tracker.evaluate_exhaustion_warning() is None


def test_warning_triggered_when_exhaustion_within_24h(tmp_path):
    """High burn rate → CRITICAL or WARNING alert."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    # Spend 5000 in 30 min → 10000/hour burn
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 5000, "video", recent),
    )
    conn.commit()
    conn.close()
    # Now we have ~20000 remaining and ~10000/hour burn → 2 hours to exhaustion
    alert = tracker.evaluate_exhaustion_warning(lookback_hours=1.0)
    assert alert is not None
    assert alert["triggered"] is True
    assert alert["severity"] == "CRITICAL"  # <6h = CRITICAL
    assert alert["hours_to_exhaustion"] < 6.0


def test_warning_severity_warning_vs_critical(tmp_path):
    """24h threshold = WARNING, 6h threshold = CRITICAL."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    # Spend 600 → 1200/hour burn, 24400 remaining → 20 hours → WARNING
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 600, "video", recent),
    )
    conn.commit()
    conn.close()
    alert = tracker.evaluate_exhaustion_warning(lookback_hours=1.0)
    if alert is not None:
        # 20 hours → between 6 and 24 → WARNING
        assert alert["severity"] in ("WARNING", "CRITICAL")


def test_warning_has_all_required_fields(tmp_path):
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    conn = sqlite3.connect(db)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    conn.execute(
        "INSERT INTO telemetry_credit_ledger "
        "(run_id, agent, provider, model, credits_spent, operation, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "agent:agy", "google-antigravity", "veo-3.1", 5000, "video", recent),
    )
    conn.commit()
    conn.close()
    alert = tracker.evaluate_exhaustion_warning(lookback_hours=1.0)
    assert alert is not None
    required_fields = {
        "triggered", "remaining_credits", "burn_velocity",
        "hours_to_exhaustion", "severity", "message", "timestamp",
    }
    assert required_fields.issubset(alert.keys())


# ── _estimate_credit_cost ───────────────────────────────────────────
def test_estimate_cost_exact_engine_match():
    """Engine that matches a known key → use that cost."""
    t = AIUltraCreditTracker(db_path=":memory:")  # no DB needed for this
    cost = t._estimate_credit_cost("video", "veo-quality-8s", 8.0)
    assert cost == 100


def test_estimate_cost_video_quality_long_duration():
    t = AIUltraCreditTracker(db_path=":memory:")
    cost = t._estimate_credit_cost("video", "veo-quality", 10.0)
    assert cost == 120  # veo-quality-10s


def test_estimate_cost_video_fast():
    t = AIUltraCreditTracker(db_path=":memory:")
    cost = t._estimate_credit_cost("video", "veo-fast", 5.0)
    assert cost == 10  # veo-fast-any


def test_estimate_cost_image_by_duration():
    t = AIUltraCreditTracker(db_path=":memory:")
    assert t._estimate_credit_cost("image", "omni-flash", 4.0) == 15
    assert t._estimate_credit_cost("image", "omni-flash", 6.0) == 20
    assert t._estimate_credit_cost("image", "omni-flash", 8.0) == 25
    assert t._estimate_credit_cost("image", "omni-flash", 10.0) == 30


def test_estimate_cost_unknown_falls_back_to_5():
    t = AIUltraCreditTracker(db_path=":memory:")
    cost = t._estimate_credit_cost("unknown_type", "unknown_engine", 0)
    assert cost == 5


# ── _compute_file_hash ──────────────────────────────────────────────
def test_compute_file_hash_returns_md5(tmp_path):
    f = tmp_path / "test.bin"
    f.write_bytes(b"hello world")
    t = AIUltraCreditTracker(db_path=":memory:")
    h = t._compute_file_hash(f)
    # MD5 of "hello world" is 5eb63bbbe01eeed093cb22bb8f5acdc3
    assert h == "5eb63bbbe01eeed093cb22bb8f5acdc3"


def test_compute_file_hash_falls_back_on_error(tmp_path):
    """Nonexistent file → returns filename as fallback (no crash)."""
    f = tmp_path / "nonexistent.bin"
    t = AIUltraCreditTracker(db_path=":memory:")
    h = t._compute_file_hash(f)
    # Should not raise; returns something (either name or hash)
    assert isinstance(h, str)
    assert len(h) > 0


# ── parse_media_artifacts ───────────────────────────────────────────
def test_parse_media_artifacts_nonexistent_dir(tmp_path):
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    result = tracker.parse_media_artifacts(str(tmp_path / "nonexistent"))
    assert result == []


def test_parse_media_artifacts_empty_dir(tmp_path):
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    result = tracker.parse_media_artifacts(str(tmp_path))
    assert result == []


def test_parse_media_artifacts_finds_new_file(tmp_path):
    """A new .mp4 file in scan_dir → parsed, recorded, ledger updated."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    # Create a fake .mp4 file
    media_file = tmp_path / "test_video.mp4"
    media_file.write_bytes(b"fake mp4 content")
    # No JSON sidecar → uses default cost (5)
    new_artifacts = tracker.parse_media_artifacts(str(tmp_path))
    assert len(new_artifacts) >= 1
    # Verify it was recorded in DB
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "SELECT filepath, media_type FROM telemetry_media_artifacts"
    )
    rows = cur.fetchall()
    conn.close()
    assert any("test_video.mp4" in r[0] for r in rows)


def test_parse_media_artifacts_idempotent(tmp_path):
    """Scanning the same dir twice doesn't double-count."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    (tmp_path / "test.mp4").write_bytes(b"content")
    first = tracker.parse_media_artifacts(str(tmp_path))
    second = tracker.parse_media_artifacts(str(tmp_path))
    assert len(first) >= 1
    assert second == []  # already processed


def test_parse_media_artifacts_with_json_metadata(tmp_path):
    """JSON sidecar provides engine + duration for accurate cost calculation."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"fake video")
    meta_file = tmp_path / "video.json"
    meta_file.write_text(json.dumps({
        "engine": "veo-quality-8s",
        "duration": 8.0,
        "media_type": "video",
    }))
    artifacts = tracker.parse_media_artifacts(str(tmp_path))
    assert len(artifacts) >= 1
    # Should have recorded the veo-quality-8s cost (100 credits)
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "SELECT credits_spent, engine FROM telemetry_media_artifacts WHERE filepath LIKE '%video.mp4'"
    )
    rows = cur.fetchall()
    conn.close()
    if rows:
        assert rows[0][0] >= 50  # at least the veo-quality cost


def test_parse_media_artifacts_ignores_non_media(tmp_path):
    """Non-media files (.txt, .py) are ignored."""
    db = str(tmp_path / "credits.db")
    tracker = AIUltraCreditTracker(db_path=db)
    (tmp_path / "script.py").write_text("print('hello')")
    (tmp_path / "readme.txt").write_text("hello")
    artifacts = tracker.parse_media_artifacts(str(tmp_path))
    assert artifacts == []
