from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path


def setup_runtime(tmp_path: Path, monkeypatch, *, disabled: str = ""):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "PRISMATIC_LAUNCH_RECORDS_DB_PATH", str(tmp_path / "event_router.db")
    )
    monkeypatch.setenv(
        "PRISMATIC_LINEAR_RATE_LIMIT_STATE",
        str(tmp_path / "linear_rate_limit_state.json"),
    )
    monkeypatch.setenv("PRISMATIC_ASSIGNED_AGENT_DRY_RUN", "1")
    monkeypatch.setenv("PRISMATIC_ENABLED_AGENTS", "kai,fred,agy,george")
    monkeypatch.setenv("PRISMATIC_DISABLED_AGENTS", disabled)
    import prismatic.ingestion_queue as q
    import prismatic.dispatcher as dispatcher

    q = importlib.reload(q)
    dispatcher = importlib.reload(dispatcher)
    q.ensure_queue_db()
    return q, dispatcher


def payload(identifier: str, *agents: str, event_id: str | None = None) -> dict:
    labels = [{"name": f"agent:{agent}"} for agent in agents]
    return {
        "id": event_id or f"evt-{identifier.lower()}",
        "action": "update",
        "type": "Issue",
        "data": {
            "id": f"issue-{identifier.lower()}",
            "identifier": identifier,
            "labels": {"nodes": labels},
        },
    }


def enqueue(q, item: dict) -> dict:
    result = q.enqueue_linear_event(item)
    assert result["inserted"] is True
    return result["item"]


def latest(q) -> dict:
    return q.queue_payload(limit=1)["items"][0]


def dispatch_identifier(dispatcher, identifier: str) -> dict:
    result = dispatcher.dispatch_issue_by_identifier(identifier, dry_run=True)
    assert result is not None
    return result


def test_agent_kai_resolves_and_wakes_only_kai(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-KAI", "kai"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-KAI")
    row = latest(q)

    assert result["marker"] == "ASSIGNED_AGENT_EVENT_DISPATCH_OK"
    assert result["wakes"] == ["kai"]
    assert row["target_agent"] == "kai"
    assert row["resolver_status"] == "resolved"
    assert row["preflight_status"] == "passed"
    assert row["dispatch_status"] == "dispatched"
    assert row["claim_owner"] == "kai"


def test_agent_fred_resolves_and_wakes_only_fred(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-FRED", "fred"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-FRED")
    row = latest(q)

    assert result["wakes"] == ["fred"]
    assert row["target_agent"] == "fred"
    assert row["dispatch_status"] == "dispatched"
    assert row["claim_owner"] == "fred"


def test_agent_agy_resolves_and_wakes_only_agy(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-AGY", "agy"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-AGY")
    row = latest(q)

    assert result["wakes"] == ["agy"]
    assert row["target_agent"] == "agy"
    assert row["dispatch_status"] == "dispatched"
    assert row["claim_owner"] == "agy"


def test_agent_george_resolves_and_wakes_only_george(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-GEORGE", "george"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-GEORGE")
    row = latest(q)

    assert result["marker"] == "ASSIGNED_AGENT_EVENT_DISPATCH_OK"
    assert result["wakes"] == ["george"]
    assert row["target_agent"] == "george"
    assert row["resolver_status"] == "resolved"
    assert row["preflight_status"] == "passed"
    assert row["dispatch_status"] == "dispatched"
    assert row["claim_owner"] == "george"


