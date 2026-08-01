from __future__ import annotations

import hashlib
import json
import shutil
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from prismatic.agy_cli import (
    AGY_UNBOUNDED_PRINT_TIMEOUT,
    CANONICAL_AGY_WORKFLOW_VERSION,
    CANONICAL_RESULT_MARKER,
    AgyLaunchSpec,
    AgyWorkflowError,
    canonical_contract,
    launch_tmux,
    wait_tmux,
)
from prismatic.cli import run


def _write(path: Path, text: str, mode: int = 0o600) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def _fixture(tmp_path: Path) -> tuple[AgyLaunchSpec, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    task_dir = tmp_path / "task"
    task_dir.mkdir(mode=0o700)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    logs = workspace / "logs"
    logs.mkdir(mode=0o700)
    agy_home = tmp_path / "agy-home"
    agy_home.mkdir(mode=0o700)
    task = _write(task_dir / "TASK.md", "Only create PLAN.md and RESULT.md.\n")
    fake = _write(
        tmp_path / "agy-fake",
        """#!/usr/bin/env python3
import json, os, pathlib, re, sys
args = sys.argv[1:]
prompt = args[args.index('--print') + 1]
plan = re.search(r'plan to (.+?)\\. Then execute', prompt).group(1)
result = re.search(r'final result to (.+?) with marker', prompt).group(1)
pathlib.Path(plan).write_text('PLAN FIRST\\n')
pathlib.Path(result).write_text('PRISMATIC_AGY_RESULT_V1\\nRESULT OK\\n')
diag = pathlib.Path(args[args.index('--log-file') + 1])
diag.write_text(json.dumps({'argv': args, 'home': os.environ.get('HOME'), 'linear_secret': os.environ.get('LINEAR_API_KEY')}) + '\\n')
print('FAKE_AGY_DONE')
""",
        0o500,
    )
    digest = hashlib.sha256(fake.read_bytes()).hexdigest()
    spec = AgyLaunchSpec(
        identifier="TST-AGY-1",
        task_file=str(task),
        workspace=str(workspace),
        artifact_root=str(workspace),
        result_path=str(workspace / "RESULT.md"),
        plan_path=str(workspace / "PLAN.md"),
        stdout_path=str(logs / "stdout.log"),
        stderr_path=str(logs / "stderr.log"),
        diagnostics_path=str(logs / "agy.log"),
        agy_binary=str(fake),
        agy_binary_sha256=digest,
        agy_home=str(agy_home),
    )
    return spec, runtime


def _admission(spec: AgyLaunchSpec, tmp_path: Path, attempt: int = 1) -> Path:
    return _write(
        tmp_path / f"admission-{attempt}.json",
        json.dumps(
            {
                "marker": "PRISMATIC_AGY_ADMISSION_V1",
                "authorized": True,
                "event_id": "task-admission:test",
                "attempt": attempt,
                "attempt_token": "a" * 64,
                "task_sha256": spec.manifest()["task_sha256"],
                "agy_binary_sha256": spec.agy_binary_sha256,
            }
        ),
    )


def test_contract_keeps_canonical_controls_at_forefront():
    contract = canonical_contract()
    assert contract["workflow_version"] == CANONICAL_AGY_WORKFLOW_VERSION
    assert contract["transport"] == "tmux-durable-anchor"
    assert contract["prompt_prefix"] == "/goal "
    assert "raw detached AGY Popen" in contract["forbidden"]
    assert "producer self-acceptance" in contract["forbidden"]


def test_manifest_is_hash_bound_and_external_write_fail_closed(tmp_path: Path):
    spec, _runtime = _fixture(tmp_path)
    manifest = spec.manifest()
    assert manifest["goal_prompt"].startswith("/goal ")
    assert len(manifest["goal_prompt"]) <= 1200
    assert manifest["argv"][1] == "--print"
    assert "--dangerously-skip-permissions" in manifest["argv"]
    timeout_index = manifest["argv"].index("--print-timeout")
    assert manifest["argv"][timeout_index + 1] == AGY_UNBOUNDED_PRINT_TIMEOUT
    assert "timeout" not in manifest
    assert manifest["external_writes_allowed"] is False
    assert manifest["checkpoint_commits_allowed"] is False
    assert (
        manifest["task_sha256"]
        == hashlib.sha256(Path(spec.task_file).read_bytes()).hexdigest()
    )


def test_binary_digest_drift_fails_closed(tmp_path: Path):
    spec, _runtime = _fixture(tmp_path)
    with pytest.raises(AgyWorkflowError, match="SHA-256 mismatch"):
        replace(spec, agy_binary_sha256="0" * 64).validate()


def test_unified_cli_exposes_canonical_contract(capsys: pytest.CaptureFixture[str]):
    assert run(["agy", "contract"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["transport"] == "tmux-durable-anchor"
    assert payload["result_marker"] == CANONICAL_RESULT_MARKER
    assert payload["runtime_deadline"] is None
    assert payload["runtime_policy"] == "no-wall-clock-cap-progress-supervised"
    assert payload["agy_print_timeout_protocol_bridge"] == AGY_UNBOUNDED_PRINT_TIMEOUT


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_tmux_launch_result_and_exact_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("LINEAR_API_KEY", "must-not-reach-agy")
    spec, runtime = _fixture(tmp_path)
    admission = _admission(spec, tmp_path)
    receipt = launch_tmux(spec, runtime, admission_receipt=admission)
    receipt_path = runtime / spec.identifier / "launch-receipt.json"
    assert receipt_path.is_file()
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    result = wait_tmux(receipt_path)
    assert result["exit_code"] == 0
    assert result["cleanup_verified"] is True
    assert result["result_exists"] is True
    activity = json.loads((runtime / spec.identifier / "activity.json").read_text())
    assert activity["runtime_deadline"] is None
    assert activity["automatic_kill"] is False
    assert activity["classification"] == "terminal"
    assert activity["sequence"] >= 1
    assert Path(spec.result_path).read_text().startswith(CANONICAL_RESULT_MARKER)
    assert Path(spec.plan_path).read_text() == "PLAN FIRST\n"
    assert Path(spec.stdout_path).read_text().strip() == "FAKE_AGY_DONE"
    assert Path(spec.stderr_path).read_text() == ""
    diagnostics = json.loads(Path(spec.diagnostics_path).read_text())
    assert diagnostics["argv"]
    assert diagnostics["home"] == spec.agy_home
    assert diagnostics["linear_secret"] is None
    assert (
        __import__("subprocess")
        .run(
            ["/usr/bin/tmux", "has-session", "-t", receipt["session"]],
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
            check=False,
        )
        .returncode
        != 0
    )


def test_launch_requires_explicit_execute_gate(tmp_path: Path):
    spec, runtime = _fixture(tmp_path)
    admission = _admission(spec, tmp_path)
    args = [
        "agy",
        "launch",
        "--identifier",
        spec.identifier,
        "--task-file",
        spec.task_file,
        "--workspace",
        spec.workspace,
        "--artifact-root",
        spec.artifact_root,
        "--result-path",
        spec.result_path,
        "--plan-path",
        spec.plan_path,
        "--stdout-path",
        spec.stdout_path,
        "--stderr-path",
        spec.stderr_path,
        "--diagnostics-path",
        spec.diagnostics_path,
        "--agy-binary",
        spec.agy_binary,
        "--agy-binary-sha256",
        spec.agy_binary_sha256,
        "--agy-home",
        spec.agy_home,
        "--runtime-dir",
        str(runtime),
        "--admission-receipt",
        str(admission),
    ]
    with pytest.raises(AgyWorkflowError, match="requires --execute"):
        run(args)
