"""Tier-A advisory recalibration (2026-09-22).

A tier-A check that *completed with conclusion failure* is ADVISORY:
recorded in advisory_flags and visible in every decision record, but not
blocking. The split is deliberately narrow — only an explicit completed
failure is advisory. Cancelled, timed-out, skipped, unfinished,
never-triggered, missing, or unrecognized tier-A data stays fail-closed
(REJECT), because it carries no evidence about the gate's verdict.

Behavioral contract, per the policy versioning rule (behavioral changes
ship as a new policy version): shadow-v3 merges CLEAN and ADVISORY
verdicts; shadow-v2 is preserved untouched and still skips on ADVISORY.

Fail-closed is preserved for everything the recalibration does not
touch: no check-runs payload, unfinished runs, non-tier-A failures, and
the zero-bad-merge / merge-authority / phase-gate behavior are all
unchanged.
"""

from prismatic.review_factory.policy import PolicyEngine
from prismatic.review_factory.shadow_observer import (
    CALL_MERGE,
    CALL_SKIP,
    SPEC_DIR,
    RiskBands,
    ShadowPolicy,
    evaluate,
    load_bands,
    load_input_from_dict,
    load_policy,
)
from prismatic.review_factory.shadow_poller import (
    RF_GATE_CHECK_NAME,
    _tier_a_advisory,
    branch_protection_satisfied,
    build_input_dict,
    ci_green_self_hosted,
    review_verdict,
)

RUFF = "smoke (ruff lint)"
TIER_A = "review factory gate (tier A)"


