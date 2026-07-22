from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import sqlite3
import urllib.request
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
