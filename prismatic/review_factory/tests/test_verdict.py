"""Tests for the stranded-work verdict pipeline (item 3, dry-run only).

Every test asserts the dry-run contract:
- stale and fresh PRs get the right verdict;
- every evaluated PR produces exactly one decision record (never silent);
- the pipeline performs no GitHub writes (stdlib-only module);
- run_pipeline appends to the dry-run log without touching anything else.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from prismatic.review_factory import verdict
from prismatic.review_factory.verdict import evaluate_pr, run_pipeline


def _pr(number: int, days_old: float, **over) -> dict:
    created = datetime.now(timezone.utc) - timedelta(days=days_old)
    entry = {
        "pr": number,
        "title": f"PR #{number}",
        "author": "muse",
        "created_at": created.isoformat(),
    }
    entry.update(over)
    return entry


def test_fresh_pr_is_watching():
    record = evaluate_pr(_pr(560, 1))
    assert record["verdict"] == "watching"
    assert record["recommended"] is None
    assert record["mode"] == "dry_run"
    assert record["age_days"] == pytest.approx(1.0, abs=0.05)


def test_approaching_pr_nearing_the_line():
    record = evaluate_pr(_pr(557, 6))
    assert record["verdict"] == "approaching"
    assert record["recommended"] is None
    assert "7-day" in record["reason"]


def test_stale_pr_is_overdue_with_reject_recommendation():
    record = evaluate_pr(_pr(550, 8))
    assert record["verdict"] == "overdue"
    assert record["recommended"] == "reject"
    assert "no action taken" in record["reason"]
    assert record["mode"] == "dry_run"


def test_stale_t1_green_pr_recommends_merge():
    record = evaluate_pr(
        _pr(551, 9, change_class="docs", ci_green=True, policy_excluded=False)
    )
    assert record["verdict"] == "overdue"
    assert record["recommended"] == "merge"


def test_stale_superseded_pr_recommends_supersede():
    record = evaluate_pr(_pr(552, 10, superseded_by=553))
    assert record["verdict"] == "overdue"
    assert record["recommended"] == "supersede"
    assert "#553" in record["reason"]


def test_unparseable_pr_is_skipped_not_silent():
    for bad in (None, 42, {}, {"title": "no number"}, {"pr": 1, "created_at": "junk"}):
        record = evaluate_pr(bad)
        assert record["verdict"] == "skipped"
        assert record["mode"] == "dry_run"
        assert record["reason"]  # a reason is always given


def test_run_pipeline_appends_one_record_per_pr(tmp_path):
    log = tmp_path / "dryrun.jsonl"
    prs = [_pr(560, 1), _pr(557, 6), _pr(550, 8), {"junk": True}]
    records = run_pipeline(prs, log_path=log)
    assert len(records) == 4  # nothing dropped, nothing silent
    lines = log.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 4
    logged = [json.loads(line) for line in lines]
    assert [r["pr"] for r in logged] == [560, 557, 550, None]
    assert all(r["mode"] == "dry_run" for r in logged)
    # Second run appends; never rewrites.
    run_pipeline([_pr(561, 2)], log_path=log)
    assert len(log.read_text(encoding="utf-8").strip().split("\n")) == 5


def test_module_cannot_make_network_calls():
    import inspect

    source_path = inspect.getsourcefile(verdict)
    assert source_path is not None and source_path.endswith("verdict.py")
    text = open(source_path, encoding="utf-8").read()
    for forbidden in ("subprocess", "urllib", "requests", "httpx", "socket"):
        assert forbidden not in text, f"network-capable import found: {forbidden}"
    assert verdict.MODE == "dry_run"
