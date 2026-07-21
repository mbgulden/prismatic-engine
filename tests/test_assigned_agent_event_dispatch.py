from __future__ import annotations

import importlib
import json
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
    assert "context_pack_path=" in text
    assert "work_packet_path=" in text
    assert "skill_pack_state=loaded" in text
    assert "shared/prismatic-completed-work-contract" in text
    assert "agy/agy-structured-result-packet" in text
    assert "AGY_FAKE_STDOUT_WITHOUT_PACKET" in text
    assert "RESULT=BLOCKED" in text
    assert "MARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED" in text

    with sqlite3.connect(state_dir / "event_router.db") as con:
        rows = list(
            con.execute(
                "select command_json, execution_context "
                "from launch_records where identifier='GRO-3954'"
            )
        )
    assert rows
    command_json = rows[0][0]
    execution_context = json.loads(rows[0][1])
    assert "PRISMATIC_AGY_OUTPUT_LOG=" in command_json
    assert "bash" in command_json
    assert "agy/agy-model-preflight" in command_json
    assert "--add-dir" in command_json
    assert "CONTEXT_PACK.md" in command_json
    assert "AGY_CLI_CONTEXT_PACK_OK" in rows[0][1]

    context_pack = Path(execution_context["context_pack"]["context_pack"])
    work_packet = Path(execution_context["context_pack"]["work_packet"])
    packet_contract = Path(execution_context["context_pack"]["packet_contract"])
    assert context_pack.exists()
    assert work_packet.exists()
    assert packet_contract.exists()
    assert "durable context in" in context_pack.read_text(encoding="utf-8")
    work_packet_text = work_packet.read_text(encoding="utf-8")
    assert "AGY_CLI_CONTEXT_PACK_OK" in work_packet_text
    assert "MARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_OK" in work_packet_text
    assert "MARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED" in work_packet_text
    packet_contract_text = packet_contract.read_text(encoding="utf-8")
    assert (
        "standardized Prismatic completed-work output contract" in packet_contract_text
    )


def test_agy_context_pack_redacts_token_like_assignment(tmp_path):
    from prismatic import dispatcher

    files = dispatcher._write_agy_context_pack(
        context_dir=tmp_path / "context",
        issue_id="GRO-SECRET",
        identifier="GRO-SECRET",
        title_or_task="Fix bug token=super-secret-value",
        expected_marker="AGY_ASSIGNED_AGENT_GRO_SECRET_OK",
        blocked_marker="AGY_ASSIGNED_AGENT_GRO_SECRET_BLOCKED",
        labels=["agent:agy", "api_key=should-not-leak"],
        worktree_path="/tmp/worktree",
        log_path=tmp_path / "agy.log",
    )

    text = "\n".join(Path(path).read_text(encoding="utf-8") for path in files.values())
    assert "super-secret-value" not in text
    assert "should-not-leak" not in text
    assert "token=[REDACTED]" in text
    assert "api_key=[REDACTED]" in text


def test_jules_cli_context_pack_uses_new_session_and_records_context(
    tmp_path, monkeypatch
):
    from prismatic import dispatcher

    state_dir = tmp_path / "state"
    run_dir = tmp_path / "runs"
    fake_jules = tmp_path / "jules"
    fake_jules.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'JULES_FAKE_ARGS=%s\\n' \"$*\"\n"
        "printf 'Created session: jules-session-123\\n'\n"
        "case \" $* \" in *' --issue '*|*' --task '*|*' --print '*|*' --log-file '*|*' --model '*) exit 7;; esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_jules.chmod(0o755)

    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    monkeypatch.setenv(
        "PRISMATIC_LAUNCH_RECORDS_DB_PATH", str(state_dir / "event_router.db")
    )
    monkeypatch.setenv("PRISMATIC_AGENT_RUN_LOG_DIR", str(run_dir))
    monkeypatch.setenv("PRISMATIC_WORKTREE_PATH", str(tmp_path))
    monkeypatch.setattr(dispatcher, "JULES_PATH", str(fake_jules))

    proc = dispatcher.launch_jules(
        "GRO-JULES",
        title="Review bounded task token=do-not-leak",
        identifier="GRO-JULES",
        labels=["agent:jules", "api_key=do-not-leak-either"],
    )
    assert proc is not None
    assert proc.wait(timeout=20) == 0

    logs = list(run_dir.glob("jules-GRO-JULES-*.log"))
    assert logs
    text = logs[0].read_text(encoding="utf-8")
    assert "JULES_SESSION_CAPTURE_STARTED" in text
    assert "context_pack_path=" in text
    assert "work_packet_path=" in text
    assert "skill_pack_state=loaded" in text
    assert "jules/jules-session-handle-capture" in text
    assert "JULES_FAKE_ARGS=new" in text
    assert "Created session: jules-session-123" in text

    with sqlite3.connect(state_dir / "event_router.db") as con:
        row = con.execute(
            "select command_json, execution_context "
            "from launch_records where identifier='GRO-JULES'"
        ).fetchone()
    assert row
    command_json, execution_context_json = row
    execution_context = json.loads(execution_context_json)
    assert "JULES_CLI_SESSION_CONTEXT_PACK_OK" in execution_context_json
    assert "jules remote list --session" in execution_context["reconcile_hint"]
    assert "--issue" not in command_json
    assert "--task" not in command_json
    assert "--print" not in command_json
    assert "--log-file" not in command_json
    assert "--model" not in command_json
    assert '"new"' in command_json

    context_pack = Path(execution_context["context_pack"]["context_pack"])
    work_packet = Path(execution_context["context_pack"]["work_packet"])
    packet_contract = Path(execution_context["context_pack"]["packet_contract"])
    assert context_pack.exists()
    assert work_packet.exists()
    assert packet_contract.exists()
    context_text = context_pack.read_text(encoding="utf-8")
    work_packet_text = work_packet.read_text(encoding="utf-8")
    packet_contract_text = packet_contract.read_text(encoding="utf-8")
    assert "jules new <compact prompt>" in context_text
    assert "unsupported AGY-style flags" in context_text
    assert "JULES_CLI_SESSION_CONTEXT_PACK_OK" in work_packet_text
    assert "MARKER=JULES_ASSIGNED_AGENT_GRO_JULES_OK" in work_packet_text
    assert "MARKER=JULES_ASSIGNED_AGENT_GRO_JULES_BLOCKED" in work_packet_text
    assert "same Prismatic completed-work packet contract" in packet_contract_text
    combined_text = "\n".join([context_text, work_packet_text, packet_contract_text])
    assert "do-not-leak" not in combined_text
    assert "do-not-leak-either" not in combined_text
    assert "token=[REDACTED]" in combined_text
    assert "api_key=[REDACTED]" in combined_text