def _run(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


def _pr(number=101, sha="a" * 40):
    return {
        "number": number,
        "title": "Test PR",
        "headRefOid": sha,
        "baseRefOid": "b" * 40,
        "mergeable": "MERGEABLE",
    }


def _policy_v3() -> ShadowPolicy:
    return load_policy()  # default is now shadow-v3


def _policy_v2() -> ShadowPolicy:
    return load_policy(SPEC_DIR / "shadow_merge_policy_v2.yaml")


def _bands() -> RiskBands:
    return load_bands(SPEC_DIR / "shadow_risk_bands_v1.yaml")


def _tier_engine() -> PolicyEngine:
    return PolicyEngine.from_yaml(SPEC_DIR / "policy_file_v1.yaml")


def _advisory_runs():
    # Tier-A completed with failure; everything else green.
    return [_run(RUFF), _run(TIER_A, conclusion="failure")]


# ── the verdict split ───────────────────────────────────────────────


def test_tier_a_success_is_clean():
    assert review_verdict([_run(TIER_A)]) == "CLEAN"


def test_tier_a_completed_failure_is_advisory():
    assert review_verdict([_run(TIER_A, conclusion="failure")]) == "ADVISORY"


def test_tier_a_non_failure_conclusions_stay_reject():
    for conclusion in ("cancelled", "timed_out", "skipped", "neutral", "stale"):
        runs = [_run(TIER_A, conclusion=conclusion)]
        assert review_verdict(runs) == "REJECT", conclusion


def test_tier_a_missing_or_unfinished_stays_reject():
    assert review_verdict([]) == "REJECT"  # no payload at all
    assert review_verdict([_run(RUFF)]) == "REJECT"  # check absent
    assert review_verdict([_run(TIER_A, status="in_progress")]) == "REJECT"


def test_tier_a_advisory_helper_is_narrow():
    assert _tier_a_advisory([_run(TIER_A, conclusion="failure")]) is True
    assert _tier_a_advisory([_run(TIER_A)]) is False
    assert _tier_a_advisory([_run(TIER_A, conclusion="cancelled")]) is False
    assert _tier_a_advisory([_run(RUFF)]) is False
    assert _tier_a_advisory([]) is False
    assert _tier_a_advisory(None) is False


# ── the flag flows through build_input_dict ─────────────────────────


def test_build_input_dict_flags_advisory_failure():
    d = build_input_dict(_pr(), _advisory_runs(), ["docs/a.md"])
    assert d["review_verdict"] == "ADVISORY"
    assert d["advisory_flags"] == [RF_GATE_CHECK_NAME]
    assert d["ci_green_self_hosted"] is True
    assert d["branch_protection_satisfied"] is True
    inp = load_input_from_dict(d)
    assert inp.advisory_flags == (RF_GATE_CHECK_NAME,)


def test_build_input_dict_no_flag_when_clean():
    d = build_input_dict(_pr(), [_run(RUFF), _run(TIER_A)], ["docs/a.md"])
    assert d["review_verdict"] == "CLEAN"
    assert d["advisory_flags"] == []
    assert load_input_from_dict(d).advisory_flags == ()


def test_build_input_dict_no_flag_when_tier_a_cancelled():
    d = build_input_dict(
        _pr(), [_run(RUFF), _run(TIER_A, conclusion="cancelled")], ["docs/a.md"]
    )
    assert d["review_verdict"] == "REJECT"
    assert d["advisory_flags"] == []


# ── ci_green_self_hosted: advisory excluded, nothing else changes ───


def test_ci_green_passes_with_advisory_tier_a_failure():
    assert ci_green_self_hosted(_advisory_runs()) is True


def test_ci_green_fails_when_nothing_green_to_stand_on():
    # Only the advisory tier-A check ran: no green check remains, so the
    # evaluation still fails closed.
    assert ci_green_self_hosted([_run(TIER_A, conclusion="failure")]) is False


def test_ci_green_still_fails_on_non_tier_a_failure():
    runs = [_run(RUFF, conclusion="failure"), _run(TIER_A, conclusion="failure")]
    assert ci_green_self_hosted(runs) is False


def test_ci_green_still_fails_on_tier_a_cancelled():
    runs = [_run(RUFF), _run(TIER_A, conclusion="cancelled")]
    assert ci_green_self_hosted(runs) is False


# ── branch-protection stand-in: advisory excluded, rest unchanged ────


def test_branch_protection_passes_with_advisory_tier_a_failure():
    assert branch_protection_satisfied(_advisory_runs()) is True


def test_branch_protection_fails_when_only_advisory_check_ran():
    assert branch_protection_satisfied([_run(TIER_A, conclusion="failure")]) is False


def test_branch_protection_still_fails_on_tier_a_cancelled():
    runs = [_run(RUFF), _run(TIER_A, conclusion="cancelled")]
    assert branch_protection_satisfied(runs) is False


# ── policy versioning: v3 merges advisory, v2 still skips ────────────


def test_default_policy_is_shadow_v3_with_advisory_mergeable():
    policy = _policy_v3()
    assert policy.version == "shadow-v3"
    assert "ADVISORY" in policy.verdicts_mergeable
    assert "CLEAN" in policy.verdicts_mergeable


def test_v2_policy_preserved_and_still_skips_advisory():
    policy = _policy_v2()
    assert policy.version == "shadow-v2"
    assert tuple(policy.verdicts_mergeable) == ("CLEAN",)


def _advisory_decision(policy: ShadowPolicy):
    d = build_input_dict(_pr(), _advisory_runs(), ["docs/a.md"])
    d["ruff_clean"] = True  # ruff_clean comes from the ruff check, not tier A
    inp = load_input_from_dict(d)
    return evaluate(inp, policy, _bands(), _tier_engine())


def test_observer_merges_advisory_under_v3_with_flag_visible():
    decision = _advisory_decision(_policy_v3())
    assert decision.call == CALL_MERGE
    assert decision.advisory_flags == (RF_GATE_CHECK_NAME,)
    assert decision.policy_version == "shadow-v3"
    # The verdict gate passed on ADVISORY — the flag is not a gate.
    verdict_gate = next(
        g for g in decision.gate_results if g.gate == "verdict_not_reject"
    )
    assert verdict_gate.passed is True
    # The flag is visible in the audit signal, not buried.
    signal = decision.to_signal()
    assert signal["metadata"]["advisory_flags"] == [RF_GATE_CHECK_NAME]
    assert "[advisory:" in signal["message"]


def test_observer_skips_advisory_under_v2():
    decision = _advisory_decision(_policy_v2())
    assert decision.call == CALL_SKIP
    assert decision.advisory_flags == (RF_GATE_CHECK_NAME,)
    assert decision.policy_version == "shadow-v2"
    assert any(
        g.gate == "verdict_not_reject" and not g.passed for g in decision.gate_results
    )


def test_advisory_flag_never_invents_a_merge():
    # Advisory only *unblocks* the tier-A failure; every other gate still
    # applies. A ruff failure alongside an advisory tier-A still skips.
    d = build_input_dict(
        _pr(),
        [_run(RUFF, conclusion="failure"), _run(TIER_A, conclusion="failure")],
        ["docs/a.md"],
    )
    d["ruff_clean"] = False
    inp = load_input_from_dict(d)
    decision = evaluate(inp, _policy_v3(), _bands(), _tier_engine())
    assert decision.call == CALL_SKIP
    assert decision.advisory_flags == (RF_GATE_CHECK_NAME,)
