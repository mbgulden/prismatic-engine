from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from prismatic.vertex_telemetry import (
    VertexQuotaError,
    VertexBillingLedger,
    normalize_quota_payload,
    poll_vertex_quota_status,
)


def _contains_nullish(value):
    if value is None:
        return True
    if isinstance(value, str) and value.lower() in {
        "undefined",
        "null",
        "none",
        "nan",
        "",
    }:
        return True
    if isinstance(value, dict):
        return any(_contains_nullish(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_nullish(v) for v in value)
    return False


def test_normalize_quota_payload_strips_nullish_and_adds_freshness():
    record = normalize_quota_payload(
        {
            "quotaId": "aiplatform.googleapis.com/gemini_predictions_per_minute_tokens_per_base_model",
            "metricName": "tokens per minute",
            "dimensions": {
                "region": "us-central1",
                "base_model": "gemini-2.5-pro",
                "empty": None,
                "bad": "undefined",
            },
            "metricInfos": [{"metricValue": "120", "ignored": None}],
            "limits": [{"maxLimit": {"value": "240"}, "display": "undefined"}],
            "null_field": None,
        },
        "us-east4",
    )

    assert record is not None
    assert record["region"] == "us-central1"
    assert record["model"] == "gemini-2.5-pro"
    assert record["metric_type"] == "tpm"
    assert record["usage"] == 120.0
    assert record["limit_value"] == 240.0
    assert record["remaining_value"] == 120.0
    assert "unavailable_reason" not in record
    assert record["utilization_pct"] == 50.0
    datetime.fromisoformat(record["recorded_at"])
    assert not _contains_nullish(record)
    assert "null_field" not in json.dumps(record["raw_payload"])


def test_normalize_quota_payload_explains_unavailable_remaining_value():
    record = normalize_quota_payload(
        {
            "quotaId": "aiplatform.googleapis.com/gemini_predictions_per_minute_requests_per_base_model",
            "dimensions": {"base_model": "gemini-2.5-flash"},
            "metricInfos": [{"metricValue": "3"}],
            "limits": [],
        },
        "us-central1",
    )

    assert record is not None
    assert record["limit_value"] == 0.0
    assert "remaining_value" not in record
    assert (
        record["unavailable_reason"]
        == "quota limit unavailable from Cloud Quotas payload"
    )
    assert not _contains_nullish(record)


def test_poll_vertex_quota_status_returns_explicit_errors(monkeypatch):
    def fake_call(url: str):
        if "us-central1" in url:
            raise VertexQuotaError("boom")
        return {
            "quotas": [
                {
                    "quotaId": "aiplatform.googleapis.com/gemini_predictions_per_minute_requests_per_base_model",
                    "dimensions": {
                        "region": "us-east4",
                        "base_model": "gemini-2.5-flash",
                    },
                    "metricInfos": [{"metricValue": 5}],
                    "limits": [{"maxLimit": {"value": 10}}],
                }
            ]
        }

    monkeypatch.setattr("prismatic.vertex_telemetry._gcp_api_call", fake_call)
    status = poll_vertex_quota_status(
        project_id="project-x", locations=["us-central1", "us-east4"]
    )

    assert len(status["records"]) == 1
    assert status["records"][0]["model"] == "gemini-2.5-flash"
    assert status["records"][0]["remaining_value"] == 5.0
    assert len(status["errors"]) == 1
    assert status["errors"][0]["location"] == "us-central1"
    assert status["errors"][0]["error_type"] == "VertexQuotaError"
    assert "boom" in status["errors"][0]["error_message"]


def test_ledger_status_exposes_freshness_errors_and_metrics(tmp_path: Path):
    db_path = tmp_path / "event_router.db"
    ledger = VertexBillingLedger(str(db_path))
    recorded_at = datetime.now(timezone.utc).isoformat()
    ledger.record_quota_snapshot(
        [
            {
                "region": "us-east4",
                "model": "gemini-2.5-pro",
                "metric_type": "rpm",
                "metric_name": "requests",
                "usage": "4",
                "limit_value": "8",
                "utilization_pct": "50",
                "recorded_at": recorded_at,
                "raw_payload": {"keep": "yes", "drop": None},
            }
        ],
        project_id="project-x",
    )
    ledger.record_quota_errors(
        [
            {
                "location": "us-central1",
                "source": "quota",
                "error_type": "VertexQuotaError",
                "error_message": "temporary upstream failure",
                "recorded_at": recorded_at,
                "raw_payload": {"bad": "undefined", "ok": "kept"},
            }
        ]
    )

    summary = ledger.get_status_summary()
    assert summary["generated_at"]
    assert summary["quota_freshness"]["last_recorded_at"] == recorded_at
    assert summary["quota_freshness"]["age_seconds"] >= 0
    assert summary["quota_freshness"]["stale"] is False
    assert summary["latest_errors"][0]["error_message"] == "temporary upstream failure"
    assert summary["quota_records"][0]["utilization_pct"] == 50.0
    assert summary["quota_records"][0]["remaining_value"] == 4.0

    metrics = ledger.metrics_text()
    assert "prismatic_vertex_quota_last_recorded_timestamp_seconds" in metrics
    assert "prismatic_vertex_quota_poll_errors_total 1" in metrics

    with sqlite3.connect(db_path) as conn:
        raw_snapshot = conn.execute(
            "SELECT raw_payload FROM gcp_vertex_quota_snapshots"
        ).fetchone()[0]
        raw_error = conn.execute(
            "SELECT raw_payload FROM gcp_vertex_poll_errors"
        ).fetchone()[0]
    assert json.loads(raw_snapshot) == {"keep": "yes"}
    assert json.loads(raw_error) == {"ok": "kept"}
