"""Tests for the triage action executors (Phase 1b: retry only).

``execute_retry`` is fail-closed and idempotent: the flag is default-off,
only deterministic "retry" verdicts fire (Jev-advised retry stays shadow),
the same run_id is never retried twice, and runs with run_attempt > 1 are
never retried (the rerun re-triggers the triage workflow on the same id).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from prismatic.review_factory.triage_actions import (
    ActionResult,
    RetriedRunStore,
    RetryRequest,
    RetryStateError,
    default_retry_store,
    execute_retry,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DRIVER = REPO_ROOT / "scripts" / "failure_triage_shadow.py"
FLAG = "PRISMATIC_TRIAGE_RETRY_ENABLED"


def _req(**over) -> RetryRequest:
    base = dict(
        repo="octo/repo",
        run_id="123",
        failure_id="ci:123:456",
        deterministic_verdict="retry",
        deterministic_evidence="known-transient rule 'runner-lost'",
    )
    base.update(over)
    return RetryRequest(**base)


class FakeGh:
    """Injectable stand-in for the gh CLI."""

    def __init__(self, run_attempt: int = 1, fail_on: str | None = None):
        self.calls: list[tuple[str, str]] = []
        self.run_attempt = run_attempt
        self.fail_on = fail_on  # "describe" | "rerun" | None

    def __call__(self, path: str, *, method: str = "GET") -> dict:
        self.calls.append((method, path))
        if self.fail_on == "describe" and method == "GET":
            raise RuntimeError("boom: describe")
        if self.fail_on == "rerun" and method == "POST":
            raise RuntimeError("boom: rerun")
        if method == "POST":
            assert "rerun-failed-jobs" in path, path
            return {}
        return {"id": 123, "run_attempt": self.run_attempt}

    def posts(self) -> int:
        return sum(1 for m, _ in self.calls if m == "POST")


def _audit_rows(audit: Path) -> list[dict]:
    if not audit.exists():
        return []
    return [json.loads(line) for line in audit.read_text().splitlines()]


def _action_rows(audit: Path) -> list[dict]:
    return [r for r in _audit_rows(audit) if r.get("component") == "triage-actions"]


# ── flag gating ──────────────────────────────────────────────────────


def test_retry_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    gh = FakeGh()
    res = execute_retry(
        _req(),
        gh=gh,
        store=RetriedRunStore(tmp_path / "s.json"),
        audit_log=tmp_path / "a.jsonl",
    )
    assert res.executed is False
    assert res.reason == "flag_off"
    assert gh.calls == []  # no GitHub calls at all


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
def test_flag_truthy_enables(tmp_path, monkeypatch, value):
    monkeypatch.setenv(FLAG, value)
    gh = FakeGh()
    res = execute_retry(
        _req(),
        gh=gh,
        store=RetriedRunStore(tmp_path / "s.json"),
        audit_log=tmp_path / "a.jsonl",
    )
    assert res.executed is True, res


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_flag_falsy_disables(tmp_path, monkeypatch, value):
    monkeypatch.setenv(FLAG, value)
    gh = FakeGh()
    res = execute_retry(
        _req(),
        gh=gh,
        store=RetriedRunStore(tmp_path / "s.json"),
        audit_log=tmp_path / "a.jsonl",
    )
    assert res.executed is False
    assert res.reason == "flag_off"
    assert gh.calls == []


# ── happy path + idempotency ──────────────────────────────────────────


def test_retry_executes_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    gh = FakeGh()
    audit = tmp_path / "a.jsonl"
    store = RetriedRunStore(tmp_path / "s.json")
    res = execute_retry(_req(), gh=gh, store=store, audit_log=audit)
    assert isinstance(res, ActionResult)
    assert res.executed is True
    assert res.reason == "ok"
    assert gh.posts() == 1
    assert store.has("123")
    rows = _action_rows(audit)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "retry_executed"
    assert row["marker"] == "triage-action"
    assert row["run_id"] == "123"


def test_retry_idempotent_same_run_twice(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    gh = FakeGh()
    store = RetriedRunStore(tmp_path / "s.json")
    audit = tmp_path / "a.jsonl"
    first = execute_retry(_req(), gh=gh, store=store, audit_log=audit)
    second = execute_retry(_req(), gh=gh, store=store, audit_log=audit)
    assert first.executed is True
    assert second.executed is False
    assert second.reason == "already_retried"
    assert gh.posts() == 1  # the rerun POST happened exactly once


def test_retry_skips_run_attempt_gt_1(tmp_path, monkeypatch):
    # The rerun re-triggers triage on the SAME run id with run_attempt
    # incremented: without this guard the executor would loop forever.
    monkeypatch.setenv(FLAG, "1")
    gh = FakeGh(run_attempt=2)
    res = execute_retry(
        _req(),
        gh=gh,
        store=RetriedRunStore(tmp_path / "s.json"),
        audit_log=tmp_path / "a.jsonl",
    )
    assert res.executed is False
    assert res.reason == "run_attempt_gt_1"
    assert gh.posts() == 0


# ── verdict gating ───────────────────────────────────────────────────


@pytest.mark.parametrize("verdict", [None, "escalate", "repair", "reject"])
def test_only_deterministic_retry_fires(tmp_path, monkeypatch, verdict):
    monkeypatch.setenv(FLAG, "1")
    gh = FakeGh()
    res = execute_retry(
        _req(deterministic_verdict=verdict),
        gh=gh,
        store=RetriedRunStore(tmp_path / "s.json"),
        audit_log=tmp_path / "a.jsonl",
    )
    assert res.executed is False
    assert res.reason == "not_deterministic_retry"
    assert gh.calls == []  # Jev-advised retry never reaches the API


def test_invalid_run_id_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    gh = FakeGh()
    res = execute_retry(
        _req(run_id="../../etc"),
        gh=gh,
        store=RetriedRunStore(tmp_path / "s.json"),
        audit_log=tmp_path / "a.jsonl",
    )
    assert res.executed is False
    assert res.reason == "invalid_run_id"
    assert gh.calls == []


# ── fail-closed ──────────────────────────────────────────────────────


def test_gh_error_is_audit_row_not_exception(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    gh = FakeGh(fail_on="rerun")
    audit = tmp_path / "a.jsonl"
    res = execute_retry(
        _req(), gh=gh, store=RetriedRunStore(tmp_path / "s.json"), audit_log=audit
    )
    assert res.executed is False
    assert res.reason == "gh_error"
    assert "boom" in res.error
    rows = _action_rows(audit)
    assert rows and rows[0]["action"] == "retry_failed"


def test_describe_error_is_audit_row_not_exception(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    gh = FakeGh(fail_on="describe")
    res = execute_retry(
        _req(),
        gh=gh,
        store=RetriedRunStore(tmp_path / "s.json"),
        audit_log=tmp_path / "a.jsonl",
    )
    assert res.executed is False
    assert res.reason == "gh_error"
    assert gh.posts() == 0  # never got as far as the rerun


def test_corrupt_state_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    state = tmp_path / "s.json"
    state.write_text("{not json")
    gh = FakeGh()
    res = execute_retry(
        _req(), gh=gh, store=RetriedRunStore(state), audit_log=tmp_path / "a.jsonl"
    )
    assert res.executed is False
    assert res.reason == "state_error"
    assert gh.calls == []  # no API call with unreadable idempotency state


def test_store_roundtrip(tmp_path):
    store = RetriedRunStore(tmp_path / "sub" / "s.json")
    assert not store.has("123")
    store.record("123", {"outcome": "rerun_requested"})
    assert store.has("123")
    # corrupt file raises on has()
    bad = tmp_path / "bad.json"
    bad.write_text("garbage")
    with pytest.raises(RetryStateError):
        RetriedRunStore(bad).has("123")


def test_default_retry_store_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_TRIAGE_STATE_DIR", str(tmp_path))
    store = default_retry_store()
    assert store.path == tmp_path / "triage-retry-state.json"


# ── driver --mode tests (subprocess, fake gh CLI) ────────────────────

FAKE_GH = """#!/bin/sh
# Fake gh: transient-failing job, run_attempt 1, counts rerun POSTs.
case "$*" in
  *rerun-failed-jobs*)
    echo x >> "$FAKE_GH_MARKERS/rerun-count"
    echo '{}'
    ;;
  */actions/runs/123/jobs*)
    echo '{"jobs": [{"id": 456, "name": "smoke", "conclusion": "failure", "head_sha": "abc123", "steps": [{"name": "run tests", "conclusion": "failure"}]}]}'
    ;;
  */actions/jobs/456/logs*)
    echo 'The runner has lost contact with the server mid-job'
    ;;
  */actions/runs/123*)
    echo '{"id": 123, "run_attempt": 1}'
    ;;
  *)
    echo "unexpected gh call: $*" >&2
    exit 1
    ;;
