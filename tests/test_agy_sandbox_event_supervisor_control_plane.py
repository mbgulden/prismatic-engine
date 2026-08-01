from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import sqlite3
import subprocess
import sys
import threading
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "agy_sandbox_event_supervisor.py"
SUPERVISOR_HOME = str(Path("/home") / "ubuntu")
AGY_PROFILE_HOME = str(
    Path("/home") / "ubuntu" / ".hermes" / "profiles" / "kai" / "home"
)


def _load_supervisor():
    spec = importlib.util.spec_from_file_location(
        "agy_sandbox_event_supervisor_under_test", SUPERVISOR_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_agy_packet(issue_id: str = "GRO-3837") -> dict:
    return {
        "agent": "agy",
        "issue_identifier": issue_id,
        "branch": "feature/runtime-result-boundary",
        "base_branch": "main",
        "changed_files": ["scripts/example.py"],
        "result_artifacts": ["RESULT.md"],
        "verification": {
            "commands": ["python3 -m pytest -q"],
            "result": "PASS",
            "log_path": "/tmp/runtime-result-boundary.log",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": ["production_deploy"],
        "merge_lane": "manual-review",
        "risk_level": "medium",
        "next_action": "needs-human-review",
        "marker": "AGY_TASK_RESULT_PACKET_OK",
    }


def _capture(supervisor, tmp_path: Path, *, packet=None, legacy=None, attempt=1):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    result_path = sandbox / "RESULT.md"
    if legacy is not None:
        result_path.write_text(legacy, encoding="utf-8")
    packet_path = sandbox / "AGY_RESULT_PACKET.json"
    if packet is not None:
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
    boundary = supervisor.capture_and_validate_agy_result(
        issue_id="GRO-3837",
        attempt=attempt,
        result_path=result_path,
        packet_path=packet_path,
        raw_output_db=tmp_path / "queue" / "raw.sqlite3",
        completed_work_db=tmp_path / "completed" / "completed.sqlite3",
        completed_work_evidence_dir=tmp_path / "completed" / "evidence",
    )
    return boundary, packet_path, result_path


def test_fresh_process_supervisor_resolves_its_own_prismatic_package(tmp_path):
    code = f"""
import importlib.util
from pathlib import Path
path = Path({str(SUPERVISOR_PATH)!r})
spec = importlib.util.spec_from_file_location('fresh_supervisor', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
import prismatic.agent_raw_output_queue as queue
print(Path(queue.__file__).resolve())
print(hasattr(queue.RawAgentOutputStore, 'get_delivery'))
"""
    env = os.environ.copy()
    env["PRISMATIC_HOME"] = str(tmp_path / "wrong-home")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    lines = result.stdout.strip().splitlines()
    assert str(REPO_ROOT / "prismatic" / "agent_raw_output_queue.py") in lines
    assert lines[-1] == "True"


def test_supervisor_default_model_is_agy_accepted_display_label():
    supervisor = _load_supervisor()

    assert supervisor.DEFAULT_MODEL == "Gemini 3.5 Flash (Medium)"


def test_agy_cli_child_env_uses_agy_cli_home_without_mutating_supervisor_home(
    monkeypatch,
):
    supervisor = _load_supervisor()
    monkeypatch.setenv("HOME", SUPERVISOR_HOME)
    monkeypatch.setenv("AGY_CLI_HOME", AGY_PROFILE_HOME)

    child_env = supervisor.agy_cli_child_env()

    assert child_env["HOME"] == AGY_PROFILE_HOME
    assert os.environ["HOME"] == SUPERVISOR_HOME
    assert Path.home() == Path(SUPERVISOR_HOME)


def test_preflight_uses_agy_cli_home_and_does_not_spawn_real_agy(monkeypatch):
    supervisor = _load_supervisor()
    monkeypatch.setenv("HOME", SUPERVISOR_HOME)
    monkeypatch.setenv("AGY_CLI_HOME", AGY_PROFILE_HOME)
    calls = []

    class Proc:
        returncode = 0
        stdout = "OK\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Proc()

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    ok, message = supervisor.preflight_agy_backend("Gemini 3.5 Flash (Medium)")

    assert ok is True
    assert message == "AGY backend OK"
    assert calls == [
        (
            [
                supervisor.AGY_BIN,
                "--print",
                "Reply with exactly: OK",
                "--print-timeout",
                "30s",
                "--model",
                "Gemini 3.5 Flash (Medium)",
            ],
            {
                "capture_output": True,
                "text": True,
                "timeout": 45,
                "env": {**os.environ, "HOME": AGY_PROFILE_HOME},
            },
        )
    ]


def test_agy_command_builder_uses_exact_filesystem_scoped_prompt(tmp_path):
    supervisor = _load_supervisor()
    sandbox = tmp_path / "sandbox with spaces"
    prompt = "Bounded prompt\nwith exact formatting and --option-like text"
    model = "Gemini 3.5 Flash (Medium)"

    command = supervisor.build_agy_command(sandbox, prompt, model)

    assert command == [
        supervisor.AGY_BIN,
        "--dir",
        str(sandbox),
        "--print",
        prompt,
        "--dangerously-skip-permissions",
        "--print-timeout",
        supervisor.PRINT_TIMEOUT,
        "--sandbox",
        "--model",
        model,
    ]
    assert command[command.index("--print") + 1] is prompt
    assert command.count(prompt) == 1


def test_agy_command_builder_rejects_subclasses_without_calling_hooks(tmp_path):
    supervisor = _load_supervisor()

    class HookedString(str):
        def __str__(self):
            raise AssertionError("custom string hook must not run")

    class HookedSandbox:
        def __str__(self):
            raise AssertionError("custom sandbox hook must not run")

    with pytest.raises(TypeError, match="sandbox must be an exact platform Path"):
        supervisor.build_agy_command(
            HookedSandbox(), "bounded prompt", "Gemini 3.5 Flash (Medium)"
        )
    with pytest.raises(TypeError, match="prompt must be an exact string"):
        supervisor.build_agy_command(
            tmp_path, HookedString("bounded prompt"), "Gemini 3.5 Flash (Medium)"
        )
    with pytest.raises(TypeError, match="model must be an exact string"):
        supervisor.build_agy_command(tmp_path, "bounded prompt", HookedString("model"))


def test_agy_initial_and_relaunch_hooks_are_identical_without_stdin(
    monkeypatch, tmp_path
):
    supervisor = _load_supervisor()
    monkeypatch.setenv("HOME", SUPERVISOR_HOME)
    monkeypatch.setenv("AGY_CLI_HOME", AGY_PROFILE_HOME)
    sandbox = tmp_path / "sandbox"
    prompt = "one exact bounded prompt"
    command = supervisor.build_agy_command(sandbox, prompt, "Gemini 3.5 Flash (Medium)")
    calls = []

    class Proc:
        stdin = None

    def fake_popen(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return Proc()

    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)
    log_file = object()

    initial = supervisor._start_agy_process(command, log_file, sandbox)
    relaunch = supervisor._start_agy_process(command, log_file, sandbox)

    assert initial.stdin is None
    assert relaunch.stdin is None
    assert calls[0][0] == calls[1][0] == command
    assert calls[0][0][calls[0][0].index("--print") + 1] == prompt
    for _, kwargs in calls:
        assert kwargs == {
            "stdout": log_file,
            "stderr": supervisor.subprocess.STDOUT,
            "stdin": None,
            "cwd": str(sandbox),
            "env": {**os.environ, "HOME": AGY_PROFILE_HOME},
        }


def test_agy_transport_source_has_no_signed_stdin_artifacts():
    source = SUPERVISOR_PATH.read_text()

    assert (
        'env={**os.environ, "HOME": os.environ.get("HOME", str(Path.home()))}'
        not in source
    )
    assert "env=agy_cli_child_env()," in source
    assert "proc = _start_agy_process(cmd, logf, sandbox)" in source
    assert source.count("proc = _start_agy_process(cmd, logf, sandbox)") == 2
    assert "Relaunching with cmd" in source
    for forbidden in (
        "INJECTED_VIA_STDIN",
        "AGY_TASK_SIGNING_SECRET",
        "default_secret",
        "import hmac",
        "hmac.new",
        "subprocess.PIPE",
        '"--add-dir"',
    ):
        assert forbidden not in source


def test_abandonment_guard_default_is_absolute_and_profile_independent(monkeypatch):
    monkeypatch.delenv("AGY_ABANDONMENT_GUARD", raising=False)
    monkeypatch.setenv("HOME", AGY_PROFILE_HOME)

    supervisor = _load_supervisor()

    expected = (
        Path(pwd.getpwuid(os.getuid()).pw_dir)
        / ".hermes"
        / "profiles"
        / "orchestrator"
        / "scripts"
        / "agy_abandonment_guard.py"
    )
    assert supervisor.AGY_ABANDONMENT_GUARD == str(expected)
    assert not supervisor.AGY_ABANDONMENT_GUARD.startswith(AGY_PROFILE_HOME)


def test_abandonment_guard_explicit_override_is_preserved(monkeypatch, tmp_path):
    override = tmp_path / "controlled-abandonment-guard.py"
    monkeypatch.setenv("AGY_ABANDONMENT_GUARD", str(override))

    supervisor = _load_supervisor()

    assert supervisor.AGY_ABANDONMENT_GUARD == str(override)


def test_cron_wrapper_preserves_supervisor_home_and_sets_child_agy_home():
    cron = (REPO_ROOT / "scripts" / "agy_sandbox_event_supervisor_cron.sh").read_text()

    assert f"export HOME={SUPERVISOR_HOME}" in cron
    assert f'export AGY_CLI_HOME="${{AGY_CLI_HOME:-{AGY_PROFILE_HOME}}}"' in cron
    assert "--max-concurrent 3" in cron


def _write_result(path: Path, body: str) -> Path:
    result = path / "RESULT.md"
    result.write_text(body + "\n" + ("proof\n" * 300), encoding="utf-8")
    return result


def test_semantic_completion_rejects_exact_false_done_failure_packet(tmp_path):
    supervisor = _load_supervisor()
    result = _write_result(
        tmp_path,
        """# RESULT.md - GRO-3738 Verification and Status

## ERROR
ERROR: GRO-3738 candidate hash mismatch
## MISSING ARTIFACTS
CHANGED_FILES=none
FOCUSED_RESULT=FAIL
CANONICAL_RESULT=FAIL
LINT_RESULT=FAIL
BUILD_RESULT=FAIL
INSTALLED_WHEEL_RESULT=FAIL
MARKER=GRO3738_REPAIR1_CANONICAL_PLAN_OK""",
    )

    decision = supervisor.semantic_completion(result, completion_signal=True)

    assert decision["has_done"] is False
    assert decision["has_error"] is True
    assert decision["has_partial_result"] is False
    assert "explicit_error" in decision["reasons"]
    assert "changed_files_empty" in decision["reasons"]
    assert "verifier_focused_result_fail" in decision["reasons"]


def test_semantic_completion_accepts_success_packet_with_completion_signal(tmp_path):
    supervisor = _load_supervisor()
    result = _write_result(
        tmp_path,
        """# Result
RESULT=PASS
CHANGED_FILES=scripts/safe.py
FOCUSED_RESULT=PASS
CANONICAL_RESULT=PASS
BUILD_RESULT=PASS
MARKER=SAFE_RESULT_OK""",
    )

    decision = supervisor.semantic_completion(result, completion_signal=True)

    assert decision["passed"] is True
    assert decision["has_done"] is True
    assert decision["has_error"] is False
    assert decision["has_partial_result"] is False


def test_semantic_completion_never_promotes_substantive_partial_packet(tmp_path):
    supervisor = _load_supervisor()
    result = _write_result(
        tmp_path,
        """# Partial progress
CHANGED_FILES=scripts/incomplete.py
FOCUSED_RESULT=PASS
MARKER=PARTIAL_ONLY""",
    )

    decision = supervisor.semantic_completion(result, completion_signal=False)

    assert decision["passed"] is True
    assert decision["has_done"] is False
    assert decision["has_partial_result"] is True


def test_stale_result_cleanup_removes_exact_outputs_and_fails_closed(
    tmp_path, monkeypatch
):
    supervisor = _load_supervisor()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "RESULT.md").mkdir()
    target = tmp_path / "old-packet.json"
    target.write_text("old", encoding="utf-8")
    (sandbox / "AGY_RESULT_PACKET.json").symlink_to(target)
    untouched = sandbox / "keep.txt"
    untouched.write_text("keep", encoding="utf-8")

    removed = supervisor.remove_stale_agy_result_outputs(sandbox)

    assert removed == ("RESULT.md", "AGY_RESULT_PACKET.json")
    assert not (sandbox / "RESULT.md").exists()
    assert not (sandbox / "AGY_RESULT_PACKET.json").exists()
    assert target.read_text(encoding="utf-8") == "old"
    assert untouched.read_text(encoding="utf-8") == "keep"

    packet = sandbox / "AGY_RESULT_PACKET.json"
    packet.write_text("stale", encoding="utf-8")
    original_unlink = type(packet).unlink

    def blocked_unlink(path, *args, **kwargs):
        if path == packet:
            raise PermissionError("blocked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(packet), "unlink", blocked_unlink)
    with pytest.raises(
        RuntimeError, match="failed to remove stale result control output"
    ):
        supervisor.remove_stale_agy_result_outputs(sandbox)
    assert packet.exists()


def test_result_boundary_captures_before_strict_raw_packet_acceptance(tmp_path):
    supervisor = _load_supervisor()
    boundary, packet_path, _ = _capture(
        supervisor, tmp_path, packet=_valid_agy_packet()
    )

    assert boundary["boundary_state"] == "canonical_valid"
    assert boundary["completion_eligible"] is True
    assert boundary["packet_issue_identifier"] == "GRO-3837"
    assert boundary["raw_output_id"].startswith("raw_")
    # Raw AGY and normalized completed-work are intentionally distinct dialects.
    assert boundary["normalization_status"] == "rejected_rerun_required"
    assert boundary["canonical_packet_id"] is None
    assert boundary["queue_rejection_reason"] == "missing packet fields: source_path"
    assert boundary["queue_repair_hint"] == "missing_source_path"
    assert boundary["completed_work_persisted"] is True
    assert boundary["completed_work_id"].startswith("agy-cw-")
    with sqlite3.connect(tmp_path / "queue" / "raw.sqlite3") as connection:
        row = connection.execute(
            "SELECT task_id, source_event_id, raw_text_or_artifact_path FROM agent_raw_output_queue"
        ).fetchone()
    assert row is not None
    assert row[0] == "GRO-3837"
    assert row[1] == boundary["source_event_id"]
    assert row[2] == str(packet_path)


def test_completed_work_is_durable_idempotent_and_retains_exact_markers(tmp_path):
    supervisor = _load_supervisor()

    first, _, _ = _capture(supervisor, tmp_path, packet=_valid_agy_packet())
    second, _, _ = _capture(supervisor, tmp_path, packet=_valid_agy_packet())

    assert first["completion_eligible"] is second["completion_eligible"] is True
    assert first["completed_work_persisted"] is True
    assert first["completed_work_id"] == second["completed_work_id"]
    assert first["completed_work_id"].startswith("agy-cw-")
    assert first["completed_work_ingestion_marker"] == "AGY_COMPLETED_WORK_INGESTION_OK"
    assert (
        first["completed_work_integration_marker"]
        == "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"
    )
    completed_db = tmp_path / "completed" / "completed.sqlite3"
    assert completed_db.is_file()
    with sqlite3.connect(completed_db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agy_completed_work"
        ).fetchone() == (1,)
    evidence_files = list((tmp_path / "completed" / "evidence").rglob("*"))
    assert any(path.is_file() for path in evidence_files)


def test_completed_work_ingest_observes_raw_row_first(tmp_path, monkeypatch):
    supervisor = _load_supervisor()
    import prismatic.agy_completed_work as completed_work

    real_store = completed_work.AgyCompletedWorkStore
    raw_db = tmp_path / "queue" / "raw.sqlite3"

    class OrderingStore:
        def __init__(self, db_path, *, evidence_dir):
            with sqlite3.connect(raw_db) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM agent_raw_output_queue"
                ).fetchone() == (1,)
            self.store = real_store(db_path, evidence_dir=evidence_dir)

        def ingest(self, packet):
            return self.store.ingest(packet)

    monkeypatch.setattr(completed_work, "AgyCompletedWorkStore", OrderingStore)
    boundary, _, _ = _capture(supervisor, tmp_path, packet=_valid_agy_packet())

    assert boundary["completed_work_persisted"] is True
    assert boundary["completion_eligible"] is True


def test_different_authoritative_packet_content_gets_distinct_identity(tmp_path):
    supervisor = _load_supervisor()
    first_packet = _valid_agy_packet()
    second_packet = _valid_agy_packet()
    second_packet["branch"] = "feature/runtime-result-boundary-second"

    first, _, _ = _capture(supervisor, tmp_path, packet=first_packet, attempt=1)
    second, _, _ = _capture(supervisor, tmp_path, packet=second_packet, attempt=2)

    assert first["completed_work_id"] != second["completed_work_id"]
    with sqlite3.connect(tmp_path / "completed" / "completed.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agy_completed_work"
        ).fetchone() == (2,)


def test_completed_work_retains_classification_without_promoting_it(tmp_path):
    supervisor = _load_supervisor()
    relative, _, _ = _capture(supervisor, tmp_path, packet=_valid_agy_packet())
    absolute_packet = _valid_agy_packet()
    absolute_packet["risk_level"] = "low"
    absolute_packet["merge_lane"] = "docs"
    absolute_packet["next_action"] = "merge-ready"
    absolute_packet["changed_files"] = ["docs/agy-result-packet-contract.md"]
    absolute_packet["non_claims"] = ["production_deploy", "auto_merge_enabled"]
    absolute_packet["result_artifacts"] = [
        {
            "path": str(
                Path.home() / ".prismatic" / "agy-results" / "GRO-3837" / "RESULT.md"
            )
        }
    ]
    absolute_root = tmp_path / "absolute"
    absolute_root.mkdir()
    absolute, _, _ = _capture(supervisor, absolute_root, packet=absolute_packet)

    assert relative["completed_work_persisted"] is True
    assert relative["completed_work_classification"] != "merge_ready"
    assert relative["completed_work_eligible_for_merge"] is False
    assert absolute["completed_work_persisted"] is True
    assert absolute["completed_work_classification"] == "merge_ready"
    assert absolute["completed_work_integration_classification"] == (
        "pass_ready_for_review"
    )
    assert absolute["completed_work_eligible_for_merge"] is True


class _HookedCompletedWorkId(str):
    pass


@pytest.mark.parametrize(
    ("row_id", "ingestion_marker", "row_dict"),
    [
        (
            "",
            "AGY_COMPLETED_WORK_INGESTION_OK",
            {"integration_marker": "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"},
        ),
        (
            "   ",
            "AGY_COMPLETED_WORK_INGESTION_OK",
            {"integration_marker": "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"},
        ),
        (
            _HookedCompletedWorkId("agy-cw-hooked"),
            "AGY_COMPLETED_WORK_INGESTION_OK",
            {"integration_marker": "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"},
        ),
        (
            "agy-cw-id",
            "wrong",
            {"integration_marker": "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"},
        ),
        (
            "agy-cw-id",
            _HookedCompletedWorkId("AGY_COMPLETED_WORK_INGESTION_OK"),
            {"integration_marker": "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"},
        ),
        (
            "agy-cw-id",
            "AGY_COMPLETED_WORK_INGESTION_OK",
            {
                "integration_marker": _HookedCompletedWorkId(
                    "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"
                )
            },
        ),
        (
            "agy-cw-id",
            "AGY_COMPLETED_WORK_INGESTION_OK",
            {"integration_marker": "wrong"},
        ),
        ("agy-cw-id", "AGY_COMPLETED_WORK_INGESTION_OK", []),
    ],
)
def test_completed_work_impossible_return_shapes_fail_closed(
    tmp_path, monkeypatch, row_id, ingestion_marker, row_dict
):
    supervisor = _load_supervisor()
    import prismatic.agy_completed_work as completed_work

    class FakeRow:
        def __init__(self):
            self.id = row_id
            self.ingestion_marker = ingestion_marker

        def as_dict(self):
            return row_dict

    class FakeStore:
        def __init__(self, _db_path, *, evidence_dir):
            pass

        def ingest(self, _packet):
            return FakeRow()

    monkeypatch.setattr(completed_work, "AgyCompletedWorkStore", FakeStore)
    boundary, _, _ = _capture(supervisor, tmp_path, packet=_valid_agy_packet())

    assert boundary["boundary_state"] == "completed_work_persist_failed"
    assert boundary["boundary_reason"] == "completed_work_ledger_persist_failed"
    assert boundary["raw_capture_succeeded"] is True
    assert boundary["completed_work_persisted"] is False
    assert boundary["completion_eligible"] is False


def test_completed_work_exception_and_evidence_only_failure_are_sanitized(
    tmp_path, monkeypatch
):
    supervisor = _load_supervisor()
    import prismatic.agy_completed_work as completed_work

    evidence = tmp_path / "completed" / "evidence" / "orphan.json"

    class EvidenceOnlyStore:
        def __init__(self, _db_path, *, evidence_dir):
            Path(evidence_dir).mkdir(parents=True, exist_ok=True)

        def ingest(self, _packet):
            evidence.write_text("retained", encoding="utf-8")
            raise sqlite3.OperationalError("secret-value /secret/path")

    monkeypatch.setattr(completed_work, "AgyCompletedWorkStore", EvidenceOnlyStore)
    boundary, _, _ = _capture(supervisor, tmp_path, packet=_valid_agy_packet())

    assert evidence.is_file()
    assert boundary["raw_capture_succeeded"] is True
    assert boundary["completed_work_persisted"] is False
    assert boundary["completion_eligible"] is False
    assert "secret-value" not in json.dumps(boundary)
    assert not (tmp_path / "completed" / "completed.sqlite3").exists()


def test_completed_work_constructor_exception_fails_closed(tmp_path, monkeypatch):
    supervisor = _load_supervisor()
    import prismatic.agy_completed_work as completed_work

    class BrokenStore:
        def __init__(self, _db_path, *, evidence_dir):
            raise OSError("private constructor detail")

    monkeypatch.setattr(completed_work, "AgyCompletedWorkStore", BrokenStore)
    boundary, _, _ = _capture(supervisor, tmp_path, packet=_valid_agy_packet())

    assert boundary["boundary_state"] == "completed_work_persist_failed"
    assert boundary["raw_capture_succeeded"] is True
    assert boundary["completed_work_persisted"] is False
    assert "private constructor detail" not in json.dumps(boundary)


def test_immediate_reconciliation_exception_returns_sanitized_boundary(
    tmp_path, monkeypatch
):
    supervisor = _load_supervisor()

    def broken_reconciliation(**_kwargs):
        raise OSError("private reconciliation detail")

    monkeypatch.setattr(supervisor, "reconcile_agy_raw_output", broken_reconciliation)
    boundary, _, _ = _capture(supervisor, tmp_path, packet=_valid_agy_packet())
    assert boundary["boundary_state"] == "completed_work_persist_failed"
    assert boundary["boundary_reason"] == "completed_work_ledger_persist_failed"
    assert boundary["raw_capture_succeeded"] is True
    assert boundary["completed_work_persisted"] is False
    assert boundary["delivery_status"] == "storage_failed"
    assert "private reconciliation detail" not in json.dumps(boundary)


def test_preledger_failures_never_invoke_completed_work_store(tmp_path, monkeypatch):
    supervisor = _load_supervisor()
    import prismatic.agy_completed_work as completed_work

    class ForbiddenStore:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("completed-work store must not run")

    monkeypatch.setattr(completed_work, "AgyCompletedWorkStore", ForbiddenStore)
    invalid = _valid_agy_packet()
    invalid["unexpected"] = "invalid"
    invalid_boundary, _, _ = _capture(supervisor, tmp_path, packet=invalid)
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_boundary, _, _ = _capture(supervisor, legacy_root, legacy="STATUS: DONE")
    valid_legacy_root = tmp_path / "valid-legacy"
    valid_legacy_root.mkdir()
    valid_legacy_boundary, _, _ = _capture(
        supervisor, valid_legacy_root, legacy=json.dumps(_valid_agy_packet())
    )
    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    missing_boundary, _, _ = _capture(supervisor, missing_root)

    assert invalid_boundary["boundary_state"] == "canonical_invalid"
    assert legacy_boundary["boundary_state"] == "legacy_unvalidated"
    assert valid_legacy_boundary["boundary_state"] == "legacy_unvalidated"
    assert valid_legacy_boundary["completion_eligible"] is False
    assert valid_legacy_boundary["delivery_status"] == "terminal_failed"
    assert not (valid_legacy_root / "completed" / "completed.sqlite3").exists()
    assert missing_boundary["boundary_state"] == "result_missing"


def test_issue_mismatch_never_invokes_completed_work_store(tmp_path, monkeypatch):
    supervisor = _load_supervisor()
    import prismatic.agy_completed_work as completed_work

    class ForbiddenStore:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("completed-work ingest must not run")

    monkeypatch.setattr(completed_work, "AgyCompletedWorkStore", ForbiddenStore)
    boundary, _, _ = _capture(
        supervisor, tmp_path, packet=_valid_agy_packet("GRO-OTHER")
    )

    assert boundary["boundary_reason"] == "active_issue_identity_mismatch"
    assert boundary["raw_capture_succeeded"] is True


def test_result_boundary_persists_invalid_packet_before_rejecting(tmp_path):
    supervisor = _load_supervisor()
    packet = _valid_agy_packet()
    packet["unexpected"] = "not-a-secret-value"
    boundary, _, _ = _capture(supervisor, tmp_path, packet=packet)

    assert boundary["boundary_state"] == "canonical_invalid"
    assert boundary["boundary_reason"] == "canonical_packet_invalid"
    assert boundary["raw_capture_succeeded"] is True
    assert boundary["completion_eligible"] is False
    with sqlite3.connect(tmp_path / "queue" / "raw.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_raw_output_queue"
        ).fetchone() == (1,)


def test_result_boundary_holds_legacy_result_and_preserves_idempotent_identity(
    tmp_path,
):
    supervisor = _load_supervisor()
    legacy = "STATUS: DONE\nRESULT=PASS\nMARKER=LEGACY_RESULT_OK"
    first, _, _ = _capture(supervisor, tmp_path, legacy=legacy, attempt=2)
    second, _, result_path = _capture(supervisor, tmp_path, legacy=legacy, attempt=2)
    result_path.write_text(legacy + "\nchanged", encoding="utf-8")
    third = supervisor.capture_and_validate_agy_result(
        issue_id="GRO-3837",
        attempt=2,
        result_path=result_path,
        packet_path=result_path.parent / "AGY_RESULT_PACKET.json",
        raw_output_db=tmp_path / "queue" / "raw.sqlite3",
        completed_work_db=tmp_path / "completed" / "completed.sqlite3",
        completed_work_evidence_dir=tmp_path / "completed" / "evidence",
    )

    assert first["boundary_state"] == second["boundary_state"] == "legacy_unvalidated"
    assert first["completion_eligible"] is second["completion_eligible"] is False
    assert first["raw_output_id"] == second["raw_output_id"]
    assert third["raw_output_id"] != first["raw_output_id"]
    with sqlite3.connect(tmp_path / "queue" / "raw.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_raw_output_queue"
        ).fetchone() == (2,)


def test_result_boundary_rejects_active_issue_mismatch_after_capture(tmp_path):
    supervisor = _load_supervisor()
    boundary, _, _ = _capture(
        supervisor, tmp_path, packet=_valid_agy_packet("GRO-9999")
    )

    assert boundary["boundary_state"] == "canonical_invalid"
    assert boundary["boundary_reason"] == "active_issue_identity_mismatch"
    assert boundary["raw_capture_succeeded"] is True
    assert boundary["completion_eligible"] is False


def test_result_boundary_fails_closed_on_queue_failure(tmp_path):
    supervisor = _load_supervisor()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    packet_path = sandbox / "AGY_RESULT_PACKET.json"
    packet_path.write_text(json.dumps(_valid_agy_packet()), encoding="utf-8")
    result_path = sandbox / "RESULT.md"
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("file", encoding="utf-8")

    boundary = supervisor.capture_and_validate_agy_result(
        issue_id="GRO-3837",
        attempt=1,
        result_path=result_path,
        packet_path=packet_path,
        raw_output_db=blocked_parent / "raw.sqlite3",
        completed_work_db=tmp_path / "completed" / "completed.sqlite3",
        completed_work_evidence_dir=tmp_path / "completed" / "evidence",
    )
    assert boundary == {
        "boundary_state": "raw_capture_failed",
        "boundary_reason": "raw_queue_persist_failed",
        "raw_capture_succeeded": False,
        "completion_eligible": False,
    }


def test_result_boundary_rejects_empty_durable_raw_identity(tmp_path, monkeypatch):
    supervisor = _load_supervisor()
    import prismatic.agent_raw_output_queue as raw_queue

    class EmptyIdentityRow:
        raw_output_id = ""
        normalization_status = "accepted"
        canonical_packet_id = "packet_should_not_matter"
        rejection_reason = None
        repair_hint = None

    class EmptyIdentityStore:
        def __init__(self, _db_path):
            pass

        def persist(self, **_kwargs):
            return EmptyIdentityRow()

    monkeypatch.setattr(raw_queue, "RawAgentOutputStore", EmptyIdentityStore)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    packet_path = sandbox / "AGY_RESULT_PACKET.json"
    packet_path.write_text(json.dumps(_valid_agy_packet()), encoding="utf-8")

    boundary = supervisor.capture_and_validate_agy_result(
        issue_id="GRO-3837",
        attempt=1,
        result_path=sandbox / "RESULT.md",
        packet_path=packet_path,
        raw_output_db=tmp_path / "queue" / "raw.sqlite3",
        completed_work_db=tmp_path / "completed" / "completed.sqlite3",
        completed_work_evidence_dir=tmp_path / "completed" / "evidence",
    )

    assert boundary == {
        "boundary_state": "raw_capture_failed",
        "boundary_reason": "raw_queue_identity_missing",
        "raw_capture_succeeded": False,
        "completion_eligible": False,
    }


@pytest.mark.parametrize(
    ("field", "relative_path"),
    [
        ("raw_output_db", Path("relative/raw.sqlite3")),
        ("completed_work_db", Path("relative/completed.sqlite3")),
        ("completed_work_evidence_dir", Path("relative/evidence")),
    ],
)
def test_result_boundary_rejects_relative_state_paths(tmp_path, field, relative_path):
    supervisor = _load_supervisor()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    packet_path = sandbox / "AGY_RESULT_PACKET.json"
    packet_path.write_text(json.dumps(_valid_agy_packet()), encoding="utf-8")
    kwargs = {
        "issue_id": "GRO-3837",
        "attempt": 1,
        "result_path": sandbox / "RESULT.md",
        "packet_path": packet_path,
        "raw_output_db": tmp_path / "queue" / "raw.sqlite3",
        "completed_work_db": tmp_path / "completed" / "completed.sqlite3",
        "completed_work_evidence_dir": tmp_path / "completed" / "evidence",
    }
    kwargs[field] = relative_path

    boundary = supervisor.capture_and_validate_agy_result(**kwargs)

    assert boundary == {
        "boundary_state": "canonical_invalid",
        "boundary_reason": "unsafe_result_path",
        "raw_capture_succeeded": False,
        "completion_eligible": False,
    }


def test_result_boundary_rejects_symlink_directory_and_hooked_inputs(tmp_path):
    supervisor = _load_supervisor()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    result_path = sandbox / "RESULT.md"
    result_path.write_text("legacy", encoding="utf-8")
    target = tmp_path / "packet.json"
    target.write_text(json.dumps(_valid_agy_packet()), encoding="utf-8")
    packet_path = sandbox / "AGY_RESULT_PACKET.json"
    packet_path.symlink_to(target)

    boundary = supervisor.capture_and_validate_agy_result(
        issue_id="GRO-3837",
        attempt=1,
        result_path=result_path,
        packet_path=packet_path,
        raw_output_db=tmp_path / "queue" / "raw.sqlite3",
        completed_work_db=tmp_path / "completed" / "completed.sqlite3",
        completed_work_evidence_dir=tmp_path / "completed" / "evidence",
    )
    assert boundary["boundary_state"] == "canonical_invalid"
    assert boundary["raw_capture_succeeded"] is False

    packet_path.unlink()
    packet_path.mkdir()
    boundary = supervisor.capture_and_validate_agy_result(
        issue_id="GRO-3837",
        attempt=1,
        result_path=result_path,
        packet_path=packet_path,
        raw_output_db=tmp_path / "queue" / "raw.sqlite3",
        completed_work_db=tmp_path / "completed" / "completed.sqlite3",
        completed_work_evidence_dir=tmp_path / "completed" / "evidence",
    )
    assert boundary["boundary_state"] == "canonical_invalid"
    assert boundary["raw_capture_succeeded"] is False

    class HookedString(str):
        def __str__(self):
            raise AssertionError("hook must not run")

    with pytest.raises(TypeError, match="issue_id must be an exact string"):
        supervisor.capture_and_validate_agy_result(
            issue_id=HookedString("GRO-3837"),
            attempt=1,
            result_path=result_path,
            packet_path=packet_path,
            raw_output_db=tmp_path / "queue" / "raw.sqlite3",
            completed_work_db=tmp_path / "completed" / "completed.sqlite3",
            completed_work_evidence_dir=tmp_path / "completed" / "evidence",
        )


def test_result_boundary_oversize_and_secret_payloads_are_sanitized(
    tmp_path, monkeypatch
):
    supervisor = _load_supervisor()
    monkeypatch.setenv("PRISMATIC_AGENT_RAW_OUTPUT_MAX_BYTES", "64")
    packet = _valid_agy_packet()
    packet["unexpected"] = "api_key=abcdefghijklmnop"
    boundary, _, _ = _capture(supervisor, tmp_path, packet=packet)

    assert boundary["boundary_state"] == "canonical_invalid"
    assert boundary["boundary_reason"] == "oversized_payload"
    assert "abcdefghijklmnop" not in json.dumps(boundary)
    with sqlite3.connect(tmp_path / "queue" / "raw.sqlite3") as connection:
        stored = connection.execute(
            "SELECT raw_text FROM agent_raw_output_queue"
        ).fetchone()[0]
    assert "abcdefghijklmnop" not in stored


def test_result_boundary_import_is_queue_side_effect_free(tmp_path, monkeypatch):
    queue_db = tmp_path / "state" / "raw.sqlite3"
    monkeypatch.setenv("PRISMATIC_AGENT_RAW_OUTPUT_DB", str(queue_db))
    real_mkdir = Path.mkdir
    real_connect = sqlite3.connect
    calls = []

    def tracked_mkdir(path, *args, **kwargs):
        calls.append(("mkdir", path))
        return real_mkdir(path, *args, **kwargs)

    def tracked_connect(*args, **kwargs):
        calls.append(("connect", args[0] if args else None))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", tracked_mkdir)
    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    supervisor = _load_supervisor()

    completed_root = (
        supervisor._SERVICE_ACCOUNT_HOME / ".prismatic" / "state" / "agy-completed-work"
    )
    completed_db = completed_root / "agy_completed_work.db"
    completed_evidence = completed_root / "evidence"
    assert not any(kind == "connect" for kind, _path in calls)
    assert not any(
        kind == "mkdir" and path in {completed_db.parent, completed_evidence}
        for kind, path in calls
    )
    assert supervisor.agy_raw_output_db_path() == queue_db
    assert supervisor.agy_completed_work_db_path() == completed_db
    assert supervisor.agy_completed_work_evidence_dir() == completed_evidence
    assert not queue_db.exists()
    assert not queue_db.parent.exists()


def _minimal_worker(supervisor, task):
    worker = supervisor.EventDrivenSupervisor.__new__(supervisor.EventDrivenSupervisor)
    worker.shutdown_event = threading.Event()
    worker.active_lock = threading.Lock()
    worker.active_count = 0
    worker.idle_event = threading.Event()
    worker.idle_event.set()
    worker.token_pool = None
    worker.completed_issues = set()
    worker.model = "agy-default"
    worker.backoff_range = (0.0, 0.0)
    worker.launch_jitter_range = (0.0, 0.0)
    worker.marked = []
    worker.mark_completed = worker.marked.append

    class Scheduler:
        def __init__(self):
            self.served = False
            self.finishes = []

        def get_next(self, _shutdown_event):
            if self.served:
                return None
            self.served = True
            return task

        def finish(self, finished_task, *, allow_requeue):
            self.finishes.append((finished_task["issue_id"], allow_requeue))
            worker.shutdown_event.set()

    worker.scheduler = Scheduler()
    return worker


def test_worker_sandbox_failure_is_requeueable_and_never_marked_completed(
    monkeypatch,
):
    supervisor = _load_supervisor()
    monkeypatch.setattr(supervisor.random, "uniform", lambda *_args: 0.0)
    worker = _minimal_worker(
        supervisor, {"issue_id": "GRO-SANDBOX-FAIL", "lane": "default"}
    )

    def fail_sandbox(_task):
        raise OSError("sandbox unavailable")

    worker.create_sandbox_env = fail_sandbox
    worker.worker_loop(1)

    assert worker.marked == []
    assert worker.scheduler.finishes == [("GRO-SANDBOX-FAIL", True)]
    assert worker.active_count == 0


def test_worker_quota_early_exit_cannot_reuse_success_or_suppress_requeue(
    tmp_path, monkeypatch
):
    supervisor = _load_supervisor()
    monkeypatch.setattr(supervisor.random, "uniform", lambda *_args: 0.0)
    task = {
        "issue_id": "GRO-QUOTA-BLOCK",
        "lane": "default",
        "labels": [],
    }
    worker = _minimal_worker(supervisor, task)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    task_path = tmp_path / "AGY_TASK.md"
    log_path = tmp_path / "agy.log"
    worker.create_sandbox_env = lambda _task: (sandbox, task_path, log_path)
    worker.add_task = lambda _task: False

    class Linear:
        def update_issue(self, *_args, **_kwargs):
            return None

    class Quota:
        def check_quota(self, _model):
            return False, "quota blocked"

    class Bus:
        def publish_canonical(self, *_args, **_kwargs):
            return None

    worker.linear_client = Linear()
    worker.quota_client = Quota()
    worker.bus_client = Bus()
    worker.worker_loop(1)

    assert worker.marked == []
    assert worker.scheduler.finishes == [("GRO-QUOTA-BLOCK", True)]
    assert worker.active_count == 0


def test_circuit_success_requires_current_final_completion_eligibility():
    supervisor = _load_supervisor()
    worker = supervisor.EventDrivenSupervisor.__new__(supervisor.EventDrivenSupervisor)

    assert worker._is_circuit_failure({"completion_eligible": True}) is False
    assert worker._is_circuit_failure({"completion_eligible": False}) is True
    assert worker._is_circuit_failure({"has_error": True}) is True
    assert (
        worker._is_circuit_failure(
            {
                "completion_eligible": False,
                "result_boundary": {
                    "completed_work_persisted": True,
                    "boundary_state": "canonical_valid",
                },
            }
        )
        is True
    )
    assert (
        worker._is_circuit_failure(
            {"result_boundary": {"boundary_state": "canonical_invalid"}}
        )
        is True
    )


def test_worker_final_disposition_uses_reset_current_result_only():
    source = SUPERVISOR_PATH.read_text(encoding="utf-8")
    issue_assignment = 'issue_id = task["issue_id"]'
    reset = "result = None"
    active = "with self.active_lock:"
    issue_index = source.index(issue_assignment)
    assert (
        issue_index
        < source.index(reset, issue_index)
        < source.index(active, issue_index)
    )
    assert 'locals().get("result")' not in source
    sandbox_failure = source.split("sandbox failed: {e}", 1)[1].split(
        "# At task pickup", 1
    )[0]
    assert "mark_completed" not in sandbox_failure


def test_quality_gate_and_partial_linear_done_are_semantically_guarded():
    source = SUPERVISOR_PATH.read_text(encoding="utf-8")

    assert "if completion_eligible:" in source
    assert "if result_md.exists() and completion_eligible:" in source
    assert 'boundary["boundary_state"] == "canonical_valid"' in source
    assert 'boundary.get("completed_work_persisted") is True' in source
    assert '"completed_work_id": boundary.get("completed_work_id")' in source
    assert '"completed_work_ingestion_marker": boundary.get(' in source
    assert '"completed_work_integration_marker": boundary.get(' in source
    assert (
        "if completion_eligible:\n                    self.mark_completed(issue_id)"
        in source
    )
    assert "allow_requeue = not completion_eligible" in source
    assert '"normalization_status": boundary.get("normalization_status")' in source
    assert '"queue_rejection_reason": boundary.get("queue_rejection_reason")' in source
    assert '"queue_repair_hint": boundary.get("queue_repair_hint")' in source
    assert "PARTIAL_DONE" not in source


def test_rejected_result_uses_non_promotable_bus_topic(tmp_path, monkeypatch):
    supervisor = _load_supervisor()
    db = tmp_path / "event_log.sqlite"
    monkeypatch.setenv("PRISMATIC_BUS_DB", str(db))
    payload = {
        "issue_id": "GRO-3738",
        "lane": "default",
        "worker_id": 1,
        "attempt": 1,
        "result_status": "has_error",
        "result_semantic_pass": False,
    }

    supervisor.publish_agent_completed(
        "GRO-3738", payload, topic="agent.result.rejected"
    )

    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT dedup_key, topic, payload_json, processed FROM events"
        ).fetchone()
    assert row is not None
    dedup_key, topic, payload_json, processed = row
    assert dedup_key == "agent.result.rejected:GRO-3738:default:1:1"
    assert topic == "agent.result.rejected"
    assert processed == 0
    event = json.loads(payload_json)
    assert event["type"] == "agent.result.rejected"
    assert event["payload"]["result_semantic_pass"] is False


