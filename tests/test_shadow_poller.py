"""Regression tests for the shadow-feed repair (Change A).

One malformed PR must never abort the whole poll: it is logged with the PR
number and deferred (its key is NOT added to seen, so it retries next poll).
``list_open_prs()`` failing still aborts loudly — that is the poll's core
input and silence there would be worse.
"""

import json
import logging
from pathlib import Path

import pytest

from prismatic.review_factory import shadow_observer as observer
from prismatic.review_factory import shadow_poller as poller


def _pr(number: int) -> dict:
    return {
        "number": number,
        "title": f"Fake PR {number}",
        "headRefOid": f"sha-{number}",
        "baseRefOid": "base-sha",
        "mergeable": "MERGEABLE",
    }


def _green_runs() -> list:
    return [
        {
            "name": "smoke (ruff lint)",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "name": "review factory gate (tier A)",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "name": "Verify shipped plugins load",
            "status": "completed",
            "conclusion": "success",
        },
    ]


class FakePRSource:
    """Injectable PR source: every PR is settled and green."""

    def __init__(self, prs):
        self.prs = prs
        self.listed = 0

    def list_open_prs(self):
        self.listed += 1
        return self.prs

    def get_pr_files(self, pr_number):
        return ["prismatic/review_factory/shadow_poller.py"]

    def get_check_runs(self, head_sha):
        return _green_runs()


@pytest.fixture(scope="module")
def components():
    policy = observer.load_policy()
    assert policy.enabled, "shadow v2 policy must be enabled for these tests"
    bands = observer.load_bands(observer.SPEC_DIR / Path(policy.bands_file).name)
    return policy, bands, observer.default_tier_engine(policy)


def _run_poll(source, components, tmp_path):
    policy, bands, tier_engine = components
    state_path = tmp_path / "shadow-seen.json"
    sink = tmp_path / "shadow-decisions.jsonl"
    decisions = poller.poll_once(
        source,
        policy,
        bands,
        tier_engine,
        state_path=state_path,
        sink=sink,
    )
    return decisions, state_path, sink


def test_one_bad_input_does_not_kill_poll(tmp_path, caplog, monkeypatch, components):
    """The crash: one PR's input raises AttributeError; the poll completes.

    Regression for the Sep 22 crashes ('str' object has no attribute 'get'):
    the bad PR is deferred with a warning naming its number, every other PR
    is still evaluated, and the poll exits 0 (no exception).
    """
    real_load = poller.load_input_from_dict

    def load_with_one_bad(data):
        if data["pr_number"] == 7:
            raise AttributeError("'str' object has no attribute 'get'")
        return real_load(data)

    monkeypatch.setattr(poller, "load_input_from_dict", load_with_one_bad)
    source = FakePRSource([_pr(7), _pr(8)])

    with caplog.at_level(logging.WARNING, logger=poller.__name__):
        decisions, state_path, _ = _run_poll(source, components, tmp_path)

    assert [d.pr_number for d in decisions] == [8]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "PR #7" in r.getMessage() and "AttributeError" in r.getMessage()
        for r in warnings
    ), "warning must name the bad PR number"
    seen = poller.load_seen(state_path)
    assert "7:sha-7" not in seen, "bad PR must retry next poll"
    assert "8:sha-8" in seen


def test_malformed_payload_deferred_and_retried(
    tmp_path, caplog, monkeypatch, components
):
    """A gh-shaped malformed payload defers the PR; it retries next poll."""
    real_build = poller.build_input_dict
    broken = {9}

    def build_with_malformed_payload(pr, check_runs, files):
        if pr["number"] in broken:
            # what build_input_dict raises on an unexpected gh payload shape
            raise AttributeError("'str' object has no attribute 'get'")
        return real_build(pr, check_runs, files)

    monkeypatch.setattr(poller, "build_input_dict", build_with_malformed_payload)
    source = FakePRSource([_pr(8), _pr(9)])

    with caplog.at_level(logging.WARNING, logger=poller.__name__):
        decisions, state_path, _ = _run_poll(source, components, tmp_path)
    assert [d.pr_number for d in decisions] == [8]
    assert "9:sha-9" not in poller.load_seen(state_path)

    # Next poll, with the payload healthy again, the deferred PR is picked up.
    broken.clear()
    decisions, state_path, _ = _run_poll(source, components, tmp_path)
    assert [d.pr_number for d in decisions] == [9]
    assert "9:sha-9" in poller.load_seen(state_path)


def test_evaluate_failure_does_not_kill_poll(tmp_path, caplog, monkeypatch, components):
    """observe() blowing up on one PR must not abort the poll either."""
    real_observe = poller.observe

    def observe_with_one_bad(inp, policy, bands, tier_engine, sink=None):
        if inp.pr_number == 7:
            raise RuntimeError("boom")
        return real_observe(inp, policy, bands, tier_engine, sink)

    monkeypatch.setattr(poller, "observe", observe_with_one_bad)
    source = FakePRSource([_pr(7), _pr(8)])

    with caplog.at_level(logging.ERROR, logger=poller.__name__):
        decisions, state_path, _ = _run_poll(source, components, tmp_path)

    assert [d.pr_number for d in decisions] == [8]
    assert any("PR #7" in r.getMessage() for r in caplog.records), (
        "failure must name the PR number"
    )
    assert "7:sha-7" not in poller.load_seen(state_path)


def test_list_open_prs_failure_still_aborts(tmp_path, components):
    """The poll's core input failing is loud, never silent."""

    class BrokenSource(FakePRSource):
        def list_open_prs(self):
            raise RuntimeError("gh exploded")

    policy, bands, tier_engine = components
    with pytest.raises(RuntimeError, match="gh exploded"):
        poller.poll_once(
            BrokenSource([]),
            policy,
            bands,
            tier_engine,
            state_path=tmp_path / "shadow-seen.json",
        )


def test_already_seen_pr_is_skipped(tmp_path, components):
    state_path = tmp_path / "shadow-seen.json"
    state_path.write_text(json.dumps(["8:sha-8"]), encoding="utf-8")
    source = FakePRSource([_pr(8)])
    decisions, _, sink = _run_poll(source, components, tmp_path)
    assert decisions == []
    assert not sink.exists(), "no signal may be emitted for a seen key"


def test_disabled_policy_short_circuits(tmp_path, components):
    policy = observer.ShadowPolicy(
        version="test",
        enabled=False,
        gates=(),
        verdicts_mergeable=("CLEAN",),
        tier_policy_file="",
        shadow_merge_max_tier=1,
        jev_enabled=False,
        bands_file="",
    )
    _, bands, tier_engine = components

    class ExplodingSource(FakePRSource):
        def list_open_prs(self):
            raise AssertionError("must not be called when disabled")

    decisions = poller.poll_once(
        ExplodingSource([]),
        policy,
        bands,
        tier_engine,
        state_path=tmp_path / "shadow-seen.json",
    )
    assert decisions == []