esac
"""

EVENT = {"workflow_run": {"id": 123, "name": "test", "conclusion": "failure"}}


def _run_driver(tmp_path, monkeypatch, *argv, env=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    markers = tmp_path / "markers"
    markers.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_GH_MARKERS", str(markers))
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/repo")
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(EVENT))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    audit = tmp_path / "audit.jsonl"
    proc = subprocess.run(
        [sys.executable, str(DRIVER), "--audit-log", str(audit), *argv],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    rows = _audit_rows(audit)
    count_file = markers / "rerun-count"
    posts = len(count_file.read_text().splitlines()) if count_file.exists() else 0
    return proc, rows, posts


def test_driver_shadow_mode_ignores_flag(tmp_path, monkeypatch):
    # Shadow (explicit) + flag ON: still no retry — shadow is observe-only.
    proc, rows, posts = _run_driver(
        tmp_path,
        monkeypatch,
        "--mode",
        "shadow",
        "--retry-state-path",
        str(tmp_path / "retry-state.json"),
        env={FLAG: "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert posts == 0
    action_rows = [r for r in rows if r.get("component") == "triage-actions"]
    assert all(r["action"] != "retry_executed" for r in action_rows)
    assert "[shadow \u2014 no action taken]" in proc.stdout


def test_driver_active_mode_flag_off_behaves_like_shadow(tmp_path, monkeypatch):
    proc, rows, posts = _run_driver(
        tmp_path,
        monkeypatch,
        "--mode",
        "active",
        "--retry-state-path",
        str(tmp_path / "retry-state.json"),
    )
    assert proc.returncode == 0, proc.stderr
    assert posts == 0
    action_rows = [r for r in rows if r.get("component") == "triage-actions"]
    assert any(r["reason"] == "flag_off" for r in action_rows)


def test_driver_active_mode_retries_transient_failure(tmp_path, monkeypatch):
    state = tmp_path / "retry-state.json"
    proc, rows, posts = _run_driver(
        tmp_path,
        monkeypatch,
        "--mode",
        "active",
        "--retry-state-path",
        str(state),
        env={FLAG: "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert posts == 1, proc.stdout  # exactly one rerun POST
    action_rows = [r for r in rows if r.get("component") == "triage-actions"]
    executed = [r for r in action_rows if r["action"] == "retry_executed"]
    assert len(executed) == 1
    assert executed[0]["run_id"] == "123"
    assert "retry executed=True reason=ok" in proc.stdout

    # Second driver run with the same state: idempotent, no second POST.
    proc2, rows2, posts2 = _run_driver(
        tmp_path,
        monkeypatch,
        "--mode",
        "active",
        "--retry-state-path",
        str(state),
        env={FLAG: "1"},
    )
    assert proc2.returncode == 0, proc2.stderr
    assert posts2 == 1  # still exactly one POST total
    assert any(
        r["reason"] == "already_retried"
        for r in _audit_rows(tmp_path / "audit.jsonl")
        if r.get("component") == "triage-actions"
    )
