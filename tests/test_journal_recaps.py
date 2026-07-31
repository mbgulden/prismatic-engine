"""Evidence-cited deterministic recap coverage."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from prismatic.journal import (
    JournalConfig,
    MAX_RECAP_BYTES,
    MAX_RECAP_EVENTS,
    MAX_RECAP_MANIFEST_BYTES,
    build_evidence_recap,
    generate_recap,
    recap_window,
)


def config_for(tmp_path: Path) -> JournalConfig:
    research = tmp_path / "Hermes-Research"
    profile = tmp_path / ".hermes" / "profiles" / "orchestrator"
    return JournalConfig(
        workspace=tmp_path,
        harness_profile=profile,
        research_repo=research,
        journal_root=research / "journals",
        report_root=research / "reports",
        doc_root=research / "docs",
        sessions_dir=profile / "sessions",
        cron_jobs=profile / "cron" / "jobs.json",
        project_registry=tmp_path / "project-registry.json",
        team_id="team",
        project_id="project",
        state_todo="todo",
        state_in_progress="progress",
        labels={},
    )


def test_daily_recap_cites_events_and_uses_current_cron_state(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    index = config.journal_root / ".index"
    index.mkdir(parents=True)
    index.joinpath("events-2026-07-23.json").write_text(
        json.dumps(
            [
                {
                    "type": "cron_run",
                    "job_name": "snapshot",
                    "status": "error",
                    "snippet": "token: secret-value",
                    "idempotency_key": "a" * 64,
                    "_timestamp": "2026-07-23T05:00:00Z",
                }
            ]
        )
    )
    config.cron_jobs.parent.mkdir(parents=True)
    config.cron_jobs.write_text(
        json.dumps(
            {"jobs": [{"name": "snapshot", "enabled": True, "last_status": "ok"}]}
        )
    )

    result = generate_recap(
        "daily", config, datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    )
    rendered = Path(result["path"]).read_text()

    assert result["source_event_count"] == 1
    assert result["rendered_claim_count"] == 1
    assert result["artifact_bytes"] < MAX_RECAP_BYTES
    assert result["citation_manifest_bytes"] < MAX_RECAP_MANIFEST_BYTES
    assert "cited_event_ids" not in result
    manifest = json.loads(Path(result["citation_manifest_path"]).read_text())
    assert manifest["cited_event_ids"] == ["a" * 64]
    assert "[E:" + "a" * 64 + "]" in rendered
    assert "current `ok`" in rendered
    assert "secret-value" not in rendered
    assert "[REDACTED]" in rendered


def test_quiet_daily_and_weekly_boundary_are_deterministic(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    now = datetime(2026, 7, 26, 23, 59, tzinfo=timezone.utc)  # Sunday
    weekly_start, weekly_end = recap_window("weekly", now)
    assert weekly_start == datetime(2026, 7, 20, tzinfo=timezone.utc)
    assert weekly_end == now

    result = generate_recap("daily", config, now)
    rendered = Path(result["path"]).read_text()
    assert result["quiet"] is True
    assert "Quiet window" in rendered
    assert "No current scheduler state available" in rendered


def test_recap_claims_are_bounded_but_each_displayed_event_is_cited() -> None:
    now = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    events = [
        {
            "type": "decision",
            "snippet": f"event {index}",
            "idempotency_key": f"{index:064x}",
        }
        for index in range(55)
    ]
    rendered, citations = build_evidence_recap(
        events, "daily", now.replace(hour=0), now, [], max_events=3
    )

    assert len(citations) == 3
    assert "Showing the latest 3 cited events of 55 accepted events" in rendered
    assert "event 54" in rendered
    assert "event 0" not in rendered
    assert rendered.count("[E:") == 3


def test_generated_recap_uses_compact_result_and_bounded_artifact(
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path)
    index = config.journal_root / ".index"
    index.mkdir(parents=True)
    events = [
        {
            "type": "decision",
            "snippet": f"event {index}",
            "idempotency_key": f"{index:064x}",
            "_timestamp": f"2026-07-23T{index % 12:02d}:00:00Z",
        }
        for index in range(55)
    ]
    index.joinpath("events-2026-07-23.json").write_text(json.dumps(events))

    result = generate_recap(
        "daily", config, datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    )
    rendered = Path(result["path"]).read_text()
    assert result["source_event_count"] == 55
    assert result["rendered_claim_count"] == 50
    assert result["artifact_bytes"] == len(rendered.encode()) < 32_768
    assert "cited_event_ids" not in result
    assert rendered.count("[E:") == 50


@pytest.mark.parametrize("invalid_limit", [0, -1, 51, True, 1.5])
def test_recap_limit_cannot_disable_or_exceed_the_global_cap(invalid_limit) -> None:
    now = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    with pytest.raises((TypeError, ValueError)):
        build_evidence_recap([], "daily", now.replace(hour=0), now, [], max_events=invalid_limit)


def test_malformed_unbounded_event_id_is_replaced_by_bounded_digest(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    index = config.journal_root / ".index"
    index.mkdir(parents=True)
    index.joinpath("events-2026-07-23.json").write_text(json.dumps([{
        "type": "decision",
        "snippet": "bounded citation",
        "idempotency_key": "x" * 100_000,
        "_timestamp": "2026-07-23T05:00:00Z",
    }]))

    result = generate_recap("daily", config, datetime(2026, 7, 23, 12, tzinfo=timezone.utc))
    manifest = json.loads(Path(result["citation_manifest_path"]).read_text())

    assert len(manifest["cited_event_ids"]) == 1
    assert len(manifest["cited_event_ids"][0]) == 64
    assert manifest["cited_event_ids"][0] != "x" * 64
    assert result["rendered_claim_count"] <= MAX_RECAP_EVENTS
    assert result["citation_manifest_bytes"] < MAX_RECAP_MANIFEST_BYTES


def test_oversized_recap_fails_before_writing_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_for(tmp_path)
    monkeypatch.setattr(
        "prismatic.journal.live_cron_health",
        lambda _config: [
            {"name": "job-" + "x" * 500, "enabled": True, "last_status": "ok"}
            for _ in range(300)
        ],
    )

    with pytest.raises(ValueError, match="recap exceeds"):
        generate_recap("daily", config, datetime(2026, 7, 23, 12, tzinfo=timezone.utc))
    assert not (config.journal_root / "recaps").exists()


def test_recap_window_rejects_naive_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        recap_window("daily", datetime(2026, 7, 23, 0, 30))


def test_generate_recap_sorts_latest_claims_by_utc_timestamp(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    index = config.journal_root / ".index"
    index.mkdir(parents=True)

    def event(number: int) -> dict:
        return {
            "type": "decision",
            "snippet": f"event {number}",
            "idempotency_key": f"{number:064x}",
            "_timestamp": f"2026-07-23T00:{number:02d}:00Z",
        }

    index.joinpath("events-2026-07-23.json").write_text(
        json.dumps([event(59), *[event(number) for number in range(59)]])
    )
    result = generate_recap("daily", config, datetime(2026, 7, 23, 1, tzinfo=timezone.utc))
    rendered = Path(result["path"]).read_text()

    assert f"[E:{10:064x}]" in rendered
    assert f"[E:{59:064x}]" in rendered
    assert f"[E:{9:064x}]" not in rendered


def test_distinct_colliding_prefixes_render_unambiguous_full_ids() -> None:
    start = datetime(2026, 7, 23, tzinfo=timezone.utc)
    end = datetime(2026, 7, 23, 1, tzinfo=timezone.utc)
    first = "a" * 12 + "1" * 52
    second = "a" * 12 + "2" * 52
    rendered, cited = build_evidence_recap(
        [
            {"type": "decision", "snippet": "first", "idempotency_key": first},
            {"type": "decision", "snippet": "second", "idempotency_key": second},
        ],
        "daily",
        start,
        end,
        [],
    )

    assert cited == [first, second]
    assert f"[E:{first}]" in rendered
    assert f"[E:{second}]" in rendered
    assert "[E:aaaaaaaaaaaa]" not in rendered


def test_all_dynamic_rendered_fields_are_redacted_and_bounded() -> None:
    start = datetime(2026, 7, 23, tzinfo=timezone.utc)
    end = datetime(2026, 7, 23, 1, tzinfo=timezone.utc)
    rendered, _ = build_evidence_recap(
        [{"type": "password: type-leak", "snippet": "token: detail-leak", "idempotency_key": "a" * 64}],
        "daily",
        start,
        end,
        [{"name": "token: health-leak", "enabled": True, "last_status": "password: status-leak"}],
    )

    assert "type-leak" not in rendered
    assert "detail-leak" not in rendered
    assert "health-leak" not in rendered
    assert "status-leak" not in rendered
    assert rendered.count("[REDACTED]") == 4


def test_pair_replace_failure_restores_previous_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_for(tmp_path)
    index = config.journal_root / ".index"
    index.mkdir(parents=True)
    source = index / "events-2026-07-23.json"
    source.write_text(json.dumps([{"type": "decision", "snippet": "old", "idempotency_key": "a" * 64, "_timestamp": "2026-07-23T05:00:00Z"}]))
    now = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    initial = generate_recap("daily", config, now)
    target = Path(initial["path"])
    manifest = Path(initial["citation_manifest_path"])
    old_target = target.read_bytes()
    old_manifest = manifest.read_bytes()
    source.write_text(json.dumps([{"type": "decision", "snippet": "new", "idempotency_key": "b" * 64, "_timestamp": "2026-07-23T06:00:00Z"}]))
    real_replace = os.replace

    def fail_manifest_install(src, dst) -> None:
        if Path(dst) == manifest and ".stage-" in Path(src).name:
            raise OSError("injected manifest install failure")
        real_replace(src, dst)

    monkeypatch.setattr("prismatic.journal.os.replace", fail_manifest_install)
    with pytest.raises(OSError, match="injected"):
        generate_recap("daily", config, now)

    assert target.read_bytes() == old_target
    assert manifest.read_bytes() == old_manifest
    assert not list(target.parent.glob(".*.stage-*"))
    assert not list(target.parent.glob(".*.backup-*"))