def test_fetch_single_linear_issue_uses_team_key_and_number(monkeypatch):
    supervisor = _load_supervisor()
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "data": {
                        "issues": {
                            "nodes": [
                                {
                                    "id": "uuid",
                                    "identifier": "GRO-3738",
                                    "title": "task",
                                    "description": "current",
                                    "priority": 1,
                                    "state": {"name": "In Progress"},
                                    "labels": {"nodes": []},
                                }
                            ]
                        }
                    }
                }
            ).encode()

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        assert timeout == 15
        return Response()

    monkeypatch.setattr(supervisor, "_read_linear_api_key", lambda: "test-key")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    issue = supervisor.fetch_single_linear_issue("gro-3738")

    assert issue["identifier"] == "GRO-3738"
    assert captured["variables"] == {"teamKey": "GRO", "number": 3738.0}
    assert "issue(id:" not in captured["query"]


def test_fetch_single_linear_issue_uses_stable_uuid_and_verifies_identifier(
    monkeypatch,
):
    supervisor = _load_supervisor()
    captured = {}
    issue_uuid = "33e47ec2-77ff-4907-90ec-cc5c85a2bb07"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "data": {
                        "issue": {
                            "id": issue_uuid,
                            "identifier": "GRO-3738",
                            "title": "task",
                            "description": "current",
                            "priority": 1,
                            "state": {"name": "In Progress"},
                            "labels": {"nodes": []},
                        }
                    }
                }
            ).encode()

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        assert timeout == 15
        return Response()

    monkeypatch.setattr(supervisor, "_read_linear_api_key", lambda: "test-key")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    issue = supervisor.fetch_single_linear_issue("GRO-3738", issue_uuid=issue_uuid)

    assert issue["id"] == issue_uuid
    assert captured["variables"] == {"issueUuid": issue_uuid}
    assert "issue(id: $issueUuid)" in captured["query"]


