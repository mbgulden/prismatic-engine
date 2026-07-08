from __future__ import annotations

import importlib.util
from pathlib import Path


def load_detector():
    script = Path(__file__).resolve().parents[2] / "scripts" / "silent_cron_detector.py"
    spec = importlib.util.spec_from_file_location("silent_cron_detector", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_paused_and_retired_cron_errors_are_suppressed_from_current_failures() -> None:
    detector = load_detector()
    jobs = [
        {
            "job_id": "active-error",
            "name": "Active failing cron",
            "enabled": True,
            "last_status": "error",
            "deliver": "local",
        },
        {
            "job_id": "paused-error",
            "name": "Paused old failure",
            "enabled": True,
            "paused": True,
            "last_status": "error",
            "deliver": "local",
        },
        {
            "job_id": "retired-error",
            "name": "Retired old failure",
            "enabled": True,
            "state": "retired",
            "last_status": "error",
            "deliver": "local",
        },
        {
            "job_id": "disabled-never-ran",
            "name": "Disabled blank status",
            "enabled": False,
            "last_status": None,
            "deliver": "local",
        },
    ]

    report = detector.detect_silent_failures(jobs, stale_hours=24)

    assert [j["job_id"] for j in report["silent_failures"]] == ["active-error"]
    assert report["stale"] == []
    assert report["blank_status"] == []
    assert {j["job_id"] for j in report["archived_suppressed"]} == {
        "paused-error",
        "retired-error",
        "disabled-never-ran",
    }
    assert report["active_total"] == 1


def test_digest_names_suppressed_archival_jobs_without_escalating_them() -> None:
    detector = load_detector()
    report = detector.detect_silent_failures(
        [
            {
                "job_id": "paused-error",
                "name": "Paused old failure",
                "enabled": True,
                "paused": True,
                "last_status": "error",
                "deliver": "local",
            }
        ],
        stale_hours=24,
    )

    digest = detector.build_digest(report, stale_hours=24)

    assert "## 🔴 Silent Failures" in digest
    assert "_None detected" in digest
    assert "Archived/Paused Suppressed: 1 jobs" in digest
    assert "Paused old failure" in digest
    assert "not a current alert" in digest
    assert "**Active jobs:** 0" in digest
