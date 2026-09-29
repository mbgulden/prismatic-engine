"""Spike tests for prismatic.harnesses.cli_json.SubprocessJSONHarness.

Proves the adapter mechanics with fake CLIs: status mapping, timeout kills,
and — the point of the spike — that receipts are built by the INDEPENDENT
verifier, never by trusting the CLI (the liar test).
"""

import os
import sys

import pytest

from prismatic.execution_evidence import VerificationStatus
from prismatic.harnesses.base import HarnessStatus
from prismatic.harnesses.cli_json import (
    SubprocessJSONHarness,
    build_task_envelope,
    canonical_task_id,
)

FAKECLIS = os.path.join(os.path.dirname(__file__), "fakeclis")
PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
VERIFIER = [
    sys.executable,
    "-c",
    "import json;assert json.load(open('result.json'))['primes']==[2,3,5,7,11,13,17,19,23,29]",
]


def make_harness(cli, run_dir, **kw):
    cfg = {
        "name": f"test-{cli}",
        "argv": [sys.executable, os.path.join(FAKECLIS, cli)],
        "run_dir": run_dir,
        "timeout_s": 30,
    }
    cfg.update(kw)
    return SubprocessJSONHarness(cfg)


def test_task_id_stable():
    a = canonical_task_id("p", "/w", VERIFIER)
    b = canonical_task_id("p", "/w", VERIFIER)
    c = canonical_task_id("p2", "/w", VERIFIER)
    assert a == b and a != c and len(a) == 16


def test_envelope_protocol():
    env = build_task_envelope("do it", "/tmp/w", verifier=VERIFIER)
    assert env["protocol"] == "prismatic.cli-json/1"
    assert env["prompt"] == "do it" and env["verifier"] == VERIFIER


def test_requires_argv(tmp_path):
    with pytest.raises(ValueError):
        SubprocessJSONHarness({"run_dir": str(tmp_path)})


def test_requires_prompt(tmp_path):
    h = make_harness("fake_ok.py", str(tmp_path))
    with pytest.raises(ValueError):
        h.dispatch({})


def test_happy_path_verified(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    h = make_harness("fake_ok.py", str(tmp_path / "runs"))
    run_id = h.dispatch(
        {"prompt": "write primes", "workdir": str(workdir), "verifier": VERIFIER}
    )
    st = h.status(run_id)
    assert st["status"] == HarnessStatus.COMPLETED.value
    assert st["completed_at"] is not None
    receipt = h.receipt(run_id)
    assert receipt.status == VerificationStatus.VERIFIED, receipt.summary
    assert "independent verifier PASS" in receipt.summary
    assert receipt.to_dict()["status"] == "verified"
    logs = h.logs(run_id)
    assert any("result.json" in line for line in logs)
    cost = h.cost(run_id)
    assert cost == {"tokens_in": None, "tokens_out": None, "dollars": None}
    assert h.health()["status"] == "ok"
    assert "cli-json" in h.capabilities().extra["protocol"]


def test_liar_claims_done_but_verifier_fails(tmp_path):
    """The money test: CLI says done, did nothing -> receipt FAILED, never trusted."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    h = make_harness("fake_liar.py", str(tmp_path / "runs"))
    run_id = h.dispatch(
        {"prompt": "write primes", "workdir": str(workdir), "verifier": VERIFIER}
    )
    assert h.status(run_id)["status"] == HarnessStatus.COMPLETED.value
    receipt = h.receipt(run_id)
    assert receipt.status == VerificationStatus.FAILED, receipt.summary
    assert receipt.failure_category.value == "verification_failed"
    assert receipt.status != VerificationStatus.SELF_REPORTED


def test_crash_maps_to_failed(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    h = make_harness("fake_crash.py", str(tmp_path / "runs"))
    run_id = h.dispatch({"prompt": "x", "workdir": str(workdir), "verifier": VERIFIER})
    assert h.status(run_id)["status"] == HarnessStatus.FAILED.value
    receipt = h.receipt(run_id)
    assert receipt.status == VerificationStatus.FAILED
    assert receipt.failure_category.value == "tooling_error"


def test_hang_times_out_and_kills(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    h = make_harness("fake_hang.py", str(tmp_path / "runs"), timeout_s=2)
    run_id = h.dispatch(
        {"prompt": "x", "workdir": str(workdir), "timeout_s": 2, "verifier": VERIFIER}
    )
    st = h.status(run_id)
    assert st["status"] == HarnessStatus.TIMEOUT.value
    receipt = h.receipt(run_id)
    assert receipt.status == VerificationStatus.FAILED
    assert receipt.failure_category.value == "timeout"


def test_exit_zero_garbage_stdout_is_protocol_failure(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    h = make_harness("fake_garbage.py", str(tmp_path / "runs"))
    run_id = h.dispatch({"prompt": "x", "workdir": str(workdir), "verifier": VERIFIER})
    # exit 0 but no protocol JSON -> adapter must not trust it
    assert h.status(run_id)["status"] == HarnessStatus.FAILED.value


def test_no_verifier_never_verified(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    h = make_harness("fake_ok.py", str(tmp_path / "runs"))
    run_id = h.dispatch({"prompt": "write primes", "workdir": str(workdir)})
    receipt = h.receipt(run_id)
    assert receipt.status == VerificationStatus.PARTIALLY_VERIFIED
    assert receipt.status != VerificationStatus.SELF_REPORTED
    assert receipt.status != VerificationStatus.VERIFIED


def test_cancel_unknown_run_false(tmp_path):
    h = make_harness("fake_ok.py", str(tmp_path / "runs"))
    assert h.cancel("nope-not-a-run") is False
    with pytest.raises(KeyError):
        h.status("nope-not-a-run")


def test_missing_binary_fails_cleanly(tmp_path):
    h = SubprocessJSONHarness(
        {
            "name": "missing",
            "argv": ["/nonexistent/cli-binary-xyz"],
            "run_dir": str(tmp_path / "runs"),
        }
    )
    run_id = h.dispatch({"prompt": "x", "workdir": str(tmp_path)})
    assert h.status(run_id)["status"] == HarnessStatus.FAILED.value
    assert "spawn failed" in h.status(run_id)["error"]