def test_resolve_exact_task_prefers_live_linear_over_stale_cache(tmp_path):
    supervisor = _load_supervisor()
    (tmp_path / "GRO-3738.txt").write_text("STALE_CACHE_CONTRACT")
    issue = {
        "identifier": "GRO-3738",
        "title": "Current task",
        "description": "CURRENT_LINEAR_CONTRACT",
        "priority": 1,
        "state": {"name": "In Progress"},
        "labels": {"nodes": [{"name": "agent:agy"}]},
    }

    task = supervisor.resolve_exact_task("GRO-3738", fetch_issue=lambda _iid: issue)

    assert task["task_source"] == "linear_identifier"
    assert "CURRENT_LINEAR_CONTRACT" in task["task_content"]
    assert "STALE_CACHE_CONTRACT" not in task["task_content"]
    assert task["labels"] == {"agent:agy"}


def test_resolve_exact_task_explicit_file_overrides_fetch_and_workdir(tmp_path):
    supervisor = _load_supervisor()
    task_file = tmp_path / "task.md"
    task_file.write_text("WORKDIR: old\nLABELS: agent:agy\nEXPLICIT_PACKET")

    def forbidden_fetch(_identifier):
        raise AssertionError("explicit task file must prevent Linear fetch")

    task = supervisor.resolve_exact_task(
        "GRO-3738",
        task_file=task_file,
        workdir_override="/clean/current-main",
        fetch_issue=forbidden_fetch,
    )

    assert task["task_source"] == "explicit_task_file"
    assert task["workdir"] == "/clean/current-main"
    assert task["task_sha256"] == hashlib.sha256(task_file.read_bytes()).hexdigest()


