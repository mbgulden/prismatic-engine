"""Tests for the live verdict path (gated, default OFF).

Every test asserts the safety contract:
- live mode is off unless PRISMATIC_VERDICT_LIVE=1 is explicitly set;
- the guard refuses without a fresh dry-run log;
- missing labels fail closed (never auto-created);
- comments are idempotent via the machine marker;
- every action — success, skip, failure — is audited.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prismatic.review_factory import verdict_live
from prismatic.review_factory.verdict_live import (
    COMMENT_MARKER_PREFIX,
    apply_verdict_label,
    dry_run_fresh,
    existing_verdict_marker,
    guard_allows_live,
    issue_live_verdict,
    label_exists,
    live_enabled,
    post_verdict_comment,
    render_verdict_comment,
    run_live_verdicts,
    verdicts_for_digest,
    write_digest_fragment,
    VerdictLiveError,
)


def _now():
    return datetime.now(timezone.utc)


def _record(pr=249, verdict="overdue", **over):
    rec = {
        "pr": pr,
        "title": f"PR #{pr}",
        "author": "muse",
        "age_days": 49.4,
        "verdict": verdict,
        "recommended": "reject",
        "reason": "past the 7-day line (49.4d old)",
        "mode": "dry_run",
        "ts": _now().isoformat(),
    }
    rec.update(over)
    return rec


class FakeRunner:
    """Dispatches canned gh responses; records every invocation."""

    def __init__(self, comments=(), labels=("stranded-overdue", "stranded-approaching"),
                 fail_on=()):
        self.comments = list(comments)
        self.labels = list(labels)
        self.fail_on = set(fail_on)
        self.calls = []

    def __call__(self, args, input_text=None):
        self.calls.append(list(args))
        key = " ".join(args[1:4])
        if any(f in key for f in self.fail_on):
            return subprocess.CompletedProcess(args, 1, "", "boom")
        if args[1:3] == ["pr", "view"]:
            bodies = [{"body": c} for c in self.comments]
            return subprocess.CompletedProcess(
                args, 0, json.dumps([c["body"] for c in bodies]), "")
        if args[1:3] == ["label", "list"]:
            return subprocess.CompletedProcess(
                args, 0, json.dumps(self.labels), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def posted_bodies(self):
        return [c[c.index("--body") + 1] for c in self.calls
                if "--body" in c]

    def added_labels(self):
        return [c[c.index("--add-label") + 1] for c in self.calls
                if "--add-label" in c]


def _fresh_log(tmp_path: Path, age_hours: float = 1.0) -> Path:
    log = tmp_path / "dryrun.jsonl"
    log.write_text(json.dumps({"pr": 1, "verdict": "watching"}) + "\n")
    import os
    ts = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).timestamp()
    os.utime(log, (ts, ts))
    return log


# --- flag + guard ---------------------------------------------------------

def test_live_disabled_by_default(monkeypatch):
    monkeypatch.delenv(verdict_live.LIVE_FLAG_ENV, raising=False)
    assert live_enabled() is False


def test_live_enabled_only_on_explicit_one(monkeypatch):
    monkeypatch.setenv(verdict_live.LIVE_FLAG_ENV, "1")
    assert live_enabled() is True
    monkeypatch.setenv(verdict_live.LIVE_FLAG_ENV, "true")
    assert live_enabled() is False
    monkeypatch.setenv(verdict_live.LIVE_FLAG_ENV, "")
    assert live_enabled() is False


def test_guard_refused_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv(verdict_live.LIVE_FLAG_ENV, raising=False)
    log = _fresh_log(tmp_path)
    allowed, reason = guard_allows_live(log)
    assert allowed is False
    assert "disabled" in reason


def test_guard_refused_when_dry_run_log_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(verdict_live.LIVE_FLAG_ENV, "1")
    allowed, reason = guard_allows_live(tmp_path / "nope.jsonl")
    assert allowed is False
    assert "missing" in reason


def test_guard_refused_when_dry_run_log_stale(tmp_path, monkeypatch):
    monkeypatch.setenv(verdict_live.LIVE_FLAG_ENV, "1")
    log = _fresh_log(tmp_path, age_hours=30)
    allowed, reason = guard_allows_live(log)
    assert allowed is False
    assert "stale" in reason


def test_guard_allows_when_flag_on_and_log_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv(verdict_live.LIVE_FLAG_ENV, "1")
    log = _fresh_log(tmp_path)
    allowed, reason = guard_allows_live(log)
    assert allowed is True


def test_dry_run_fresh_empty_log_refused(tmp_path):
    log = tmp_path / "empty.jsonl"
    log.write_text("")
    fresh, reason = dry_run_fresh(log)
    assert fresh is False
    assert "empty" in reason


# --- comment rendering -----------------------------------------------------

def test_render_verdict_comment_has_marker_and_facts():
    body = render_verdict_comment(_record())
    assert COMMENT_MARKER_PREFIX + "overdue -->" in body
    assert "OVERDUE" in body
    assert "49.4" in body
    assert "reject" in body
    assert "nothing was closed or merged" in body


# --- gh interactions (mocked) ----------------------------------------------

def test_existing_verdict_marker_found():
    runner = FakeRunner(comments=["hello", f"note {COMMENT_MARKER_PREFIX}overdue --> x"])
    assert existing_verdict_marker("o/r", 249, runner) == "overdue"


def test_existing_verdict_marker_absent():
    runner = FakeRunner(comments=["just a human comment"])
    assert existing_verdict_marker("o/r", 249, runner) is None


def test_label_exists_true_and_false():
    runner = FakeRunner(labels=["stranded-overdue"])
    assert label_exists("o/r", "stranded-overdue", runner) is True
    assert label_exists("o/r", "nope", runner) is False


def test_post_verdict_comment_failure_raises():
    runner = FakeRunner(fail_on={"pr comment"})
    with pytest.raises(VerdictLiveError, match="gh pr comment failed"):
        post_verdict_comment("o/r", 249, "body", runner)


def test_apply_verdict_label_missing_fails_closed():
    runner = FakeRunner(labels=["other-label"])
    with pytest.raises(VerdictLiveError, match="does not exist"):
        apply_verdict_label("o/r", 249, "stranded-overdue", runner)
    # fail-closed: no gh write attempted after the list check
    assert runner.added_labels() == []


def test_issue_live_verdict_overdue_posts_and_labels(tmp_path):
    runner = FakeRunner()
    live_log = tmp_path / "live.jsonl"
    action = issue_live_verdict(_record(), "o/r", runner=runner, live_log=live_log)
    assert action["action"] == "verdict_issued"
    assert action["label"] == "stranded-overdue"
    assert len(runner.posted_bodies()) == 1
    assert runner.added_labels() == ["stranded-overdue"]
    # audited
    entries = [json.loads(line) for line in live_log.read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]["action"] == "verdict_issued"
    assert entries[0]["mode"] == "live"


def test_issue_live_verdict_idempotent_on_existing_marker(tmp_path):
    marker = f"{COMMENT_MARKER_PREFIX}overdue -->"
    runner = FakeRunner(comments=[marker])
    live_log = tmp_path / "live.jsonl"
    action = issue_live_verdict(_record(), "o/r", runner=runner, live_log=live_log)
    assert action["action"] == "skipped"
    assert "already announced" in action["reason"]
    assert runner.posted_bodies() == []
    assert runner.added_labels() == []


def test_issue_live_verdict_watching_takes_no_action(tmp_path):
    runner = FakeRunner()
    action = issue_live_verdict(
        _record(verdict="watching"), "o/r",
        runner=runner, live_log=tmp_path / "live.jsonl")
    assert action["action"] == "skipped"
    assert runner.calls == []


def test_issue_live_verdict_audits_failure(tmp_path):
    runner = FakeRunner(fail_on={"pr edit"})
    live_log = tmp_path / "live.jsonl"
    action = issue_live_verdict(_record(), "o/r", runner=runner, live_log=live_log)
    assert action["action"] == "failed"
    entries = [json.loads(line) for line in live_log.read_text().splitlines()]
    assert entries[0]["action"] == "failed"


def test_run_live_verdicts_refuses_without_guard(tmp_path, monkeypatch):
    monkeypatch.delenv(verdict_live.LIVE_FLAG_ENV, raising=False)
    runner = FakeRunner()
    with pytest.raises(VerdictLiveError, match="refused"):
        run_live_verdicts([_record()], "o/r", runner=runner,
                          live_log=tmp_path / "live.jsonl",
                          dry_run_log=tmp_path / "missing.jsonl")
    assert runner.calls == []  # no gh touched


def test_run_live_verdicts_full_pass(tmp_path, monkeypatch):
    monkeypatch.setenv(verdict_live.LIVE_FLAG_ENV, "1")
    dry_log = _fresh_log(tmp_path)
    runner = FakeRunner()
    actions, failures = run_live_verdicts(
        [_record(249), _record(250, verdict="watching")], "o/r",
        runner=runner, live_log=tmp_path / "live.jsonl", dry_run_log=dry_log)
    assert len(actions) == 2
    assert failures == []
    assert actions[0]["action"] == "verdict_issued"
    assert actions[1]["action"] == "skipped"


# --- digest wiring ----------------------------------------------------------

def test_verdicts_for_digest_shape():
    records = [_record(249), _record(557, verdict="approaching", age_days=6.1)]
    actions = [
        {"pr": 249, "action": "verdict_issued", "verdict": "overdue",
         "ts": "2026-09-27T18:00:00+00:00"},
        {"pr": 557, "action": "skipped", "reason": "no live action",
         "ts": "2026-09-27T18:00:00+00:00"},
    ]
    payload = verdicts_for_digest(actions, records)
    assert [a["pr"] for a in payload["approaching"]] == [557]
    assert payload["approaching"][0]["age_days"] == 6.1
    assert len(payload["verdicts_issued"]) == 1
    assert payload["verdicts_issued"][0]["pr"] == 249

    # digest normalizer accepts the payload shape
    from prismatic.review_factory.digest import _normalize_stranded
    approaching, issued, truncated = _normalize_stranded(payload, 25)
    assert len(approaching) == 1
    assert issued[0]["verdict"] == "overdue"
    assert truncated == 0


def test_write_digest_fragment_round_trip(tmp_path):
    frag = write_digest_fragment(
        {"approaching": [], "verdicts_issued": [{"pr": 1}]},
        tmp_path / "frag.json")
    assert frag.is_file()
    data = json.loads(frag.read_text())
    assert data["verdicts_issued"] == [{"pr": 1}]
    assert "ts" in data
