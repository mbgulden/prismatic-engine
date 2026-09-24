"""Workstream A tests: the data layer (artifact + verdict).

Covers plan §9 for the data layer:
  - artifact_id determinism (same input → same id)
  - validate() rejects unknown fields and oversize diffs
  - explicit_gaps never fabricated (null ↔ gap correspondence, both directions)
  - replay() reproduces the deterministic verdict byte-identically
  - re-judgment creates a new verdict linked via supersedes (old one untouched)

All storage is redirected to a tmp dir via PRISMATIC_AUDIT_DIR; nothing
touches the real ~/.prismatic/audit.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from prismatic.review_factory.artifact import (
    MAX_UNIFIED_DIFF_BYTES,
    ArtifactValidationError,
    CheckResult,
    Diff,
    DiffFile,
    Intent,
    NoveltyContext,
    ReviewArtifact,
    load_artifact,
    store_artifact,
)
from prismatic.review_factory.verdict import (
    VerdictValidationError,
    decide,
    default_deterministic_runner,
    load_verdict,
    rejudge,
    replay,
    store_verdict,
    verdicts_for_artifact,
)


@pytest.fixture()
def audit_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_AUDIT_DIR", str(tmp_path))
    return tmp_path


def _make_artifact(**overrides) -> ReviewArtifact:
    kwargs = {
        "harness_id": "hermes",
        "harness_run_id": "run-123",
        "submitted_at": "2026-09-23T17:00:00+00:00",
        "intent": Intent(
            plan_ref="plans/x.md",
            brief="fix the thing",
            goals=["thing is fixed", "tests pass"],
        ),
        "diff": Diff(
            base_tree="a" * 64,
            head_tree="b" * 64,
            unified="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n",
            files=[
                DiffFile(
                    path="x.py", change_type="modified", lines_added=1, lines_removed=1
                )
            ],
        ),
        "checks": [
            CheckResult(
                name="pytest",
                exit_code=0,
                log_sha256="c" * 64,
                ran_at="2026-09-23T17:01:00+00:00",
            )
        ],
        "prior_receipts": ["receipt-1"],
        "novelty_context": NoveltyContext(first_seen_paths=["x.py"]),
        "explicit_gaps": [],
    }
    kwargs.update(overrides)
    return ReviewArtifact.create(**kwargs)


# ── artifact_id determinism ──────────────────────────────────────────


def test_artifact_id_deterministic():
    a = _make_artifact()
    b = _make_artifact()
    assert a.artifact_id == b.artifact_id
    assert len(a.artifact_id) == 64  # sha256 hex


def test_artifact_id_changes_with_content():
    a = _make_artifact()
    b = _make_artifact(harness_run_id="run-456")
    assert a.artifact_id != b.artifact_id


def test_artifact_id_matches_canonical_hash():
    a = _make_artifact()
    core = {k: v for k, v in a.to_dict().items() if k != "artifact_id"}
    canonical = json.dumps(
        core, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert a.artifact_id == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_round_trip_from_dict_preserves_id():
    a = _make_artifact()
    b = ReviewArtifact.from_dict(a.to_dict())
    assert b.artifact_id == a.artifact_id
    assert b == a


def test_tampered_id_rejected():
    a = _make_artifact()
    raw = a.to_dict()
    raw["artifact_id"] = "0" * 64
    with pytest.raises(ArtifactValidationError):
        ReviewArtifact.from_dict(raw)


# ── validate(): unknown fields & oversize diffs ──────────────────────


def test_validate_rejects_unknown_top_level_field():
    raw = _make_artifact().to_dict()
    raw["harness_version"] = "9.9.9"
    with pytest.raises(ArtifactValidationError, match="unknown field"):
        ReviewArtifact.from_dict(raw)


def test_validate_rejects_unknown_nested_field():
    raw = _make_artifact().to_dict()
    raw["intent"]["author"] = "someone"
    with pytest.raises(ArtifactValidationError, match="unknown field"):
        ReviewArtifact.from_dict(raw)
    raw = _make_artifact().to_dict()
    raw["diff"]["files"][0]["owner"] = "someone"
    with pytest.raises(ArtifactValidationError, match="unknown field"):
        ReviewArtifact.from_dict(raw)


def test_validate_rejects_unknown_harness_id():
    raw = _make_artifact().to_dict()
    raw["harness_id"] = "mystery-harness"
    # Recompute the id so the failure is on harness_id, not the hash check.
    core = {k: v for k, v in raw.items() if k != "artifact_id"}
    canonical = json.dumps(
        core, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    raw["artifact_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with pytest.raises(ArtifactValidationError, match="harness_id"):
        ReviewArtifact.from_dict(raw)


def test_validate_rejects_oversize_unified_diff():
    raw = _make_artifact().to_dict()
    raw["diff"]["unified"] = "x" * (MAX_UNIFIED_DIFF_BYTES + 1)
    with pytest.raises(ArtifactValidationError, match="size cap"):
        ReviewArtifact.from_dict(raw)


def test_validate_rejects_bad_change_type():
    raw = _make_artifact().to_dict()
    raw["diff"]["files"][0]["change_type"] = "moved-around"
    with pytest.raises(ArtifactValidationError, match="change_type"):
        ReviewArtifact.from_dict(raw)


def test_validate_rejects_negative_line_counts():
    raw = _make_artifact().to_dict()
    raw["diff"]["files"][0]["lines_added"] = -3
    with pytest.raises(ArtifactValidationError, match="non-negative"):
        ReviewArtifact.from_dict(raw)


# ── explicit_gaps: never fabricated ──────────────────────────────────


def test_null_without_gap_rejected():
    """A null field that is not listed under explicit_gaps fails closed."""
    a = _make_artifact(
        intent=Intent(plan_ref=None, brief="fix the thing", goals=[]),
        explicit_gaps=[],
    )
    with pytest.raises(ArtifactValidationError, match="explicit_gaps"):
        # create() does not validate; from_dict(to_dict()) does.
        ReviewArtifact.from_dict(a.to_dict())


def test_gap_without_null_rejected():
    """An explicit_gaps entry naming a non-null field fails closed."""
    a = _make_artifact(explicit_gaps=["intent.plan_ref"])
    with pytest.raises(ArtifactValidationError, match="explicit_gaps"):
        ReviewArtifact.from_dict(a.to_dict())


def test_null_with_gap_accepted():
    """The honest path: null + listed gap validates."""
    a = _make_artifact(
        intent=Intent(plan_ref=None, brief=None, goals=[]),
        diff=Diff(
            base_tree=None,
            head_tree=None,
            unified=None,
            files=[],
        ),
        explicit_gaps=[
            "intent.plan_ref",
            "intent.brief",
            "diff.base_tree",
            "diff.head_tree",
            "diff.unified",
        ],
    )
    b = ReviewArtifact.from_dict(a.to_dict())  # must not raise
    assert b.artifact_id == a.artifact_id


def test_gap_naming_unknown_field_rejected():
    a = _make_artifact(explicit_gaps=["intent.telepathy"])
    with pytest.raises(ArtifactValidationError, match="explicit_gaps"):
        ReviewArtifact.from_dict(a.to_dict())


# ── write-once store ─────────────────────────────────────────────────


def test_store_is_write_once(audit_dir):
    a = _make_artifact()
    store_artifact(a)
    store_artifact(a)  # no-op
    path = os.path.join(str(audit_dir), "review-artifacts.jsonl")
    with open(path) as fh:
        lines = [ln for ln in fh if ln.strip()]
    assert len(lines) == 1
    assert load_artifact(a.artifact_id) == a


def test_load_missing_artifact_raises(audit_dir):
    with pytest.raises(KeyError):
        load_artifact("f" * 64)


# ── verdict: decide / replay / rejudge ───────────────────────────────


def _judgment(decision="CLEAR"):
    return {
        "judge": "jev",
        "decision": decision,
        "confidence": 0.9,
        "reasons": [
            {
                "question": "intent-vs-implementation",
                "finding": "diff matches goals",
                "severity": "low",
            }
        ],
        "trace_id": "trace-1",
    }


def test_decide_clean_verdict(audit_dir):
    v = decide(_make_artifact())
    assert v.schema_version == "verdict-v1"
    assert v.deterministic_verdict == "CLEAN"
    assert v.final == "CLEAN"
    assert v.judgment is None
    assert v.supersedes is None
    assert load_verdict(v.verdict_id) == v


def test_decide_repair_on_red_checks(audit_dir):
    a = _make_artifact(
        checks=[
            CheckResult(name="pytest", exit_code=1, log_sha256="d" * 64, ran_at=None)
        ],
        explicit_gaps=["checks[].ran_at"],
    )
    v = decide(a)
    assert v.deterministic_verdict == "REPAIR"
    assert v.final == "REPAIR"


def test_decide_reject_when_no_checks(audit_dir):
    a = _make_artifact(checks=[])
    v = decide(a)
    assert v.deterministic_verdict == "REJECT"
    assert v.final == "REJECT"


def test_decide_rejects_judgment_on_non_clean(audit_dir):
    a = _make_artifact(
        checks=[CheckResult(name="pytest", exit_code=1, log_sha256=None, ran_at=None)],
        explicit_gaps=["checks[].log_sha256", "checks[].ran_at"],
    )
    with pytest.raises(VerdictValidationError):
        decide(a, judgment=_judgment())


def test_decide_pause_escalates(audit_dir):
    v = decide(_make_artifact(), judgment=_judgment(decision="PAUSE"))
    assert v.final == "ESCALATE"
    assert v.judgment["decision"] == "PAUSE"


def test_decide_clear_keeps_clean(audit_dir):
    v = decide(_make_artifact(), judgment=_judgment(decision="CLEAR"))
    assert v.final == "CLEAN"


def test_replay_reproduces_byte_identically(audit_dir):
    v = decide(_make_artifact())
    result = replay(v.verdict_id)
    assert result.reproduced is True
    assert result.expected_canonical == result.actual_canonical
    # byte-identical on the canonical form:
    assert result.actual_canonical.encode("utf-8") == result.expected_canonical.encode(
        "utf-8"
    )


def test_replay_detects_drift(audit_dir):
    v = decide(_make_artifact())

    def lying_runner(_artifact):
        return {"verdict": "REJECT", "receipt_ids": []}

    result = replay(v.verdict_id, runner=lying_runner)
    assert result.reproduced is False
    assert result.expected_canonical != result.actual_canonical


def test_replay_uses_default_runner(audit_dir):
    from prismatic.review_factory import verdict as verdict_mod

    verdict_mod.register_runner(default_deterministic_runner)
    try:
        v = decide(_make_artifact())
        result = replay(v.verdict_id)  # no explicit runner
        assert result.reproduced is True
    finally:
        verdict_mod.register_runner(None)


def test_rejudge_links_supersedes(audit_dir):
    v1 = decide(_make_artifact(), judgment=_judgment(decision="CLEAR"))
    v2 = rejudge(v1.verdict_id, _judgment(decision="PAUSE"))
    assert v2.verdict_id != v1.verdict_id
    assert v2.supersedes == v1.verdict_id
    assert v2.final == "ESCALATE"
    assert v2.artifact_id == v1.artifact_id
    # The old verdict is untouched (write-once).
    assert load_verdict(v1.verdict_id).final == "CLEAN"
    assert load_verdict(v1.verdict_id).supersedes is None
    # Both verdicts are listed for the artifact.
    assert [v.verdict_id for v in verdicts_for_artifact(v1.artifact_id)] == [
        v1.verdict_id,
        v2.verdict_id,
    ]


def test_rejudge_rejects_non_clean(audit_dir):
    a = _make_artifact(checks=[])
    v = decide(a)
    with pytest.raises(VerdictValidationError):
        rejudge(v.verdict_id, _judgment())


def test_verdict_store_is_write_once(audit_dir):
    v = decide(_make_artifact())
    store_verdict(v)  # no-op
    path = os.path.join(str(audit_dir), "review-verdicts.jsonl")
    with open(path) as fh:
        lines = [ln for ln in fh if ln.strip()]
    assert len(lines) == 1


def test_verdict_rejects_unknown_fields(audit_dir):
    v = decide(_make_artifact())
    raw = v.to_dict()
    raw["reviewer_mood"] = "great"
    from prismatic.review_factory.verdict import validate_verdict

    with pytest.raises(VerdictValidationError, match="unknown field"):
        validate_verdict(raw)


def test_judgment_skipped_recorded(audit_dir):
    v = decide(_make_artifact(), judgment_skipped="tier-0")
    assert v.judgment is None
    assert v.judgment_skipped == "tier-0"
    assert v.final == "CLEAN"
    assert load_verdict(v.verdict_id) == v