def test_resolve_exact_task_refuses_mutable_cache_when_linear_fails(tmp_path):
    supervisor = _load_supervisor()
    cache = tmp_path / "GRO-3738.txt"
    cache.write_text("WORKDIR: current\nLABELS: agent:agy\nSTALE_CACHE")

    with pytest.raises(RuntimeError, match="refuses mutable cache fallback"):
        supervisor.resolve_exact_task("GRO-3738", fetch_issue=lambda _iid: None)

    assert cache.read_text().endswith("STALE_CACHE")


def _write_seed_manifest(tmp_path, entries):
    manifest = tmp_path / "repair-seed.json"
    manifest.write_text(json.dumps({"version": 1, "files": entries}, sort_keys=True))
    return manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_repair_seed_manifest_applies_hash_bound_files_atomically(tmp_path):
    supervisor = _load_supervisor()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    source = tmp_path / "candidate.py"
    source.write_text("CANDIDATE = True\n")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest, manifest_sha = _write_seed_manifest(
        tmp_path,
        [
            {
                "source": str(source),
                "destination": "plugins/pwp/candidate.py",
                "sha256": source_sha,
            }
        ],
    )

    seeded = supervisor.apply_repair_seed_manifest(sandbox, manifest, manifest_sha)

    assert seeded == ["plugins/pwp/candidate.py"]
    assert (sandbox / seeded[0]).read_bytes() == source.read_bytes()


