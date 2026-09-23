"""Tests for the deterministic novelty detector + quarantine path (chunk 3).

Every test asserts a safety property the detector claims:
- disabled means inert: no input read, no trips, no quarantine;
- the Jev slot is data-in only and INACTIVE until Jev exists;
- monitor-only trips are logged, never quarantining or halting;
- enforcing trips quarantine and STAY quarantined until Michael releases;
- invalid or unknown input cannot produce a "familiar" verdict (fail-closed);
- every evaluation emits exactly one audit signal.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory.novelty import (
    DEFAULT_REARM_PRINCIPAL,
    MODE_ENFORCING,
    MODE_MONITOR_ONLY,
    STATE_DISABLED,
    STATE_FAMILIAR,
    STATE_INVALID,
    STATE_NOVEL_MONITOR,
    STATE_NOVEL_QUARANTINED,
    NoveltyDetector,
    NoveltyConfigError,
    NoveltyInput,
    load_novelty_policy,
)

HERE = Path(__file__).resolve()
SPEC_YAML = HERE.parent.parent / "spec" / "novelty_policy_v1.yaml"


# ── fixtures ─────────────────────────────────────────────────────────


def _write_policy(tmp_path, **overrides):
    data = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        data[key] = value
    path = tmp_path / "novelty_policy_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _detector(tmp_path, **overrides):
    return NoveltyDetector(
        _write_policy(tmp_path, **overrides),
        audit_log=tmp_path / "novelty-audit.jsonl",
    )


def _monitor_detector(tmp_path):
    return _detector(tmp_path, enabled=True, mode=MODE_MONITOR_ONLY)


def _enforcing_detector(tmp_path):
    return _detector(tmp_path, enabled=True, mode=MODE_ENFORCING)


def _familiar_input(**overrides):
    base = {
        "candidate_id": "cand-1",
        "jev_confidences": None,
        "precedent_matches": 5,
        "change_shape": {"tier": 0, "files": 2},
        "error_classes": ("timeout",),
        "known_error_classes": frozenset({"timeout", "auth"}),
        "event_types": ("merge",),
        "seen_event_types": frozenset({"merge", "deploy"}),
        "input_schema_hash": "abc",
        "expected_schema_hash": "abc",
    }
    base.update(overrides)
    return NoveltyInput(**base)


def _audit_rows(tmp_path):
    path = tmp_path / "novelty-audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ── policy loading (5) ───────────────────────────────────────────────


def test_shipped_policy_defaults():
    policy = load_novelty_policy(SPEC_YAML)
    assert policy.enabled is False
    assert policy.mode == MODE_MONITOR_ONLY
    assert policy.rearm_principal == DEFAULT_REARM_PRINCIPAL == "mbgulden"
    assert policy.jev_low_confidence == 0.35
    assert policy.version == "novelty-v1"


def test_missing_policy_file_is_disabled_not_error(tmp_path):
    policy = load_novelty_policy(tmp_path / "does-not-exist.yaml")
    assert policy.enabled is False
    det = NoveltyDetector(
        tmp_path / "does-not-exist.yaml",
        audit_log=tmp_path / "novelty-audit.jsonl",
    )
    result = det.evaluate(_familiar_input())
    assert result.state == STATE_DISABLED


def test_malformed_policy_raises_on_load(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("enabled: true\nmode: [unclosed\n", encoding="utf-8")
    with pytest.raises(NoveltyConfigError):
        load_novelty_policy(bad)


def test_malformed_policy_detector_never_evaluates(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("enabled: true\nmode: [unclosed\n", encoding="utf-8")
    det = NoveltyDetector(bad, audit_log=tmp_path / "novelty-audit.jsonl")
    result = det.evaluate(_familiar_input(jev_confidences={"a": 0.99}))
    assert result.state == STATE_INVALID
    assert result.quarantined is False
    assert result.pipeline_halted is False
    assert det.quarantined_ids == ()


def test_unknown_mode_is_config_error(tmp_path):
    path = _write_policy(tmp_path, mode="turbo")
    with pytest.raises(NoveltyConfigError):
        load_novelty_policy(path)


# ── disabled inertness (2) ──────────────────────────────────────────


def test_disabled_returns_disabled_without_reading_inputs(tmp_path):
    det = _detector(tmp_path)  # shipped state: enabled: false
    # Poisoned input that would explode on any read: still disabled.
    result = det.evaluate(_familiar_input(jev_confidences={"a": float("nan")}))
    assert result.state == STATE_DISABLED
    assert result.quarantined is False
    assert result.pipeline_halted is False


def test_disabled_creates_no_quarantine_entries(tmp_path):
    det = _detector(tmp_path)
    det.evaluate(_familiar_input(candidate_id="cand-x", error_classes=("boom",)))
    det.release("mbgulden", "cand-x")
    assert det.quarantined_ids == ()


# ── input (a): Jev slot (6) ─────────────────────────────────────────


def test_jev_none_is_unavailable_never_trips(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input(jev_confidences=None))
    assert result.state == STATE_FAMILIAR
    assert result.trips == ()


def test_jev_all_below_threshold_trips(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(jev_confidences={"merge": 0.10, "escalate": 0.20})
    )
    assert result.state == STATE_NOVEL_MONITOR
    assert [t.input for t in result.trips] == ["jev_all_low_confidence"]


def test_jev_one_above_threshold_no_trip(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(jev_confidences={"merge": 0.10, "escalate": 0.90})
    )
    assert result.state == STATE_FAMILIAR


def test_jev_exactly_at_threshold_does_not_trip(tmp_path):
    det = _monitor_detector(tmp_path)
    # Exactly at the threshold is not "below" — no trip.
    result = det.evaluate(_familiar_input(jev_confidences={"merge": 0.35}))
    assert result.state == STATE_FAMILIAR
    # Just below the threshold trips (strictly-below semantics).
    result = det.evaluate(_familiar_input(jev_confidences={"merge": 0.3499}))
    assert result.state == STATE_NOVEL_MONITOR


def test_jev_empty_dict_is_malformed_invalid(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input(jev_confidences={}))
    assert result.state == STATE_INVALID


def test_jev_out_of_range_confidence_is_invalid(tmp_path):
    det = _monitor_detector(tmp_path)
    for bad in (float("nan"), float("inf"), -0.1, 1.5, "high"):
        result = det.evaluate(_familiar_input(jev_confidences={"a": bad}))
        assert result.state == STATE_INVALID, bad


# ── input (b): precedent (4) ────────────────────────────────────────


def test_zero_precedent_matches_trips(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(precedent_matches=0, change_shape={"tier": 1})
    )
    assert result.state == STATE_NOVEL_MONITOR
    assert [t.input for t in result.trips] == ["no_precedent"]
    assert "0" in result.trips[0].evidence


def test_precedent_matches_clear(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input(precedent_matches=3))
    assert result.state == STATE_FAMILIAR


def test_precedent_none_is_unavailable_never_trips(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input(precedent_matches=None))
    assert result.state == STATE_FAMILIAR


def test_negative_precedent_count_is_invalid(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input(precedent_matches=-1))
    assert result.state == STATE_INVALID


# ── input (c): tripwires (6) ────────────────────────────────────────


def test_unknown_error_class_trips(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(
            error_classes=("timeout", "never_seen"),
            known_error_classes=frozenset({"timeout"}),
        )
    )
    assert result.state == STATE_NOVEL_MONITOR
    assert result.trips[0].input == "unknown_error_class"
    assert "never_seen" in result.trips[0].evidence


def test_all_known_error_classes_clear(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input())
    assert result.state == STATE_FAMILIAR


def test_first_time_event_type_trips(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(
            event_types=("merge", "brand_new"),
            seen_event_types=frozenset({"merge"}),
        )
    )
    assert result.state == STATE_NOVEL_MONITOR
    assert result.trips[0].input == "first_time_event"


def test_schema_hash_mismatch_trips(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(input_schema_hash="aaa", expected_schema_hash="bbb")
    )
    assert result.state == STATE_NOVEL_MONITOR
    assert result.trips[0].input == "schema_change"


def test_schema_hash_match_clear(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(input_schema_hash="aaa", expected_schema_hash="aaa")
    )
    assert result.state == STATE_FAMILIAR


def test_schema_hash_one_missing_is_unavailable(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(input_schema_hash=None, expected_schema_hash="aaa")
    )
    assert result.state == STATE_FAMILIAR
    result2 = det.evaluate(
        _familiar_input(input_schema_hash="aaa", expected_schema_hash=None)
    )
    assert result2.state == STATE_FAMILIAR


# ── monitor-only behavior (4) ───────────────────────────────────────


def test_monitor_only_trip_logs_but_does_not_quarantine(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input(precedent_matches=0))
    assert result.state == STATE_NOVEL_MONITOR
    assert result.quarantined is False
    assert result.pipeline_halted is False
    assert det.quarantined_ids == ()


def test_monitor_only_clean_is_familiar(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input())
    assert result.state == STATE_FAMILIAR
    assert result.quarantined is False
    assert result.pipeline_halted is False


def test_monitor_only_invalid_input_logged(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input(candidate_id=""))
    assert result.state == STATE_INVALID
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["state"] == STATE_INVALID


def test_monitor_only_quarantine_registry_stays_empty(tmp_path):
    det = _monitor_detector(tmp_path)
    for i in range(3):
        det.evaluate(_familiar_input(candidate_id=f"c{i}", precedent_matches=0))
    assert det.quarantined_ids == ()


# ── enforcing behavior (7) ──────────────────────────────────────────


def test_enforcing_trip_quarantines_and_halts(tmp_path):
    det = _enforcing_detector(tmp_path)
    result = det.evaluate(_familiar_input(precedent_matches=0))
    assert result.state == STATE_NOVEL_QUARANTINED
    assert result.quarantined is True
    assert result.pipeline_halted is True
    assert det.quarantined_ids == ("cand-1",)


def test_enforcing_stays_quarantined_on_reevaluate(tmp_path):
    det = _enforcing_detector(tmp_path)
    det.evaluate(_familiar_input(precedent_matches=0))
    # Re-evaluate with a clean input: stays quarantined, pipeline halted.
    result = det.evaluate(_familiar_input())
    assert result.state == STATE_NOVEL_QUARANTINED
    assert result.quarantined is True
    assert result.pipeline_halted is True


def test_enforcing_release_by_principal(tmp_path):
    det = _enforcing_detector(tmp_path)
    det.evaluate(_familiar_input(precedent_matches=0))
    assert det.release("mbgulden", "cand-1") == "released"
    assert det.quarantined_ids == ()
    # After release, a clean input is familiar again.
    result = det.evaluate(_familiar_input())
    assert result.state == STATE_FAMILIAR


def test_enforcing_release_wrong_principal_refused(tmp_path):
    det = _enforcing_detector(tmp_path)
    det.evaluate(_familiar_input(precedent_matches=0))
    assert det.release("someone_else", "cand-1") == "refused"
    assert det.quarantined_ids == ("cand-1",)


def test_enforcing_release_not_quarantined_is_noop(tmp_path):
    det = _enforcing_detector(tmp_path)
    assert det.release("mbgulden", "ghost") == "noop"


def test_audit_dir_removed_mid_run_self_heals(tmp_path):
    """Deleting the audit dir mid-run must not drop later signals or verdicts.

    Covers both the evaluate-audit and the release-audit paths: each
    re-creates the dir and writes its row after the removal, and verdicts
    (quarantine, release, familiar) stand throughout (fail-closed).
    """
    log = tmp_path / "nested" / "audit.jsonl"
    det = NoveltyDetector(
        _write_policy(tmp_path, enabled=True, mode=MODE_ENFORCING),
        audit_log=log,
    )
    result = det.evaluate(_familiar_input(precedent_matches=0))
    assert result.state == STATE_NOVEL_QUARANTINED
    assert det.quarantined_ids == ("cand-1",)
    shutil.rmtree(tmp_path / "nested")
    # Release writes via the release-audit path on a fresh dir.
    assert det.release("mbgulden", "cand-1") == "released"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["event"] == "quarantine_release"
    assert rows[0]["outcome"] == "released"
    # And the evaluate-audit path self-heals too.
    result = det.evaluate(_familiar_input())
    assert result.state == STATE_FAMILIAR
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[1]["state"] == STATE_FAMILIAR


def test_enforcing_invalid_input_is_contained_fail_closed(tmp_path):
    det = _enforcing_detector(tmp_path)
    result = det.evaluate(_familiar_input(jev_confidences={}))
    assert result.state == STATE_NOVEL_QUARANTINED
    assert result.quarantined is True
    assert result.pipeline_halted is True
    assert det.quarantined_ids == ("cand-1",)


def test_enforcing_jev_slot_stays_unavailable_not_unknown(tmp_path):
    det = _enforcing_detector(tmp_path)
    # Jev not consulted: the slot is inactive — clean input, no containment.
    result = det.evaluate(_familiar_input(jev_confidences=None))
    assert result.state == STATE_FAMILIAR
    assert det.quarantined_ids == ()


# ── audit + page (6) ────────────────────────────────────────────────


def test_exactly_one_audit_row_per_evaluate(tmp_path):
    det = _monitor_detector(tmp_path)
    det.evaluate(_familiar_input(precedent_matches=0))
    det.evaluate(_familiar_input())
    rows = _audit_rows(tmp_path)
    assert len(rows) == 2


def test_audit_row_shape(tmp_path):
    det = _enforcing_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(precedent_matches=0, candidate_id="page-cand")
    )
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["component"] == "novelty-detector"
    assert row["candidate_id"] == "page-cand"
    assert row["tripped_inputs"] == ["no_precedent"]
    assert row["quarantined"] is True
    assert row["pipeline_halted"] is True
    assert result.policy_version == "novelty-v1"


def test_malformed_policy_detector_still_emits_invalid_row(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("enabled: true\nmode: [unclosed\n", encoding="utf-8")
    det = NoveltyDetector(bad, audit_log=tmp_path / "novelty-audit.jsonl")
    det.evaluate(_familiar_input())
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["state"] == STATE_INVALID
    assert rows[0]["component"] == "novelty-detector"


def test_page_top_signals_bounded_with_summary(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(
        _familiar_input(
            jev_confidences={"a": 0.1},
            precedent_matches=0,
            error_classes=("weird",),
            event_types=("brand_new",),
            input_schema_hash="aaa",
            expected_schema_hash="bbb",
        )
    )
    page = NoveltyDetector.prepare_page(result)
    assert len(page["top_signals"]) <= 3
    assert isinstance(page["summary"], str) and page["summary"]
    # All 5 trips recorded in the full list.
    assert len(page["trips"]) == 5
    # Deterministic relevance order: Jev slot first.
    assert page["top_signals"][0].startswith("jev_all_low_confidence:")


def test_page_is_data_only_no_send(tmp_path):
    det = _monitor_detector(tmp_path)
    result = det.evaluate(_familiar_input(precedent_matches=0))
    before = _audit_rows(tmp_path)
    page = NoveltyDetector.prepare_page(result)
    assert isinstance(page, dict)
    # Preparing the page has no side effects: no new audit rows, no send.
    assert _audit_rows(tmp_path) == before


def test_page_names_release_authority(tmp_path):
    det = _enforcing_detector(tmp_path)
    result = det.evaluate(_familiar_input(precedent_matches=0))
    page = NoveltyDetector.prepare_page(result)
    assert page["release_principal"] == "mbgulden"
    assert any("mbgulden" in s for s in page["top_signals"]) or (
        "release authority" in page["top_signals"][-1]
        and "mbgulden" in page["top_signals"][-1]
    )


# ── wiring: disabled audit row carries the real candidate id (§2.1) ───


def test_disabled_audit_row_carries_candidate_id(tmp_path):
    det = _detector(tmp_path)  # shipped state: enabled: false
    result = det.evaluate(_familiar_input(candidate_id="review-job-42"))
    assert result.state == STATE_DISABLED
    assert result.candidate_id == "review-job-42"
    # the emitted audit row is joinable back to the job (was "" before)
    rows = [
        json.loads(line)
        for line in (tmp_path / "novelty-audit.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["state"] == STATE_DISABLED
    assert rows[0]["candidate_id"] == "review-job-42"
