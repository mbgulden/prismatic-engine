"""Tests for the Jev validation-loop calibration backfill (plan §9).

- Synthetic git-history fixture -> ``build_proposal`` emits the expected
  proposal (tolerated-red / never-red sets match the fixture).
- The proposal file carries ``status: proposed`` and is NOT read by the
  live bar: ``load_calibration`` refuses any non-approved status.
- The new learn-loop outcome types record through ``record_outcome``'s
  existing validation path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory import calibrate
from prismatic.review_factory.calibrate import (
    PROPOSAL_VERSION,
    CalibrationNotApproved,
    build_proposal,
    load_calibration,
    main,
)
from prismatic.review_factory.learn_loop import (
    OUTCOME_HUMAN_OVERRRODE_DETERMINISTIC,
    OUTCOME_HUMAN_OVERRRODE_PAUSE,
    OUTCOME_JEV_PAUSE_PRECISION,
    OUTCOME_MERGED,
    OUTCOMES,
    LearnInputError,
    LearnLoop,
)

HERE = Path(__file__).resolve()
SPEC_LEARN = HERE.parent.parent / "spec" / "learn_loop_policy_v1.yaml"
SPEC_BANDS = HERE.parent.parent / "spec" / "auto_merge_bands_v1.yaml"
SPEC_PROPOSAL = HERE.parent.parent / "spec" / "merge_bar_calibration_v1.yaml"


def _row(number, merged_at, title, checks=(), reverts=()):
    return {
        "number": number,
        "merged_at": merged_at,
        "title": title,
        "checks": [{"name": n, "conclusion": c} for n, c in checks],
        "reverts": list(reverts),
    }


def _fixture_rows():
    # PR 101: ruff-lint + cancelled-runs red at merge, merged anyway, clean.
    # PR 103: style-check red at merge, reverted by PR 104 within 14 days.
    return [
        _row(
            101,
            "2026-09-01T12:00:00Z",
            "repair loop fix (#101)",
            [("tests", "success"), ("ruff-lint", "failure"),
             ("cancelled-runs", "cancelled")],
        ),
        _row(
            102,
            "2026-09-05T12:00:00Z",
            "novelty screen (#102)",
            [("tests", "success"), ("ruff-lint", "success")],
        ),
        _row(
            103,
            "2026-09-10T12:00:00Z",
            "risky refactor (#103)",
            [("tests", "success"), ("style-check", "failure")],
        ),
        _row(
            104,
            "2026-09-12T12:00:00Z",
            'Revert "risky refactor (#103)"',
            [],
            reverts=[103],
        ),
    ]


# ── proposal construction ────────────────────────────────────────────


def test_build_proposal_tolerated_and_never_red_match_fixture():
    proposal = build_proposal(_fixture_rows())
    assert proposal["version"] == PROPOSAL_VERSION
    assert proposal["status"] == "proposed"
    bar = proposal["demonstrated_bar"]
    # ruff-lint + cancelled-runs: red at merge, never followed by a revert.
    assert bar["checks_tolerated_red"] == ["cancelled-runs", "ruff-lint"]
    # tests: hardcoded never-red even though always green here; style-check
    # was red on a PR that got reverted, so it stays strict.
    assert bar["checks_never_red"] == ["style-check", "tests"]
    assert bar["tier_ceiling_without_human"] == 1
    assert any("style-check" in s for s in proposal["rollback_signals"])
    assert any("#103" in s for s in proposal["rollback_signals"])
    ev = proposal["evidence"]
    assert ev["merged_prs_scanned"] == 4
    assert ev["prs_with_check_data"] == 3
    assert ev["history_thin"] is False


def test_build_proposal_never_red_not_calibratable():
    # tests red at merge with no rollback: STILL never_red (§10 — the set
    # is hardcoded, not evidence-derived).
    rows = [
        _row(
            201,
            "2026-09-01T12:00:00Z",
            "forced through (#201)",
            [("tests", "failure"), ("ruff-lint", "success")],
        )
    ]
    proposal = build_proposal(rows)
    bar = proposal["demonstrated_bar"]
    assert "tests" in bar["checks_never_red"]
    assert "tests" not in bar["checks_tolerated_red"]


def test_build_proposal_thin_history_uses_safe_defaults():
    rows = [_row(301, "2026-09-01T12:00:00Z", "no check data (#301)")]
    proposal = build_proposal(rows)
    assert proposal["evidence"]["history_thin"] is True
    bar = proposal["demonstrated_bar"]
    assert bar["checks_tolerated_red"] == []
    assert bar["checks_never_red"] == []
    assert bar["tier_ceiling_without_human"] == 1


# ── loader gate: proposals are invisible to the live bar ─────────────


def test_committed_proposal_file_has_status_proposed():
    assert SPEC_PROPOSAL.exists(), "the initial proposal must be checked in"
    data = yaml.safe_load(SPEC_PROPOSAL.read_text(encoding="utf-8"))
    assert data["version"] == PROPOSAL_VERSION
    assert data["status"] == "proposed"
    # The file must say, in its own comments, that the live bar may not
    # read it until a human approves.
    text = SPEC_PROPOSAL.read_text(encoding="utf-8")
    assert "must NOT read" in text or "must not read" in text.lower()


def test_loader_refuses_proposed_status():
    with pytest.raises(CalibrationNotApproved):
        load_calibration(SPEC_PROPOSAL)


def test_loader_refuses_wrong_version_and_missing_file(tmp_path):
    bad = tmp_path / "cal.yaml"
    bad.write_text(
        yaml.safe_dump({"version": "merge_bar_calibration_v0",
                        "status": "approved"}),
        encoding="utf-8",
    )
    with pytest.raises(CalibrationNotApproved):
        load_calibration(bad)
    with pytest.raises(CalibrationNotApproved):
        load_calibration(tmp_path / "does-not-exist.yaml")


def test_loader_accepts_approved_status(tmp_path):
    good = tmp_path / "cal.yaml"
    good.write_text(
        yaml.safe_dump(
            {
                "version": PROPOSAL_VERSION,
                "status": "approved",
                "demonstrated_bar": {"tier_ceiling_without_human": 1},
            }
        ),
        encoding="utf-8",
    )
    data = load_calibration(good)
    assert data["status"] == "approved"


# ── new learn-loop outcome types ─────────────────────────────────────


def _enabled_loop(tmp_path):
    policy_data = yaml.safe_load(SPEC_LEARN.read_text(encoding="utf-8"))
    policy_data["enabled"] = True
    policy = tmp_path / "learn_loop_policy_test.yaml"
    policy.write_text(yaml.safe_dump(policy_data), encoding="utf-8")
    bands = tmp_path / "auto_merge_bands_test.yaml"
    bands.write_text(
        yaml.safe_dump(yaml.safe_load(SPEC_BANDS.read_text(encoding="utf-8"))),
        encoding="utf-8",
    )
    return LearnLoop(
        policy,
        bands,
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "learn-audit.jsonl",
    )


def _write_decision(path, job_id):
    row = {
        "ts": 1_700_000_000,
        "ts_iso": "2026-09-22T00:00:00+00:00",
        "component": "merge_authority",
        "policy_version": "auto-v1",
        "job_id": job_id,
        "repository": "mbgulden/prismatic-engine",
        "head_sha": "abc123",
        "tier": 1,
        "decision": "allowed",
        "reason": "test",
        "gates": [],
        "jev_score": None,
        "merge_sha": "def456",
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_new_outcome_types_are_in_enum():
    for outcome in (
        OUTCOME_MERGED,
        OUTCOME_HUMAN_OVERRRODE_PAUSE,
        OUTCOME_HUMAN_OVERRRODE_DETERMINISTIC,
        OUTCOME_JEV_PAUSE_PRECISION,
    ):
        assert outcome in OUTCOMES
    # Existing outcomes are untouched.
    assert {"clean", "rolled_back", "escalated"} <= OUTCOMES


def test_new_outcome_types_record_through_validation_path(tmp_path):
    loop = _enabled_loop(tmp_path)
    jobs = {
        OUTCOME_MERGED: "job-merged",
        OUTCOME_HUMAN_OVERRRODE_PAUSE: "job-pause",
        OUTCOME_HUMAN_OVERRRODE_DETERMINISTIC: "job-det",
        OUTCOME_JEV_PAUSE_PRECISION: "job-precision",
    }
    for job_id in jobs.values():
        _write_decision(loop.decision_log, job_id)
    for outcome, job_id in jobs.items():
        result = loop.record_outcome(job_id, outcome)
        assert result["status"] == "ok"
        assert result["outcome"] == outcome
    recorded = {
        json.loads(line)["outcome"]
        for line in (tmp_path / "outcomes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    assert set(jobs) == recorded


def test_unknown_outcome_still_rejected(tmp_path):
    loop = _enabled_loop(tmp_path)
    _write_decision(loop.decision_log, "job-x")
    with pytest.raises(LearnInputError):
        loop.record_outcome("job-x", "invented_outcome")


# ── CLI: --dry-run / --output ────────────────────────────────────────


def test_cli_dry_run_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        calibrate, "scan_git_history", lambda repo, limit=200: _fixture_rows()
    )
    out = tmp_path / "proposal.yaml"
    rc = main(["--repo", ".", "--output", str(out), "--dry-run"])
    assert rc == 0
    assert not out.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == PROPOSAL_VERSION
    assert payload["status"] == "proposed"


def test_cli_output_flag_writes_arbitrary_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        calibrate, "scan_git_history", lambda repo, limit=200: _fixture_rows()
    )
    out = tmp_path / "nested" / "proposal.yaml"
    rc = main(["--repo", ".", "--output", str(out)])
    assert rc == 0
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["version"] == PROPOSAL_VERSION
    assert data["status"] == "proposed"
    assert data["demonstrated_bar"]["checks_tolerated_red"] == [
        "cancelled-runs",
        "ruff-lint",
    ]