def test_repair_seed_manifest_rejects_hash_mismatch_and_traversal_before_write(
    tmp_path,
):
    supervisor = _load_supervisor()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    source = tmp_path / "candidate.py"
    source.write_text("CANDIDATE = True\n")
    good_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest, manifest_sha = _write_seed_manifest(
        tmp_path,
        [
            {
                "source": str(source),
                "destination": "safe/candidate.py",
                "sha256": good_sha,
            },
            {
                "source": str(source),
                "destination": "../escape.py",
                "sha256": good_sha,
            },
        ],
    )

    with pytest.raises(RuntimeError, match="destination is unsafe"):
        supervisor.apply_repair_seed_manifest(sandbox, manifest, manifest_sha)
    assert not (sandbox / "safe/candidate.py").exists()
    assert not (tmp_path / "escape.py").exists()

    with pytest.raises(RuntimeError, match="manifest hash mismatch"):
        supervisor.apply_repair_seed_manifest(sandbox, manifest, "0" * 64)


def test_repair_seed_manifest_rejects_protected_and_normalized_duplicate_paths(
    tmp_path,
):
    supervisor = _load_supervisor()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    source = tmp_path / "candidate.py"
    source.write_text("CANDIDATE = True\n")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    protected, protected_sha = _write_seed_manifest(
        tmp_path,
        [
            {
                "source": str(source),
                "destination": "AGY_TASK.md",
                "sha256": source_sha,
            }
        ],
    )
    with pytest.raises(RuntimeError, match="protected control path"):
        supervisor.apply_repair_seed_manifest(sandbox, protected, protected_sha)

    protected_packet, protected_packet_sha = _write_seed_manifest(
        tmp_path,
        [
            {
                "source": str(source),
                "destination": "AGY_RESULT_PACKET.json",
                "sha256": source_sha,
            }
        ],
    )
    with pytest.raises(RuntimeError, match="protected control path"):
        supervisor.apply_repair_seed_manifest(
            sandbox, protected_packet, protected_packet_sha
        )

    duplicate, duplicate_sha = _write_seed_manifest(
        tmp_path,
        [
            {
                "source": str(source),
                "destination": "safe/candidate.py",
                "sha256": source_sha,
            },
            {
                "source": str(source),
                "destination": "safe//candidate.py",
                "sha256": source_sha,
            },
        ],
    )
    with pytest.raises(RuntimeError, match="destination is duplicated"):
        supervisor.apply_repair_seed_manifest(sandbox, duplicate, duplicate_sha)
    assert not (sandbox / "safe/candidate.py").exists()


