from __future__ import annotations

import importlib.util
import json
import os
import pwd
import sqlite3
from pathlib import Path


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


def test_agy_launch_and_relaunch_paths_use_child_env_static_guard():
    source = SUPERVISOR_PATH.read_text()

    assert (
        'env={**os.environ, "HOME": os.environ.get("HOME", str(Path.home()))}'
        not in source
    )
    assert source.count("env=agy_cli_child_env(),") >= 3
    assert "proc = subprocess.Popen(" in source
    assert "Relaunching with cmd" in source


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


def test_quality_gate_and_partial_linear_done_are_semantically_guarded():
    source = SUPERVISOR_PATH.read_text(encoding="utf-8")

    assert 'and result.get("has_done")' in source
    assert 'and result_semantics["passed"]' in source
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
