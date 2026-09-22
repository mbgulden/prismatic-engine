"""Tests for the Phase 0 shadow event adapter (shadow_event).

No network: the PR source is faked and ``fetch_pr`` is monkeypatched.
The fail-safe contract mirrors the poller: unknown or unfinished state
defers (exit 0, no signal), never invents a green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory import shadow_event
from prismatic.review_factory.policy import PolicyEngine
from prismatic.review_factory.shadow_observer import (
    RiskBands,
    ShadowPolicy,
    load_policy,
)
from prismatic.review_factory.shadow_poller import (
    RF_GATE_CHECK_NAME,
    RUFF_CHECK_NAME,
    SELF_HOSTED_CHECKS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/shadow-event-feed.yml"


def _run(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


ALL_GREEN = [_run(name) for name in SELF_HOSTED_CHECKS]


def _policy(enabled=True) -> ShadowPolicy:
    # Real policy file keeps the gate list honest; enabled flag overridden.
    base = load_policy()
    return ShadowPolicy(
        version=base.version,
        enabled=enabled,
        gates=base.gates,
        verdicts_mergeable=base.verdicts_mergeable,
        tier_policy_file=base.tier_policy_file,
        shadow_merge_max_tier=base.shadow_merge_max_tier,
        jev_enabled=base.jev_enabled,
        bands_file=base.bands_file,
    )


def _bands() -> RiskBands:
    return RiskBands(version="test", auto_below=0.2, human_above=0.6)


def _tier_engine() -> PolicyEngine:
    return PolicyEngine.from_yaml(
        Path(__file__).resolve().parent.parent / "spec" / "policy_file_v1.yaml"
    )


class FakeSource:
    """In-memory PRSource (only the per-PR methods the adapter uses)."""

    def __init__(self, checks=None, files=None, fail_checks=False, fail_files=False):
        self._checks = checks or {}
        self._files = files or {}
        self.fail_checks = fail_checks
        self.fail_files = fail_files

    def get_check_runs(self, head_sha):
        if self.fail_checks:
            raise RuntimeError("boom")
        return self._checks.get(head_sha, [])

    def get_pr_files(self, pr_number):
        if self.fail_files:
            raise RuntimeError("boom")
        return self._files.get(pr_number, ["docs/note.md"])


def _pr(number=101, sha="a" * 40, mergeable="MERGEABLE", state="OPEN"):
    return {
        "number": number,
        "title": "Test PR",
        "headRefOid": sha,
        "mergeable": mergeable,
        "state": state,
    }


def _run_eval(monkeypatch, tmp_path, pr, source, event="synchronize", enabled=True):
    monkeypatch.setattr(shadow_event, "fetch_pr", lambda n, repo="r": pr)
    sink = tmp_path / "shadow-decisions.jsonl"
    state = tmp_path / "shadow-seen.json"
    rc = shadow_event.evaluate_pr(
        pr["number"],
        event,
        source,
        _policy(enabled),
        _bands(),
        _tier_engine(),
        state,
        sink,
    )
    signals = (
        [json.loads(line) for line in sink.read_text().splitlines()]
        if sink.exists()
        else []
    )
    return rc, signals, state


# ── happy path ─────────────────────────────────────────────────────────


def test_settled_green_pr_emits_one_signal(monkeypatch, tmp_path):
    sha = "a" * 40
    source = FakeSource(checks={sha: ALL_GREEN})
    rc, signals, state = _run_eval(monkeypatch, tmp_path, _pr(sha=sha), source)
    assert rc == 0
    assert len(signals) == 1
    assert signals[0]["metadata"]["pr_number"] == 101
    assert signals[0]["metadata"]["policy_version"].startswith("shadow-")
    assert signals[0]["agent"] == "prismatic-shadow-observer"
    assert f"101:{sha}" in json.loads(state.read_text())


def test_second_event_for_same_sha_does_not_duplicate(monkeypatch, tmp_path):
    sha = "b" * 40
    source = FakeSource(checks={sha: ALL_GREEN})
    rc1, s1, _ = _run_eval(monkeypatch, tmp_path, _pr(sha=sha), source)
    rc2, s2, _ = _run_eval(monkeypatch, tmp_path, _pr(sha=sha), source)
    assert (rc1, rc2) == (0, 0)
    assert len(s1) == 1 and len(s2) == 1  # same single signal, no duplicate


def test_closed_event_skips_mergeability_gate_and_emits(monkeypatch, tmp_path):
    # Resolved PRs report mergeable=UNKNOWN; the observation is historical.
    sha = "c" * 40
    source = FakeSource(checks={sha: ALL_GREEN})
    pr = _pr(sha=sha, mergeable="UNKNOWN", state="MERGED")
    rc, signals, _ = _run_eval(monkeypatch, tmp_path, pr, source, event="closed")
    assert rc == 0
    assert len(signals) == 1
    assert signals[0]["metadata"]["pr_number"] == 101


# ── fail-safe deferrals (exit 0, no signal) ────────────────────────────


def test_unreadable_pr_defers(monkeypatch, tmp_path):
    monkeypatch.setattr(
        shadow_event,
        "fetch_pr",
        lambda n, repo="r": (_ for _ in ()).throw(RuntimeError("nope")),
    )
    sink = tmp_path / "s.jsonl"
    rc = shadow_event.evaluate_pr(
        101,
        "opened",
        FakeSource(),
        _policy(),
        _bands(),
        _tier_engine(),
        tmp_path / "seen.json",
        sink,
    )
    assert rc == 0
    assert not sink.exists()


def test_unknown_mergeability_defers_for_open_pr(monkeypatch, tmp_path):
    sha = "d" * 40
    source = FakeSource(checks={sha: ALL_GREEN})
    rc, signals, _ = _run_eval(
        monkeypatch, tmp_path, _pr(sha=sha, mergeable="UNKNOWN"), source
    )
    assert rc == 0
    assert signals == []


def test_unsettled_ci_defers(monkeypatch, tmp_path):
    sha = "e" * 40
    runs = [_run(RUFF_CHECK_NAME), _run(RF_GATE_CHECK_NAME, status="in_progress")]
    source = FakeSource(checks={sha: runs})
    rc, signals, _ = _run_eval(monkeypatch, tmp_path, _pr(sha=sha), source)
    assert rc == 0
    assert signals == []


def test_unreadable_check_runs_defers(monkeypatch, tmp_path):
    sha = "f" * 40
    source = FakeSource(fail_checks=True)
    rc, signals, _ = _run_eval(monkeypatch, tmp_path, _pr(sha=sha), source)
    assert rc == 0
    assert signals == []


def test_unreadable_file_list_defers(monkeypatch, tmp_path):
    sha = "0" * 40
    source = FakeSource(checks={sha: ALL_GREEN}, fail_files=True)
    rc, signals, _ = _run_eval(monkeypatch, tmp_path, _pr(sha=sha), source)
    assert rc == 0
    assert signals == []


def test_disabled_policy_is_noop(monkeypatch, tmp_path):
    sha = "1" * 40
    source = FakeSource(checks={sha: ALL_GREEN})
    rc, signals, _ = _run_eval(
        monkeypatch, tmp_path, _pr(sha=sha), source, enabled=False
    )
    assert rc == 0
    assert signals == []


# ── CLI wiring ─────────────────────────────────────────────────────────


def test_main_usage_error_exits_2():
    with pytest.raises(SystemExit) as exc:
        shadow_event.main(["--pr-number", "not-an-int"])
    assert exc.value.code == 2


def test_main_config_error_exits_1(monkeypatch, tmp_path):
    from prismatic.review_factory.shadow_observer import ShadowConfigError

    monkeypatch.setattr(
        shadow_event,
        "default_components",
        lambda: (_ for _ in ()).throw(ShadowConfigError("bad")),
    )
    rc = shadow_event.main(["--pr-number", "101"])
    assert rc == 1


# ── workflow file contract ─────────────────────────────────────────────


def test_workflow_triggers_and_runner():
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    # YAML 1.1 parses the `on:` key as boolean True.
    on_block = data.get("on", data.get(True))
    types = on_block["pull_request"]["types"]
    assert sorted(types) == ["closed", "opened", "reopened", "synchronize"]
    job = data["jobs"]["shadow-call"]
    assert "self-hosted" in job["runs-on"]
    perms = data["permissions"]
    assert perms["pull-requests"] == "read"
    assert perms["checks"] == "read"
    # The workflow must invoke the adapter (not duplicate its logic).
    steps_text = json.dumps(data["jobs"]["shadow-call"]["steps"])
    assert "shadow_event.py" in steps_text
    assert "--pr-number" in steps_text


# ── workflow_run completed re-trigger (Sep 22, 2026) ───────────────────


def test_completed_event_takes_reevaluation_path(monkeypatch, tmp_path):
    # The workflow_run completed trigger passes --event completed; it must
    # take the same non-closed re-evaluation path as synchronize.
    sha = "d" * 40
    source = FakeSource(checks={sha: ALL_GREEN})
    pr = _pr(sha=sha)
    rc, signals, _ = _run_eval(monkeypatch, tmp_path, pr, source, event="completed")
    assert rc == 0
    assert len(signals) == 1
    assert signals[0]["metadata"]["pr_number"] == 101


def test_completed_event_applies_mergeability_gate(monkeypatch, tmp_path):
    # Unlike "closed", "completed" is subject to the mergeability gate.
    sha = "e" * 40
    source = FakeSource(checks={sha: ALL_GREEN})
    pr = _pr(sha=sha, mergeable="UNKNOWN")
    rc, signals, _ = _run_eval(monkeypatch, tmp_path, pr, source, event="completed")
    assert rc == 0
    assert signals == []


def test_main_accepts_completed_event(monkeypatch, tmp_path):
    # argparse must not reject the event value the workflow passes.
    monkeypatch.setattr(
        shadow_event,
        "default_components",
        lambda: (_policy(), _bands(), _tier_engine()),
    )
    seen = {}

    def fake_eval(
        pr_number, event, source, policy, bands, tier_engine, state_path, sink
    ):
        seen["event"] = event
        return 0

    monkeypatch.setattr(shadow_event, "evaluate_pr", fake_eval)
    rc = shadow_event.main(["--pr-number", "101", "--event", "completed"])
    assert rc == 0
    assert seen["event"] == "completed"