def test_exact_task_cli_has_explicit_packet_and_repair_seed_contract():
    source = SUPERVISOR_PATH.read_text()

    assert '"--task-file"' in source
    assert '"--linear-issue-uuid"' in source
    assert '"--repair-seed-manifest"' in source
    assert '"--repair-seed-sha256"' in source
    assert "falling back to 16-byte placeholder" not in source
    assert 'task["repair_seed_manifest"]' in source
    assert "exact mode refuses mutable cache fallback" in source
    assert "has_done = False" in source
    assert "get_harness_db" not in source
    assert "harness_runs" not in source


def _persist_recovery_packet(tmp_path, packet=None, **overrides):
    from prismatic.agent_raw_output_queue import RawAgentOutputStore

    packet = packet or _valid_agy_packet()
    raw_text = json.dumps(packet)
    task_id = overrides.pop("task_id", "GRO-3837")
    digest = hashlib.sha256(raw_text.encode()).hexdigest()
    source_event_id = overrides.pop(
        "source_event_id", f"agy:{task_id}:attempt:1:sha256:{digest}"
    )
    db = tmp_path / "raw" / "raw.sqlite3"
    row = RawAgentOutputStore(db).persist(
        raw_text=raw_text,
        agent=overrides.pop("agent", "agy"),
        task_id=task_id,
        source_event_id=source_event_id,
        raw_text_or_artifact_path=overrides.pop(
            "raw_text_or_artifact_path", str(tmp_path / "AGY_RESULT_PACKET.json")
        ),
        expected_agent="agy",
        **overrides,
    )
    return db, row


