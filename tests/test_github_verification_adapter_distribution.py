"""Distribution and package build test for GitHub verification adapter."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def _find_build_python() -> str:
    # Try sys.executable first, or venv_stable python if sys.executable lacks build module
    for cand in [
        sys.executable,
        "/home/ubuntu/.prismatic/venv_stable/bin/python",
        shutil.which("python3"),
    ]:
        if cand and os.path.exists(cand):
            res = subprocess.run(
                [cand, "-c", "import build"],
                capture_output=True,
            )
            if res.returncode == 0:
                return cand
    return sys.executable


def test_built_wheel_contains_adapter_and_imports_cleanly(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    py_bin = _find_build_python()

    # Build wheel using build module
    res = subprocess.run(
        [py_bin, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Build failed: {res.stderr}\n{res.stdout}"

    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel file in {dist_dir}, found {wheels}"
    wheel_path = wheels[0]

    # Verify adapter module file exists inside wheel zip archive
    with zipfile.ZipFile(wheel_path, "r") as zf:
        namelist = zf.namelist()
        assert "prismatic/verification/github_adapter.py" in namelist

    # Install wheel to target directory inside tmp_path
    target_site = tmp_path / "site-packages"
    target_site.mkdir(parents=True, exist_ok=True)

    res_install = subprocess.run(
        [py_bin, "-m", "pip", "install", str(wheel_path), "--target", str(target_site)],
        capture_output=True,
        text=True,
    )
    assert res_install.returncode == 0, f"Pip install failed: {res_install.stderr}"

    empty_cwd = tmp_path / "empty_cwd"
    empty_cwd.mkdir(parents=True, exist_ok=True)

    inline_script = """
import hashlib, hmac, json
from prismatic.verification.github_adapter import (
    normalize_github_trigger,
    project_github_check_run,
)

payload = {
    "action": "opened",
    "repository": {"id": 123, "full_name": "org/repo", "node_id": "R_node123"},
    "pull_request": {
        "number": 1,
        "base": {"ref": "main", "sha": "a" * 40, "repo": {"id": 123, "full_name": "org/repo", "node_id": "R_node123"}},
        "head": {"ref": "feat", "sha": "b" * 40, "repo": {"id": 123, "full_name": "org/repo", "node_id": "R_node123"}},
    },
}
body = json.dumps(payload).encode("utf-8")
sig = "sha256=" + hmac.new(b"sec", body, hashlib.sha256).hexdigest()
headers = {"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d1"}

trig = normalize_github_trigger(
    body, headers, secrets=["sec"], expected_repository_full_name="org/repo", repository_id="123"
)
assert trig.task_id == f"github:pr:123:1:{('b' * 40)}"

policy = {
    "schema_version": "1.0",
    "policy_id": "p1",
    "policy_version": "1.0.0",
    "status": "active",
    "repository": {
        "repository_id": "123",
        "source_requirements": {
            "require_full_git_objects": True,
            "allowed_source_kinds": ["provider_remote", "local_bare_repository"],
            "allowed_source_providers": ["github", "local", "none"],
        },
    },
    "approved_backends": [{"id": "cleanroom-v1", "class": "self_hosted_clean_room"}],
    "approved_verifiers": {
        "identities": [{"id": "v1"}],
        "require_producer_verifier_separation": False,
    },
    "freshness": {"max_age_seconds": 3600},
}
receipt = {
    "schema_version": "1.0",
    "policy_id": "p1",
    "policy_version": "1.0.0",
    "task_id": trig.task_id,
    "repository_id": "123",
    "base_sha": "a" * 40,
    "candidate_sha": "b" * 40,
    "tree_sha": "c" * 40,
    "source_kind": "provider_remote",
    "source_provider": "github",
    "source_locator": "github://org/repo",
    "backend_id": "cleanroom-v1",
    "backend_class": "self_hosted_clean_room",
    "producer_id": "p1",
    "verifier_id": "v1",
    "started_at": "2026-07-25T00:00:00Z",
    "completed_at": "2026-07-25T00:01:00Z",
    "expires_at": "2026-07-25T01:00:00Z",
    "decision": {"status": "pass", "merge_eligible": True},
    "provider_metadata": {"pull_request_id": "1"},
    "changed_paths": ["a.py"],
    "commands_and_exit_states": [],
    "proof_classes": [],
    "logs_and_digests": [],
    "artifacts_and_digests": [],
    "required_evidence": [],
    "signature_or_attestation": {
        "type": "attestation",
        "algorithm": "ed25519",
        "key_id": "v1",
        "value": "",
    },
}

proj = project_github_check_run(trig, receipt, policy, expected_tree_sha="c" * 40)
assert proj.status == "completed"
print("DISTRIBUTION_VERIFICATION_PASS")
"""

    env = os.environ.copy()
    env["PYTHONPATH"] = str(target_site)

    res_exec = subprocess.run(
        [py_bin, "-c", inline_script],
        cwd=str(empty_cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_exec.returncode == 0, f"Execution failed: {res_exec.stderr}"
    assert "DISTRIBUTION_VERIFICATION_PASS" in res_exec.stdout