def _handoff_packet(agent: str = "fred") -> dict:
    return {
        "handoff_id": "handoff-gro-549",
        "source": {"agent": "george", "system": "linear"},
        "target": {"agent": agent, "required_capabilities": ["handoff_validation"]},
        "work": {
            "issue": "GRO-549",
            "scope": "test assigned-agent handoff gate",
            "base": "main",
            "allowed_paths": ["docs"],
            "production_facing": False,
        },
        "acceptance": {
            "expected_markers": ["FRED_HANDOFF_CURRENT_EVENT_PATH_REPAIR_OK"],
            "not_claiming": ["merge", "deploy"],
        },
        "retry": {"on_missing_result": "manual_review"},
        "evidence": {
            "required_artifacts": ["/tmp/proof.log"],
            "production_proof": {"required": False, "artifacts": []},
        },
        "result": {
            "status": "pass",
            "changed_paths": ["docs/handoff.md"],
            "artifacts": ["/tmp/proof.log"],
            "claims": [],
        },
    }


def test_assigned_agent_event_nested_handoff_contract_blocks_mismatch_before_launcher(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    item = payload("GRO-HANDOFF-MISMATCH", "fred")
    item["data"]["handoff_contract"] = _handoff_packet("agy")
    enqueue(q, item)
    calls: list[str] = []

    result = dispatch_identifier(dispatcher, "GRO-HANDOFF-MISMATCH")
    row = latest(q)

    assert result["status"] == "blocked_preflight"
    assert result["wakes"] == []
    assert calls == []
    assert row["preflight_status"] == "blocked_preflight"
    assert row["dispatch_status"] == "blocked_preflight"
    assert row["last_error"] == "target_agent_mismatch"


def test_assigned_agent_event_invalid_nested_handoff_blocks_and_persists_reason(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    item = payload("GRO-HANDOFF-BAD", "fred")
    bad = _handoff_packet("fred")
    bad["result"] = {"status": "pass", "changed_paths": ["secret.txt"], "artifacts": []}
    item["data"]["metadata"] = {"handoff_contract": bad}
    enqueue(q, item)

    result = dispatch_identifier(dispatcher, "GRO-HANDOFF-BAD")
    row = latest(q)

    assert result["status"] == "blocked_preflight"
    assert result["wakes"] == []
    assert result["reason"] == "handoff_contract_invalid"
    assert row["preflight_status"] == "blocked_preflight"
    assert row["dispatch_status"] == "blocked_preflight"
    assert row["last_error"] == "handoff_contract_invalid"


def test_assigned_agent_event_missing_handoff_metadata_still_dispatches(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    enqueue(q, payload("GRO-HANDOFF-MISSING", "fred"))

    result = dispatch_identifier(dispatcher, "GRO-HANDOFF-MISSING")
    row = latest(q)

    assert result["status"] == "dispatched"
    assert result["wakes"] == ["fred"]
    assert row["dispatch_status"] == "dispatched"


def test_result_writeback_captures_raw_output_before_preview_normalization(
    tmp_path: Path, monkeypatch
):
    q, dispatcher = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("PRISMATIC_AGENT_RAW_OUTPUT_DB", str(tmp_path / "raw.sqlite3"))
    enqueue(q, payload("GRO-TEST-WB-RAW", "fred"))
    dispatch = dispatch_identifier(dispatcher, "GRO-TEST-WB-RAW")
    raw_output = "Fred raw prose before packet normalization"

    result = dispatcher.record_assigned_agent_result_writeback(
        run_id=dispatch["run_id"],
        result_status="completed",
        result_summary="Fixture agent completed acceptance checks.",
        raw_output_text=raw_output,
        raw_output_artifact_path="/tmp/fred-proof.log",
    )

    assert result["raw_output_capture"]["ok"] is True
    assert result["raw_output_capture"]["source_event_id"].startswith(
        "assigned_agent_result_writeback:fred:GRO-TEST-WB-RAW:"
    )
    from prismatic.agent_raw_output_queue import RawAgentOutputStore

    row = RawAgentOutputStore(tmp_path / "raw.sqlite3").get(
        result["raw_output_capture"]["raw_output_id"]
    )
    assert row.task_id == "GRO-TEST-WB-RAW"
    assert row.raw_text_or_artifact_path == "/tmp/fred-proof.log"
    assert row.normalization_status in {
        "rejected_repairable",
        "rejected_rerun_required",
    }