def test_reconciliation_recovers_raw_agy_despite_queue_rejection(tmp_path):
    supervisor = _load_supervisor()
    raw_db, raw_row = _persist_recovery_packet(tmp_path)
    assert raw_row.normalization_status == "rejected_rerun_required"
    result = supervisor.reconcile_agy_raw_output(
        raw_output_id=raw_row.raw_output_id,
        raw_db_path=raw_db,
        completed_work_db_path=tmp_path / "completed" / "work.sqlite3",
        completed_work_evidence_dir=tmp_path / "completed" / "evidence",
        lease_owner="test-recovery",
    )
    assert result.status == "succeeded"
    assert result.completed_work_id.startswith("agy-cw-")
    assert result.recovery_marker == "AGY_RAW_OUTPUT_RECOVERY_OK"
    assert result.reconciliation_gate_marker == "AGY_RAW_OUTPUT_RECONCILIATION_GATE_OK"
    from prismatic.agent_raw_output_queue import RawAgentOutputStore

    delivery = RawAgentOutputStore(raw_db).get_delivery(raw_row.raw_output_id)
    assert delivery.completed_work_id == result.completed_work_id


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda packet: "not json", "digest_mismatch"),
        (lambda packet: json.dumps({"agent": "agy"}), "raw_dialect_invalid"),
    ],
)
def test_reconciliation_terminal_failures_are_safe(tmp_path, mutator, reason):
    supervisor = _load_supervisor()
    packet = _valid_agy_packet()
    raw_db, row = _persist_recovery_packet(tmp_path, packet)
    if reason == "digest_mismatch":
        with sqlite3.connect(raw_db) as conn:
            conn.execute(
                "UPDATE agent_raw_output_queue SET raw_text=? WHERE raw_output_id=?",
                (mutator(packet), row.raw_output_id),
            )
    else:
        replacement = mutator(packet)
        digest = hashlib.sha256(replacement.encode()).hexdigest()
        with sqlite3.connect(raw_db) as conn:
            conn.execute(
                "UPDATE agent_raw_output_queue SET raw_text=?, source_event_id=? WHERE raw_output_id=?",
                (
                    replacement,
                    f"agy:GRO-3837:attempt:1:sha256:{digest}",
                    row.raw_output_id,
                ),
            )
    result = supervisor.reconcile_agy_raw_output(
        raw_output_id=row.raw_output_id,
        raw_db_path=raw_db,
        completed_work_db_path=tmp_path / "completed.sqlite3",
        completed_work_evidence_dir=tmp_path / "evidence",
        lease_owner="test-terminal",
    )
    assert result.status == "terminal_failed"
    assert result.reason == reason
    assert result.recovery_marker is None
    assert result.reconciliation_gate_marker is None
    assert result.runtime_marker is None
    assert "raw_text" not in result.as_dict()


