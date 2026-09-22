"""Never-triggered checks are N/A for ci_green_self_hosted.

Recalibration (plan: never-triggered-checks-recalibration-plan.md,
2026-09-22): the shadow pipeline recommended "merge" exactly once in 60
replayed PRs because ``ci_green_self_hosted`` treated the path-conditional
``Verify shipped plugins load`` check (plugin-load.yml only triggers on
plugin-file changes) as a mandatory green. A check that never ran for the
PR head is now N/A — excluded from the green requirement.

Fail-closed is preserved for everything else: no check-runs payload,
scheduled-but-unfinished runs, failed/cancelled/timed-out runs, and
unrecognized conclusions all fail the evaluation. Plugin-file PRs still
actually run the plugin-load check, and a failure there still fails.
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
    SELF_HOSTED_CHECKS,
    _check_state,
    build_input_dict,
    ci_green_self_hosted,
)

RUFF = "smoke (ruff lint)"
TIER_A = "review factory gate (tier A)"
PLUGIN_LOAD = "Verify shipped plugins load"


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


def _policy() -> ShadowPolicy:
    return load_policy()


def _bands() -> RiskBands:
    return load_bands(SPEC_DIR / "shadow_risk_bands_v1.yaml")


def _tier_engine() -> PolicyEngine:
    return PolicyEngine.from_yaml(SPEC_DIR / "policy_file_v1.yaml")


# ── the recalibration ────────────────────────────────────────────────


def test_never_triggered_check_is_na():
    # Ordinary PR: plugin-load workflow never ran (paths: filter excluded
    # it), the two always-triggered checks are green.
    runs = [_run(RUFF), _run(TIER_A)]
    assert _check_state(runs, PLUGIN_LOAD) == "na"
    assert ci_green_self_hosted(runs) is True


def test_all_triggered_green_still_passes():
    runs = [_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD)]
    assert ci_green_self_hosted(runs) is True


def test_plugin_file_pr_still_enforces_plugin_load():
    # A PR touching plugin files: the check ran. Success passes…
    assert ci_green_self_hosted([_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD)]) is True
    # …and failure fails.
    assert (
        ci_green_self_hosted(
            [_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD, conclusion="failure")]
        )
        is False
    )


# ── fail-closed regressions ─────────────────────────────────────────


def test_no_payload_fails_closed():
    assert ci_green_self_hosted([]) is False
    assert ci_green_self_hosted(None) is False


def test_all_never_triggered_fails_closed():
    # CI ran (payload present) but none of the known checks did: nothing
    # green to stand on.
    assert ci_green_self_hosted([_run("some other check")]) is False


def test_failed_run_fails():
    for conclusion in ("failure", "cancelled", "timed_out", "action_required"):
        runs = [_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD, conclusion=conclusion)]
        assert ci_green_self_hosted(runs) is False, conclusion


def test_unknown_conclusion_fails():
    for conclusion in (None, "neutral", "stale", "bogus"):
        runs = [_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD, conclusion=conclusion)]
        assert _check_state(runs, PLUGIN_LOAD) == "fail", conclusion
        assert ci_green_self_hosted(runs) is False, conclusion


def test_unfinished_run_fails():
    # Scheduled but never completed: fail closed, not N/A.
    runs = [_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD, status="in_progress")]
    assert _check_state(runs, PLUGIN_LOAD) == "fail"
    assert ci_green_self_hosted(runs) is False


def test_always_triggered_check_absent_still_na_not_invented_green():
    # ruff is an always-triggered job in test.yml, so its absence in a
    # real payload would mean an API gap — but the N/A rule is uniform:
    # absence = never triggered = excluded. The evaluation then stands on
    # the remaining checks only.
    runs = [_run(TIER_A), _run(PLUGIN_LOAD)]
    assert _check_state(runs, RUFF) == "na"
    assert ci_green_self_hosted(runs) is True


# ── skip-conclusion rule ─────────────────────────────────────────────


def test_skipped_check_is_na_when_siblings_green():
    runs = [_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD, conclusion="skipped")]
    assert _check_state(runs, PLUGIN_LOAD) == "na"
    assert ci_green_self_hosted(runs) is True


def test_skipped_check_cannot_mask_failed_sibling():
    # The failed sibling fails the evaluation on its own; the skip is
    # irrelevant to the outcome.
    runs = [
        _run(RUFF, conclusion="failure"),
        _run(TIER_A),
        _run(PLUGIN_LOAD, conclusion="skipped"),
    ]
    assert ci_green_self_hosted(runs) is False


# ── live-path flow: build_input_dict -> observer ─────────────────────


def test_build_input_dict_flows_na_through():
    d = build_input_dict(_pr(), [_run(RUFF), _run(TIER_A)], ["docs/a.md"])
    inp = load_input_from_dict(d)
    assert inp.ci_green_self_hosted is True

    d = build_input_dict(
        _pr(),
        [_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD, conclusion="failure")],
        ["plugins/x.py"],
    )
    inp = load_input_from_dict(d)
    assert inp.ci_green_self_hosted is False


def test_observer_can_now_call_merge_on_ordinary_pr():
    # Full live path: ordinary PR (no plugin files), green ruff + tier A,
    # plugin-load never triggered. Under the old semantics this failed
    # closed to skip; now the gate passes.
    d = build_input_dict(_pr(), [_run(RUFF), _run(TIER_A)], ["docs/a.md"])
    d["ruff_clean"] = True
    d["review_verdict"] = "CLEAN"
    d["branch_protection_satisfied"] = True
    inp = load_input_from_dict(d)
    decision = evaluate(inp, _policy(), _bands(), _tier_engine())
    assert decision.call == CALL_MERGE


def test_observer_still_skips_when_plugin_load_failed():
    d = build_input_dict(
        _pr(),
        [_run(RUFF), _run(TIER_A), _run(PLUGIN_LOAD, conclusion="failure")],
        ["plugins/x.py"],
    )
    d["ruff_clean"] = True
    d["review_verdict"] = "CLEAN"
    d["branch_protection_satisfied"] = True
    inp = load_input_from_dict(d)
    decision = evaluate(inp, _policy(), _bands(), _tier_engine())
    assert decision.call == CALL_SKIP
    assert any(
        g.gate == "ci_green_self_hosted" and not g.passed for g in decision.gate_results
    )


def test_self_hosted_checks_tuple_unchanged():
    # The recalibration changes interpretation, not the check set.
    assert tuple(SELF_HOSTED_CHECKS) == (
        "smoke (ruff lint)",
        "review factory gate (tier A)",
        "Verify shipped plugins load",
    )
