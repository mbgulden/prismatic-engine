"""Unit tests for scripts/branch_protection_tripwire.py.

Covers the classification logic and the check driver against a fake
GitHub transport — no network, no state writes outside tmp_path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import branch_protection_tripwire as tw


def merged_pr(n=617):
    return [{"number": n, "merged_at": "2026-09-28T23:08:00Z"}]


class FakeGitHub:
    """Minimal fake for the module-level gh() transport."""

    def __init__(self):
        self.head = "a" * 40
        self.prs_by_sha: dict[str, list] = {}
        self.compare_status = "ahead"
        self.compare_ahead_by = 1
        self.calls: list[tuple] = []

    def __call__(self, api_path, method="GET", payload=None):
        self.calls.append((method, api_path))
        if api_path.endswith("/branches/main"):
            return {"commit": {"sha": self.head}}
        if "/commits/" in api_path and api_path.endswith("/pulls"):
            sha = api_path.split("/commits/")[1].split("/pulls")[0]
            return self.prs_by_sha.get(sha, [])
        if "/compare/" in api_path:
            return {
                "status": self.compare_status,
                "ahead_by": self.compare_ahead_by,
                "behind_by": 0,
            }
        if api_path.startswith("/repos/") and "/commits/" in api_path:
            return {"commit": {"tree": {"sha": "t" * 40}}}
        raise AssertionError(f"unexpected API call: {method} {api_path}")


@pytest.fixture
def fake(monkeypatch):
    f = FakeGitHub()
    monkeypatch.setattr(tw, "gh", f)
    monkeypatch.setattr(tw.time, "sleep", lambda s: None)
    return f


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "state.json")


def write_state(path, sha):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps({"last_seen_sha": sha, "last_check_utc": "t", "tripped_sha": None})
    )


# ── classification ──────────────────────────────────────────────────


def test_squash_merge_with_merged_pr_is_expected():
    # tonight's real shape: single parent, one merged PR, zero receipts
    assert tw.classify_head(merged_pr(617)) == "expected"


def test_merge_commit_with_merged_pr_is_expected():
    assert tw.classify_head(merged_pr(100)) == "expected"  # parent count ignored


def test_direct_push_is_unexpected():
    assert tw.classify_head([]) == "unexpected-push"


def test_closed_unmerged_pr_is_unexpected():
    assert tw.classify_head([{"number": 5, "merged_at": None}]) == "unexpected-push"


def test_ancestry_ok():
    assert tw.classify_ancestry("ahead") == "ok"
    assert tw.classify_ancestry("identical") == "ok"


def test_ancestry_rewritten():
    assert tw.classify_ancestry("behind") == "rewritten"
    assert tw.classify_ancestry("diverged") == "rewritten"


# ── driver ──────────────────────────────────────────────────────────


def test_first_run_records_baseline(fake, state_file):
    out = tw.check(dry_run=True, state_path=state_file)
    assert out["verdict"] == "baseline-recorded"
    assert not Path(state_file).exists()  # dry-run writes nothing


def test_unchanged_head_is_quiet(fake, state_file):
    write_state(state_file, fake.head)
    out = tw.check(dry_run=True, state_path=state_file)
    assert out["verdict"] == "unchanged"
    assert not any(c[0] == "POST" for c in fake.calls)


def test_expected_advance_updates_state(fake, state_file, tmp_path):
    old, new = "0" * 40, "n" * 40
    write_state(state_file, old)
    fake.head = new
    fake.prs_by_sha[new] = merged_pr(618)
    out = tw.check(dry_run=False, state_path=state_file)
    assert out["verdict"] == "expected"
    saved = json.loads(Path(state_file).read_text())
    assert saved["last_seen_sha"] == new


def test_direct_push_trips_after_grace(fake, state_file):
    old, bad = "0" * 40, "b" * 40
    write_state(state_file, old)
    fake.head = bad
    fake.prs_by_sha[bad] = []  # no PR, before and after grace
    out = tw.check(dry_run=True, state_path=state_file)
    assert out["verdict"] == "TRIP"
    assert out["kind"] == "direct-push"
    assert "revert PR" in out["dry_run_action"]


def test_grace_recovers_when_pr_appears(fake, state_file, monkeypatch):
    """GitHub association lag: no PR on first fetch, PR on re-check → no trip."""
    old, new = "0" * 40, "n" * 40
    write_state(state_file, old)
    fake.head = new
    seen = {"n": 0}

    orig = fake.__call__

    def flaky(api_path, method="GET", payload=None):
        if api_path.endswith("/pulls"):
            seen["n"] += 1
            return [] if seen["n"] == 1 else merged_pr(619)
        return orig(api_path, method, payload)

    monkeypatch.setattr(tw, "gh", flaky)
    out = tw.check(dry_run=True, state_path=state_file)
    assert out["verdict"] == "expected"


def test_history_rewrite_trips(fake, state_file):
    old, new = "0" * 40, "r" * 40
    write_state(state_file, old)
    fake.head = new
    fake.prs_by_sha[new] = merged_pr(620)  # PR-associated but ancestry broken
    fake.compare_status = "diverged"
    out = tw.check(dry_run=True, state_path=state_file)
    assert out["verdict"] == "TRIP"
    assert out["kind"] == "history-rewrite"


def test_real_trip_emits_signal_opens_pr_and_rate_limits(fake, state_file, monkeypatch):
    old, bad = "0" * 40, "b" * 40
    write_state(state_file, old)
    fake.head = bad
    fake.prs_by_sha[bad] = []
    emitted, prs = [], []

    monkeypatch.setattr(
        tw, "emit_signal", lambda m, a, severity="error": emitted.append(m)
    )
    monkeypatch.setattr(
        tw, "open_revert_pr", lambda b, g, n: prs.append((b, g, n)) or {"number": 700}
    )
    out = tw.check(dry_run=False, state_path=state_file)
    assert out == {"verdict": "TRIP", "kind": "direct-push", "pr": 700}
    assert len(emitted) == 1 and "TRIP" in emitted[0]
    assert prs == [(bad, old, 1)]

    # second poll with the same head stays quiet
    out2 = tw.check(dry_run=False, state_path=state_file)
    assert out2["verdict"] == "already-tripped"
    assert len(emitted) == 1 and len(prs) == 1


def test_revert_message_names_shas():
    msg = tw.revert_commit_message("b" * 40, "0" * 40, 2)
    assert "bbbbbbbb" in msg and "00000000" in msg and "2 unexpected" in msg