def test_reconciliation_post_claim_race_reloads_safe_succeeded_metadata(
    tmp_path, monkeypatch
):
    supervisor = _load_supervisor()
    raw_db, row = _persist_recovery_packet(tmp_path)
    completed_db = tmp_path / "completed" / "work.sqlite3"
    evidence = tmp_path / "completed" / "evidence"
    first = supervisor.reconcile_agy_raw_output(
        raw_output_id=row.raw_output_id,
        raw_db_path=raw_db,
        completed_work_db_path=completed_db,
        completed_work_evidence_dir=evidence,
        lease_owner="first",
    )
    assert first.status == "succeeded"

    from prismatic.agent_raw_output_queue import RawAgentOutputStore

    real_get = RawAgentOutputStore.get_delivery
    calls = []

    def raced_get(store, raw_output_id):
        delivery = real_get(store, raw_output_id)
        calls.append(raw_output_id)
        if len(calls) == 1:
            return replace(delivery, status="pending", completed_work_id=None)
        return delivery

    monkeypatch.setattr(RawAgentOutputStore, "get_delivery", raced_get)
    monkeypatch.setattr(RawAgentOutputStore, "claim_delivery", lambda *a, **k: None)
    raced = supervisor.reconcile_agy_raw_output(
        raw_output_id=row.raw_output_id,
        raw_db_path=raw_db,
        completed_work_db_path=completed_db,
        completed_work_evidence_dir=evidence,
        lease_owner="raced",
    )
    assert raced.status == "succeeded"
    assert raced.completed_work_id == first.completed_work_id
    assert raced.ingestion_marker == "AGY_COMPLETED_WORK_INGESTION_OK"
    assert raced.integration_marker == "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"
    assert raced.recovery_marker == "AGY_RAW_OUTPUT_RECOVERY_OK"


def test_stale_failure_cas_never_reports_terminal_or_retry_success():
    supervisor = _load_supervisor()

    class StaleStore:
        @staticmethod
        def mark_delivery_failed(*args, **kwargs):
            return False

    class Claim:
        raw_output_id = "raw-stale"
        retry_count = 0

    terminal = supervisor._terminal_reconciliation(
        StaleStore(), Claim(), "raw_json_invalid", "malformed", None
    )
    retry = supervisor._retry_reconciliation(
        StaleStore(), Claim(), "completed_work_storage_failed", None
    )
    assert terminal.status == terminal.reason == "stale_claim"
    assert retry.status == retry.reason == "stale_claim"
    assert terminal.recovery_marker is None
    assert retry.recovery_marker is None


def test_cross_database_crash_replays_same_completed_work(tmp_path, monkeypatch):
    supervisor = _load_supervisor()
    raw_db, row = _persist_recovery_packet(tmp_path)
    completed_db = tmp_path / "completed" / "work.sqlite3"
    evidence = tmp_path / "completed" / "evidence"
    from prismatic.agent_raw_output_queue import RawAgentOutputStore

    real_mark = RawAgentOutputStore.mark_delivery_succeeded
    monkeypatch.setattr(
        RawAgentOutputStore, "mark_delivery_succeeded", lambda *a, **k: False
    )
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    first = supervisor.reconcile_agy_raw_output(
        raw_output_id=row.raw_output_id,
        raw_db_path=raw_db,
        completed_work_db_path=completed_db,
        completed_work_evidence_dir=evidence,
        lease_owner="crashing",
        now=now,
    )
    assert first.status == "stale_claim"
    monkeypatch.setattr(RawAgentOutputStore, "mark_delivery_succeeded", real_mark)
    second = supervisor.reconcile_agy_raw_output(
        raw_output_id=row.raw_output_id,
        raw_db_path=raw_db,
        completed_work_db_path=completed_db,
        completed_work_evidence_dir=evidence,
        lease_owner="replay",
        now=now + timedelta(seconds=61),
    )
    assert second.status == "succeeded"
    with sqlite3.connect(completed_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agy_completed_work").fetchone() == (
            1,
        )


def test_recovery_batch_has_no_external_completion_or_writeback_side_effects(
    tmp_path, monkeypatch
):
    supervisor = _load_supervisor()
    raw_db, _row = _persist_recovery_packet(tmp_path)
    completed_db = tmp_path / "completed" / "work.sqlite3"
    evidence = tmp_path / "completed" / "evidence"
    monkeypatch.setattr(supervisor, "agy_raw_output_db_path", lambda: raw_db)
    monkeypatch.setattr(supervisor, "agy_completed_work_db_path", lambda: completed_db)
    monkeypatch.setattr(supervisor, "agy_completed_work_evidence_dir", lambda: evidence)

    def forbidden(*args, **kwargs):
        raise AssertionError("recovery attempted an external side effect")

    monkeypatch.setattr(supervisor, "publish_agent_completed", forbidden)
    monkeypatch.setattr(supervisor, "linear_update_issue", forbidden)
    monkeypatch.setattr(supervisor, "linear_comment", forbidden)
    monkeypatch.setattr(supervisor.subprocess, "Popen", forbidden)
    results = supervisor.run_agy_raw_recovery_batch(
        lease_owner="side-effect-proof", limit=10
    )
    assert len(results) == 1
    assert results[0].status == "succeeded"
    assert results[0].recovery_marker == "AGY_RAW_OUTPUT_RECOVERY_OK"


def test_startup_recovery_precedes_worker_start():
    source = SUPERVISOR_PATH.read_text(encoding="utf-8")
    recovery = 'run_agy_raw_recovery_batch(lease_owner=f"startup-{os.getpid()}"'
    assert source.index(recovery) < source.index("supervisor.start_workers()")


def test_watchdog_recovery_precedes_linear_fetch(monkeypatch):
    supervisor = _load_supervisor()
    order = []
    monkeypatch.setattr(
        supervisor,
        "run_agy_raw_recovery_batch",
        lambda **kwargs: order.append("recovery") or (),
    )

    class Linear:
        def fetch_issues(self, strict_opt_in=False):
            order.append("linear")
            stop.set()
            return []

    class Stub:
        linear_client = Linear()
        completed_issues = set()
        lane_mode = "off"
        active_project = None
        backlog_age_days = 0

    stop = threading.Event()
    supervisor.linear_watchdog_loop(Stub(), stop)
    assert order == ["recovery", "linear"]