def test_george_visible_hermes_dispatch_path_is_used_when_available(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("PRISMATIC_ASSIGNED_AGENT_DRY_RUN", raising=False)
    monkeypatch.setenv("PRISMATIC_VISIBLE_HERMES_EXECUTION", "1")
    monkeypatch.setenv("PRISMATIC_HERMES_BIN", sys.executable)
    george_profile = tmp_path / "george-profile"
    george_profile.mkdir()
    monkeypatch.setenv("PRISMATIC_GEORGE_PROFILE_DIR", str(george_profile))
    enqueue(q, payload("GRO-VISIBLE-GEORGE", "george"))
    calls = []

    class FakeProc:
        pid = 42424

    def fake_visible(agent, issue_id, **kwargs):
        calls.append((agent, issue_id, kwargs))
        return FakeProc()

    monkeypatch.setattr(dispatcher, "launch_visible_hermes_agent", fake_visible)
    monkeypatch.setitem(dispatcher.AGENT_LAUNCHERS, "george", lambda *a, **k: True)
    result = dispatcher.dispatch_issue_by_identifier(
        "GRO-VISIBLE-GEORGE",
        dry_run=False,
    )
    row = latest(q)

    assert result is not None
    assert result["status"] == "dispatched"
    assert result["target_agent"] == "george"
    assert result["wakes"] == ["george"]
    assert calls and calls[0][0] == "george"
    assert row["claim_owner"] == "george"
    assert row["dispatch_status"] == "dispatched"


def test_missing_agent_metadata_fails_closed_and_wakes_nobody(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-MISSING"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-MISSING")
    row = latest(q)

    assert result["status"] == "needs_manual_review"
    assert result["wakes"] == []
    assert row["dispatch_status"] == "needs_manual_review"
    assert row["resolver_status"] == "needs_manual_review"


def test_conflicting_agent_metadata_fails_closed_and_wakes_nobody(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-CONFLICT", "kai", "agy"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-CONFLICT")
    row = latest(q)

    assert result["status"] == "needs_manual_review"
    assert result["wakes"] == []
    assert "conflicting" in row["last_error"]
    assert row["dispatch_status"] == "needs_manual_review"


def test_disabled_agent_fails_preflight_and_wakes_nobody(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch, disabled="kai")
    enqueue(q, payload("GRO-TEST-DISABLED", "kai"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-DISABLED")
    row = latest(q)

    assert result["status"] == "blocked_preflight"
    assert result["wakes"] == []
    assert row["target_agent"] == "kai"
    assert row["preflight_status"] == "blocked_preflight"
    assert row["dispatch_status"] == "blocked_preflight"


def test_unknown_agent_fails_closed_and_wakes_nobody(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-UNKNOWN", "ned"))

    result = dispatch_identifier(dispatcher, "GRO-TEST-UNKNOWN")
    row = latest(q)

    assert result["status"] == "needs_manual_review"
    assert result["wakes"] == []
    assert row["dispatch_status"] == "needs_manual_review"


def test_real_fred_wake_uses_signal_launcher_signature(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("PRISMATIC_ASSIGNED_AGENT_DRY_RUN", raising=False)
    enqueue(q, payload("GRO-TEST-FRED-REAL", "fred"))
    calls = []

    def fake_fred(issue_id: str, title: str = "", priority: int = 3):
        calls.append({"issue_id": issue_id, "title": title, "priority": priority})
        return True

    result = dispatcher.dispatch_issue_by_identifier(
        "GRO-TEST-FRED-REAL", launchers={"fred": fake_fred}, dry_run=False
    )
    row = latest(q)

    assert result["marker"] == "ASSIGNED_AGENT_EVENT_DISPATCH_OK"
    assert result["status"] == "dispatched"
    assert result["wakes"] == ["fred"]
    assert calls == [
        {"issue_id": "GRO-TEST-FRED-REAL", "title": "GRO-TEST-FRED-REAL", "priority": 3}
    ]
    assert row["dispatch_status"] == "dispatched"
    assert row["claim_owner"] == "fred"


def test_agy_preflight_accepts_binary_found_on_path(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("PRISMATIC_ASSIGNED_AGENT_DRY_RUN", raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agy_bin = bin_dir / "agy"
    agy_bin.write_text("#!/bin/sh\nexit 0\n")
    agy_bin.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    row = enqueue(q, payload("GRO-TEST-AGY-PATH", "agy"))
    row = latest(q)

    resolution = dispatcher.resolve_assigned_agent(row)
    preflight = dispatcher.preflight_assigned_agent(
        row, resolution, launchers={"agy": lambda *a, **k: True}
    )

    assert resolution.status == "resolved"
    assert preflight.status == "passed"


def test_already_claimed_running_completed_rows_are_not_double_dispatched(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    for status in ["claimed", "processing", "dispatched"]:
        event_id = f"evt-{status}"
        item = payload(f"GRO-TEST-{status.upper()}", "fred", event_id=event_id)
        enqueue(q, item)
        q.update_assigned_dispatch_state(
            event_id, dispatch_status=status, target_agent="fred", claim_owner="fred"
        )
        row = latest(q)
        result = dispatcher.dispatch_assigned_agent_event(row, dry_run=True)
        assert result["wakes"] == []
        assert result["status"] == "blocked_preflight"


def test_rate_limit_cooldown_blocks_wake_and_records_deferred(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-COOLDOWN", "agy"))
    from prismatic.linear_rate_limit import LinearRateLimitState

    LinearRateLimitState(tmp_path / "linear_rate_limit_state.json").trip(
        reason="fixture cooldown", source="test.assigned_agent", remaining=0, limit=100
    )

    result = dispatch_identifier(dispatcher, "GRO-TEST-COOLDOWN")
    row = latest(q)

    assert result["status"] == "deferred_rate_limit"
    assert result["wakes"] == []
    assert row["preflight_status"] == "deferred_rate_limit"
    assert row["dispatch_status"] == "deferred_rate_limit"


def test_queue_status_api_fields_expose_assigned_agent_state(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-API", "kai"))
    dispatch_identifier(dispatcher, "GRO-TEST-API")

    status = q.queue_status_payload()
    latest_event = status["latest_event"]

    assert status["assigned_agent_marker"] == "ASSIGNED_AGENT_EVENT_DISPATCH_OK"
    assert latest_event["target_agent"] == "kai"
    assert latest_event["routing_source"] == "label"
    assert latest_event["resolver_status"] == "resolved"
    assert latest_event["preflight_status"] == "passed"
    assert latest_event["dispatch_status"] == "dispatched"


def test_result_writeback_completed_persists_dry_run_preview(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-WB-DONE", "kai"))
    dispatch = dispatch_identifier(dispatcher, "GRO-TEST-WB-DONE")

    result = dispatcher.record_assigned_agent_result_writeback(
        run_id=dispatch["run_id"],
        result_status="completed",
        result_summary="Fixture agent completed acceptance checks.",
    )
    row = latest(q)

    assert result["marker"] == "ASSIGNED_AGENT_RESULT_WRITEBACK_OK"
    assert result["status"] == "dry_run"
    assert result["linear_mutation"] is False
    assert row["result_status"] == "completed"
    assert row["dispatch_status"] == "completed"
    assert row["writeback_status"] == "dry_run"
    assert row["writeback_mode"] == "linear_comment_preview"
    assert "Fixture agent completed" in row["writeback_preview"]
    assert row["retry_status"] == "not_required"
    assert row["recovery_status"] == "completed"


def test_result_writeback_blocker_records_operator_visible_blocker(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-WB-BLOCKED", "fred"))
    dispatch = dispatch_identifier(dispatcher, "GRO-TEST-WB-BLOCKED")

    result = dispatcher.record_assigned_agent_result_writeback(
        run_id=dispatch["run_id"],
        result_status="blocked",
        blocker_summary="Missing deploy credential.",
    )
    row = latest(q)

    assert result["status"] == "dry_run"
    assert row["result_status"] == "blocked"
    assert row["dispatch_status"] == "blocked"
    assert row["blocker_summary"] == "Missing deploy credential."
    assert row["retry_status"] == "blocked_until_operator_review"
    assert row["recovery_status"] == "blocked"
    assert "Missing deploy credential" in row["writeback_preview"]


def test_result_writeback_failed_is_retry_eligible_and_retry_updates_recovery_state(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-WB-FAILED", "agy"))
    dispatch = dispatch_identifier(dispatcher, "GRO-TEST-WB-FAILED")

    dispatcher.record_assigned_agent_result_writeback(
        run_id=dispatch["run_id"],
        result_status="failed",
        result_summary="Runner exited non-zero.",
    )
    failed = latest(q)
    assert failed["result_status"] == "failed"
    assert failed["retry_status"] == "retry_eligible"
    assert failed["recovery_status"] == "failed_retryable"

    retry = q.retry_task(failed["event_id"])
    assert retry["ok"] is True
    assert retry["item"]["dispatch_status"] == "pending"
    assert retry["item"]["retry_status"] == "retry_requested"
    assert retry["item"]["retry_count"] == 1
    assert retry["item"]["recovery_status"] == "queued_for_retry"


def test_result_writeback_live_request_without_authorization_is_blocked(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("PRISMATIC_LINEAR_WRITEBACK_AUTHORIZED", raising=False)
    enqueue(q, payload("GRO-TEST-WB-LIVE-BLOCK", "kai"))
    dispatch = dispatch_identifier(dispatcher, "GRO-TEST-WB-LIVE-BLOCK")

    result = dispatcher.record_assigned_agent_result_writeback(
        run_id=dispatch["run_id"],
        result_status="completed",
        result_summary="Should not mutate Linear.",
        dry_run=False,
    )
    row = latest(q)

    assert result["ok"] is False
    assert result["status"] == "blocked_live_unauthorized"
    assert result["linear_mutation"] is False
    assert row["writeback_status"] == "blocked_live_unauthorized"
    assert row["retry_status"] == "operator_authorization_required"
    assert row["recovery_status"] == "writeback_blocked"


def test_queue_status_exposes_result_writeback_fields(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-WB-API", "fred"))
    dispatch = dispatch_identifier(dispatcher, "GRO-TEST-WB-API")
    dispatcher.record_assigned_agent_result_writeback(
        run_id=dispatch["run_id"],
        result_status="blocked",
        blocker_summary="Needs operator review.",
    )

    status = q.queue_status_payload()
    latest_event = status["latest_event"]

    assert status["result_writeback_marker"] == "ASSIGNED_AGENT_RESULT_WRITEBACK_OK"
    assert latest_event["result_status"] == "blocked"
    assert latest_event["writeback_status"] == "dry_run"
    assert latest_event["writeback_mode"] == "linear_comment_preview"
    assert latest_event["retry_status"] == "blocked_until_operator_review"
    assert latest_event["recovery_status"] == "blocked"


def test_dispatch_recovery_proves_resolver_preflight_wake_and_writeback_for_one_controlled_task(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-TEST-RECOVERY-OK", "kai"))

    proof = dispatcher.run_assigned_agent_dispatch_recovery(
        identifier="GRO-TEST-RECOVERY-OK",
        result_status="completed",
        result_summary="Controlled recovery task completed.",
    )
    row = latest(q)
    status = q.queue_status_payload()

    assert proof["marker"] == "ASSIGNED_AGENT_DISPATCH_RECOVERY_OK"
    assert proof["ok"] is True
    assert proof["phases"] == {
        "resolver": True,
        "preflight": True,
        "wake": True,
        "result_writeback": True,
    }
    assert proof["dispatch"]["marker"] == "ASSIGNED_AGENT_EVENT_DISPATCH_OK"
    assert proof["dispatch"]["wakes"] == ["kai"]
    assert proof["writeback"]["marker"] == "ASSIGNED_AGENT_RESULT_WRITEBACK_OK"
    assert proof["writeback"]["linear_mutation"] is False
    assert row["resolver_status"] == "resolved"
    assert row["preflight_status"] == "passed"
    assert row["claim_owner"] == "kai"
    assert row["result_status"] == "completed"
    assert row["writeback_status"] == "dry_run"
    assert row["retry_status"] == "not_required"
    assert status["dispatch_recovery_marker"] == "ASSIGNED_AGENT_DISPATCH_RECOVERY_OK"


def test_old_poller_gate_remains_absent_and_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert not (Path.home() / ".prismatic" / "allow-poll-dispatcher").exists()


def test_visible_agent_stream_event_dry_run_logs_message(tmp_path: Path, monkeypatch):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    log_path = tmp_path / "visible-wake.log"
    monkeypatch.setenv("PRISMATIC_VISIBLE_WAKE_DRY_RUN", "1")
    monkeypatch.setenv("PRISMATIC_VISIBLE_WAKE_LOG", str(log_path))

    result = dispatcher.emit_visible_agent_stream_event(
        "fred",
        "GRO-VISIBLE",
        "WAKE_STARTED",
        title="Visible stream proof",
        run_id="assigned-fred-visible",
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    text = log_path.read_text()
    assert "Prismatic assigned-agent stream: WAKE_STARTED" in text
    assert "agent=fred" in text
    assert "issue=GRO-VISIBLE" in text
    assert "run_id=assigned-fred-visible" in text
    assert "Linear remains source of truth" in text


def test_assigned_dispatch_emits_visible_started_and_dispatched_events(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("PRISMATIC_ASSIGNED_AGENT_DRY_RUN", raising=False)
    enqueue(q, payload("GRO-VISIBLE-FRED", "fred"))
    emitted: list[tuple[str, str, str, str]] = []

    def fake_visible(agent, issue_id, status, *, title="", run_id="", reason=""):
        emitted.append((agent, issue_id, status, run_id))
        return {"ok": True, "dry_run": True}

    monkeypatch.setattr(dispatcher, "emit_visible_agent_stream_event", fake_visible)
    result = dispatcher.dispatch_issue_by_identifier(
        "GRO-VISIBLE-FRED",
        dry_run=False,
        launchers={"fred": lambda identifier, title="", priority=3: True},
    )

    assert result is not None
    assert result["wakes"] == ["fred"]
    assert [item[2] for item in emitted] == ["WAKE_STARTED", "WAKE_DISPATCHED"]
    assert all(item[0] == "fred" for item in emitted)
    assert all(item[1] == "GRO-VISIBLE-FRED" for item in emitted)
    assert emitted[0][3].startswith("assigned-fred-")


def test_assigned_dispatch_uses_visible_hermes_execution_for_fred(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("PRISMATIC_ASSIGNED_AGENT_DRY_RUN", raising=False)
    monkeypatch.setenv("PRISMATIC_VISIBLE_HERMES_EXECUTION", "1")
    monkeypatch.setenv("PRISMATIC_AGENT_RUN_LOG_DIR", str(tmp_path / "runs"))
    fake_hermes = tmp_path / "hermes"
    fake_hermes.write_text(
        "#!/usr/bin/env bash\necho RESULT=BLOCKED\necho MARKER=VISIBLE_HERMES_TEST_BLOCKED\n",
        encoding="utf-8",
    )
    fake_hermes.chmod(0o700)
    monkeypatch.setenv("PRISMATIC_HERMES_BIN", str(fake_hermes))
    enqueue(q, payload("GRO-VISIBLE-HERMES", "fred"))

    result = dispatcher.dispatch_issue_by_identifier(
        "GRO-VISIBLE-HERMES", dry_run=False
    )

    assert result is not None
    assert result["wakes"] == ["fred"]
    row = latest(q)
    assert row["dispatch_status"] == "dispatched"
    with sqlite3.connect(tmp_path / "event_router.db") as con:
        launch_rows = list(
            con.execute(
                "select agent_name, command_json from launch_records where identifier=?",
                ("GRO-VISIBLE-HERMES",),
            )
        )
    assert launch_rows
    assert launch_rows[0][0] == "fred"
    assert "--profile" in launch_rows[0][1]


def test_agy_print_mode_wrapper_records_skill_packs_and_forces_blocked_packet(
    tmp_path, monkeypatch
):
    import sqlite3

    from prismatic import dispatcher

    state_dir = tmp_path / "state"
    run_dir = tmp_path / "runs"
    fake_agy = tmp_path / "agy"
    fake_agy.write_text(
        "#!/usr/bin/env bash\nprintf 'AGY_FAKE_STDOUT_WITHOUT_PACKET\\n'\nexit 0\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    monkeypatch.setenv(
        "PRISMATIC_LAUNCH_RECORDS_DB_PATH", str(state_dir / "event_router.db")
    )
    monkeypatch.setenv("PRISMATIC_AGENT_RUN_LOG_DIR", str(run_dir))
    monkeypatch.setenv("PRISMATIC_AGY_USE_SYSTEMD_SCOPE", "0")
    monkeypatch.setenv("PRISMATIC_AGY_FORCE_PACKET_WRAPPER", "1")
    monkeypatch.setattr(dispatcher, "AGY_PATH", str(fake_agy))

    proc = dispatcher.launch_agy(
        "GRO-3954",
        title="Fixture repair hints",
        identifier="GRO-3954",
        labels=["agent:agy"],
    )
    assert proc is not None
    assert proc.wait(timeout=20) == 0

    logs = list(run_dir.glob("agy-GRO-3954-*.log"))
    assert logs
    text = logs[0].read_text(encoding="utf-8")
    assert "AGY_OUTPUT_CAPTURE_WRAPPER_STARTED" in text
    assert "skill_pack_state=loaded" in text
    assert "shared/prismatic-completed-work-contract" in text
    assert "agy/agy-structured-result-packet" in text
    assert "AGY_FAKE_STDOUT_WITHOUT_PACKET" in text
    assert "RESULT=BLOCKED" in text
    assert "MARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED" in text

    with sqlite3.connect(state_dir / "event_router.db") as con:
        rows = list(
            con.execute(
                "select command_json from launch_records where identifier='GRO-3954'"
            )
        )
    assert rows
    command_json = rows[0][0]
    assert "PRISMATIC_AGY_OUTPUT_LOG=" in command_json
    assert "bash" in command_json
    assert "agy/agy-model-preflight" in command_json
