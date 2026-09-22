"""Tests for the historical PR replay (shadow evidence backfill).

All fixtures are synthetic; no network is touched. The fetch layer
(GitHubApiPRSource) is exercised only through a stub.
"""

from datetime import datetime, timezone

import pytest

from prismatic.review_factory.replay import (
    ACTUAL_CLOSED_UNMERGED,
    ACTUAL_MERGED,
    REVIEW_VERDICT_PRODUCER,
    replay_batch,
    replay_pr,
    select_prs,
)
from prismatic.review_factory.shadow_poller import default_components


NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)

GREEN_RUNS = [
    {"name": "smoke (ruff lint)", "status": "completed", "conclusion": "success"},
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

FAILED_RUFF_RUNS = [
    {"name": "smoke (ruff lint)", "status": "completed", "conclusion": "failure"},
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


def _pr(number=500, merged=True, mergeable="MERGEABLE"):
    return {
        "number": number,
        "title": f"synthetic PR {number}",
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
        "mergeable": mergeable,
        "merged": merged,
        "merged_at": "2026-09-20T10:00:00Z" if merged else None,
        "closed_at": "2026-09-20T10:00:00Z",
        "author_login": "mbgulden",
        "author_type": "User",
    }


@pytest.fixture(scope="module")
def components():
    return default_components()


def test_replay_agrees_on_green_merged_pr(components):
    rec = replay_pr(
        _pr(merged=True), GREEN_RUNS, ["prismatic/x.py"], components, now=NOW
    )
    assert rec["system_call"] == "merge"
    assert rec["actual_outcome"] == ACTUAL_MERGED
    assert rec["agree"] is True
    assert rec["decided_at"] == "2026-09-20T10:00:00Z"
    assert rec["backfilled"] is True


def test_replay_flags_failed_ruff_as_skip(components):
    # System skips (ruff gate fails), human merged -> disagreement, D2-style.
    rec = replay_pr(
        _pr(number=501, merged=True),
        FAILED_RUFF_RUNS,
        ["prismatic/x.py"],
        components,
        now=NOW,
    )
    assert rec["system_call"] == "skip"
    assert rec["agree"] is False


def test_replay_closed_unmerged_agrees_on_skip(components):
    rec = replay_pr(
        _pr(number=502, merged=False), [], ["prismatic/x.py"], components, now=NOW
    )
    assert rec["actual_outcome"] == ACTUAL_CLOSED_UNMERGED
    # no check runs -> fail closed -> skip -> agrees with closed_unmerged
    assert rec["system_call"] == "skip"
    assert rec["agree"] is True


def test_unknown_fields_flagged(components):
    rec = replay_pr(
        _pr(number=503, merged=False, mergeable="UNKNOWN"),
        [],
        ["prismatic/x.py"],
        components,
        now=NOW,
    )
    assert "check_runs" in rec["replay"]["unknown_fields"]
    assert "merge_conflicts" in rec["replay"]["unknown_fields"]
    rec2 = replay_pr(
        _pr(number=504, merged=True),
        GREEN_RUNS,
        ["prismatic/x.py"],
        components,
        now=NOW,
    )
    assert rec2["replay"]["unknown_fields"] == []


def test_record_schema_and_provenance(components):
    rec = replay_pr(_pr(), GREEN_RUNS, ["prismatic/x.py"], components, now=NOW)
    assert set(rec) >= {
        "pr_number",
        "pr_title",
        "system_call",
        "actual_outcome",
        "agree",
        "decided_at",
        "backfilled",
        "gate_results",
        "reasons",
        "replay",
    }
    meta = rec["replay"]
    assert meta["pipeline"] == "shadow_observer.evaluate"
    assert meta["policy_version"]  # e.g. shadow-v2
    assert meta["review_verdict_producer"] == REVIEW_VERDICT_PRODUCER
    assert meta["replayed_at"] == NOW.isoformat()


def test_determinism_same_input_same_record(components):
    pr = _pr(number=505)
    r1 = replay_pr(pr, GREEN_RUNS, ["a.py"], components, now=NOW)
    r2 = replay_pr(pr, GREEN_RUNS, ["a.py"], components, now=NOW)
    assert r1 == r2


def test_missing_pr_key_raises(components):
    bad = _pr()
    del bad["headRefOid"]
    with pytest.raises(ValueError):
        replay_pr(bad, GREEN_RUNS, ["a.py"], components, now=NOW)


def test_per_pr_error_isolation(components):
    prs = [_pr(number=510), _pr(number=511), _pr(number=512)]

    def fetch_files(n):
        if n == 511:
            raise RuntimeError("boom")
        return ["a.py"]

    def fetch_runs(sha):
        return GREEN_RUNS

    records, errors = replay_batch(prs, fetch_files, fetch_runs, components, now=NOW)
    assert [r["pr_number"] for r in records] == [510, 512]
    assert len(errors) == 1 and errors[0]["pr_number"] == 511


def test_empty_file_list_excluded(components):
    prs = [_pr(number=520), _pr(number=521)]
    records, errors = replay_batch(
        prs, lambda n: [], lambda sha: GREEN_RUNS, components, now=NOW
    )
    assert records == [] and errors == []


def test_select_prs_deterministic_and_skips_bots():
    bot = _pr(number=1)
    bot["author_type"] = "Bot"
    prs = [_pr(number=30), bot, _pr(number=10), _pr(number=20)]
    sel = select_prs(prs, limit=2)
    assert [p["number"] for p in sel] == [10, 20]


def test_source_is_get_only():
    # The fetch layer must not contain any mutating HTTP verbs.
    import inspect

    import prismatic.review_factory.replay as mod

    src = inspect.getsource(mod.GitHubApiPRSource)
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert verb not in src, f"mutating verb {verb} in fetch layer"


def test_filter_prs_with_check_runs():
    from prismatic.review_factory.replay import filter_prs_with_check_runs

    prs = [_pr(number=600), _pr(number=601), _pr(number=602)]

    def fetch(sha):
        return GREEN_RUNS if sha == "a" * 40 else []

    # all share the same head sha in the fixture; vary via number instead
    def fetch2(sha):
        raise RuntimeError("unreachable")

    kept = filter_prs_with_check_runs(prs, fetch)
    assert [p["number"] for p in kept] == [600, 601, 602]
    kept2 = filter_prs_with_check_runs(prs, lambda sha: [])
    assert kept2 == []
    # fetch failure isolates per-PR, keeps going
    calls = []

    def flaky(sha):
        calls.append(sha)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return GREEN_RUNS

    kept3 = filter_prs_with_check_runs(prs, flaky)
    assert [p["number"] for p in kept3] == [600, 602]


def test_filter_requires_self_hosted_checks():
    from prismatic.review_factory.replay import filter_prs_with_check_runs

    prs = [_pr(number=610), _pr(number=611)]
    shadow_only = [
        {"name": "shadow-call", "status": "completed", "conclusion": "success"}
    ]
    kept = filter_prs_with_check_runs(prs, lambda sha: shadow_only)
    assert kept == []
    kept2 = filter_prs_with_check_runs(
        prs,
        lambda sha: [
            {
                "name": "smoke (ruff lint)",
                "status": "completed",
                "conclusion": "success",
            }
        ],
    )
    assert [p["number"] for p in kept2] == [610, 611]


def test_select_prs_order_desc():
    prs = [_pr(number=10), _pr(number=30), _pr(number=20)]
    sel = select_prs(prs, limit=2, order="desc")
    assert [p["number"] for p in sel] == [30, 20]
    with pytest.raises(ValueError):
        select_prs(prs, limit=2, order="sideways")


def test_filter_max_keep_stops_early():
    from prismatic.review_factory.replay import filter_prs_with_check_runs

    prs = [_pr(number=n) for n in (700, 701, 702, 703)]
    calls = []

    def fetch(sha):
        calls.append(sha)
        return [
            {
                "name": "smoke (ruff lint)",
                "status": "completed",
                "conclusion": "success",
            }
        ]

    kept = filter_prs_with_check_runs(reversed(prs), fetch, max_keep=2)
    assert [p["number"] for p in kept] == [703, 702]
    assert len(calls) == 2
