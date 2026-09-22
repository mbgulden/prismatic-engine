"""Tests for the shadow-mode poll adapter (shadow_poller).

All GitHub access goes through the injectable PRSource, so no test
touches the network. The mapping tests pin the fail-safe defaults:
unknown or unfinished state defers or fails the gate, never invents
a green.
"""

from pathlib import Path


from prismatic.review_factory.policy import PolicyEngine
from prismatic.review_factory.shadow_observer import (
    SPEC_DIR,
    RiskBands,
    ShadowPolicy,
    load_policy,
)
from prismatic.review_factory.shadow_poller import (
    GhCliPRSource,  # noqa: F401  (import surface check)
    branch_protection_satisfied,
    build_input_dict,
    checks_settled,
    ci_green_self_hosted,
    load_input_from_dict,
    merge_conflicts,
    mergeability_known,
    poll_once,
    review_verdict,
    ruff_clean,
)


def _run(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


ALL_GREEN = [
    _run("smoke (ruff lint)"),
    _run("review factory gate (tier A)"),
    _run("Verify shipped plugins load"),
]


def _policy(tmp_path: Path) -> ShadowPolicy:
    # Real policy file keeps the gate list honest; enabled flag is
    # overridden per-test via object replacement.
    return load_policy()


def _bands() -> RiskBands:
    return RiskBands(version="test", auto_below=0.2, human_above=0.6)


def _tier_engine() -> PolicyEngine:
    return PolicyEngine.from_yaml(
        Path(__file__).resolve().parent.parent / "spec" / "policy_file_v1.yaml"
    )


class FakeSource:
    """In-memory PRSource."""

    def __init__(self, prs, files=None, checks=None):
        self.prs = prs
        self.files = files or {}
        self.checks = checks or {}

    def list_open_prs(self):
        return self.prs

    def get_pr_files(self, pr_number):
        return self.files.get(pr_number, ["docs/note.md"])

    def get_check_runs(self, head_sha):
        return self.checks.get(head_sha, [])


def _pr(number=101, sha="a" * 40, mergeable="MERGEABLE"):
    return {
        "number": number,
        "title": "Test PR",
        "headRefOid": sha,
        "baseRefOid": "b" * 40,
        "mergeable": mergeable,
    }


# ── mapping ────────────────────────────────────────────────────────────


def test_ruff_clean_requires_smoke_success():
    assert ruff_clean(ALL_GREEN) is True
    assert ruff_clean([_run("smoke (ruff lint)", conclusion="failure")]) is False
    assert ruff_clean([]) is False  # missing -> fail-safe False
    assert ruff_clean([_run("smoke (ruff lint)", status="in_progress")]) is False


def test_review_verdict_fail_safe():
    assert review_verdict(ALL_GREEN) == "CLEAN"
    # Recalibration 2026-09-22: a *completed* tier-A failure is ADVISORY
    # (recorded in advisory_flags, not blocking) — the full split lives in
    # test_tier_a_advisory.py. Everything else stays fail-closed.
    assert (
        review_verdict([_run("review factory gate (tier A)", conclusion="failure")])
        == "ADVISORY"
    )
    assert review_verdict([]) == "REJECT"  # missing gate -> REJECT, never cleared
    assert (
        review_verdict(
            [_run("review factory gate (tier A)", conclusion="cancelled")]
        )
        == "REJECT"
    )


def test_ci_green_requires_all_self_hosted():
    assert ci_green_self_hosted(ALL_GREEN) is True
    runs = [_run("smoke (ruff lint)"), _run("review factory gate (tier A)")]
    # Recalibration 2026-09-22: a never-triggered check (here the
    # path-conditional plugin-load workflow, absent from the check-runs)
    # is N/A and excluded from the green requirement — it no longer
    # forces a skip on ordinary PRs.
    assert ci_green_self_hosted(runs) is True
    runs = ALL_GREEN + [_run("extra check", conclusion="failure")]
    assert ci_green_self_hosted(runs) is True  # extra checks don't matter here
    # But a triggered check that failed still fails the evaluation.
    runs = [
        _run("smoke (ruff lint)"),
        _run("review factory gate (tier A)"),
        _run("Verify shipped plugins load", conclusion="failure"),
    ]
    assert ci_green_self_hosted(runs) is False


def test_branch_protection_stand_in():
    assert branch_protection_satisfied(ALL_GREEN) is True
    assert branch_protection_satisfied([]) is False
    assert (
        branch_protection_satisfied(ALL_GREEN + [_run("other", conclusion="failure")])
        is False
    )


def test_merge_conflicts_only_on_positive():
    assert merge_conflicts({"mergeable": "CONFLICTING"}) is True
    assert merge_conflicts({"mergeable": "MERGEABLE"}) is False
    assert merge_conflicts({"mergeable": "UNKNOWN"}) is False
    assert mergeability_known({"mergeable": "UNKNOWN"}) is False
    assert mergeability_known({"mergeable": "MERGEABLE"}) is True


def test_checks_settled():
    assert checks_settled(ALL_GREEN) is True
    assert checks_settled(ALL_GREEN + [_run("x", status="in_progress")]) is False
    assert checks_settled([]) is True  # vacuous: no CI at all -> gates fail


def test_build_input_dict_shapes_shadow_input():
    d = build_input_dict(_pr(), ALL_GREEN, ["docs/a.md"])
    inp = load_input_from_dict(d)  # must satisfy the observer's schema
    assert inp.pr_number == 101
    assert inp.ci_green_self_hosted is True
    assert inp.review_verdict == "CLEAN"
    assert inp.merge_conflicts is False


# ── poll_once behavior ─────────────────────────────────────────────────


def _enabled(policy: ShadowPolicy) -> ShadowPolicy:
    return ShadowPolicy(
        version=policy.version,
        enabled=True,
        gates=policy.gates,
        verdicts_mergeable=policy.verdicts_mergeable,
        tier_policy_file=policy.tier_policy_file,
        shadow_merge_max_tier=policy.shadow_merge_max_tier,
        jev_enabled=policy.jev_enabled,
        bands_file=policy.bands_file,
    )


def test_poll_emits_one_decision_per_pr_sha(tmp_path):
    pr = _pr(sha="a" * 40)
    src = FakeSource([pr], checks={"a" * 40: ALL_GREEN})
    state = tmp_path / "seen.json"
    sink = tmp_path / "signals.jsonl"
    policy = _enabled(_policy(tmp_path))

    first = poll_once(src, policy, _bands(), _tier_engine(), state, sink)
    assert len(first) == 1
    assert first[0].call == "merge"  # docs-only change, all gates green

    second = poll_once(src, policy, _bands(), _tier_engine(), state, sink)
    assert second == []  # dedup: same (pr, sha) not re-emitted
    assert sink.read_text().strip().count("\n") == 0  # exactly one line


def test_poll_re_evaluates_new_head_sha(tmp_path):
    state = tmp_path / "seen.json"
    sink = tmp_path / "signals.jsonl"
    policy = _enabled(_policy(tmp_path))
    src2 = FakeSource([_pr(sha="c" * 40)], checks={"c" * 40: ALL_GREEN})
    # seed the seen-state with the old sha as if a previous poll ran
    import json

    state.write_text(json.dumps(["101:" + "b" * 40]))
    out = poll_once(src2, policy, _bands(), _tier_engine(), state, sink)
    assert len(out) == 1
    assert out[0].head_sha == "c" * 40


def test_poll_defers_unsettled_and_unknown(tmp_path):
    prs = [
        _pr(number=1, sha="a" * 40),  # CI still running
        _pr(number=2, sha="b" * 40, mergeable="UNKNOWN"),  # mergeability pending
    ]
    src = FakeSource(
        prs,
        checks={
            "a" * 40: ALL_GREEN + [_run("late", status="in_progress")],
            "b" * 40: ALL_GREEN,
        },
    )
    out = poll_once(
        src,
        _enabled(_policy(tmp_path)),
        _bands(),
        _tier_engine(),
        tmp_path / "seen.json",
        tmp_path / "s.jsonl",
    )
    assert out == []


def test_poll_default_off_emits_nothing(tmp_path):
    pr = _pr(sha="a" * 40)
    src = FakeSource([pr], checks={"a" * 40: ALL_GREEN})
    # The shipped default policy is now enabled (v2); the default-off path
    # is pinned to the preserved v1 file.
    policy = load_policy(SPEC_DIR / "shadow_merge_policy_v1.yaml")
    assert policy.enabled is False
    out = poll_once(
        src,
        policy,
        _bands(),
        _tier_engine(),
        tmp_path / "seen.json",
        tmp_path / "s.jsonl",
    )
    assert out == []
    assert not (tmp_path / "s.jsonl").exists()


def test_poll_disabled_short_circuits_source(tmp_path):
    """Default-off: zero per-PR source calls (gh API calls in production).

    With the policy disabled, observe() would return None for every PR, so
    the poll must not fetch check-runs or file lists at all — and must not
    even create the seen-state file.
    """
    pr = _pr(sha="a" * 40)
    src = FakeSource([pr], checks={"a" * 40: ALL_GREEN})
    calls = {"checks": 0, "files": 0}
    orig_checks, orig_files = src.get_check_runs, src.get_pr_files

    def counting_checks(sha):
        calls["checks"] += 1
        return orig_checks(sha)

    def counting_files(n):
        calls["files"] += 1
        return orig_files(n)

    src.get_check_runs = counting_checks
    src.get_pr_files = counting_files
    policy = load_policy(SPEC_DIR / "shadow_merge_policy_v1.yaml")
    assert policy.enabled is False
    out = poll_once(src, policy, _bands(), _tier_engine(), tmp_path / "seen.json")
    assert out == []
    assert calls == {"checks": 0, "files": 0}
    assert not (tmp_path / "seen.json").exists()


def test_poll_skips_seen_rewrite_when_nothing_emitted(tmp_path, monkeypatch):
    """Steady-state polls must not rewrite the seen-state file.

    The seen set only changes when a decision is emitted, so a poll that
    emits nothing must leave the file untouched (no mtime churn).
    """
    pr = _pr(sha="a" * 40)
    src = FakeSource([pr], checks={"a" * 40: ALL_GREEN})
    state = tmp_path / "seen.json"
    policy = _enabled(_policy(tmp_path))
    first = poll_once(
        src, policy, _bands(), _tier_engine(), state, tmp_path / "s.jsonl"
    )
    assert len(first) == 1
    before = state.read_text(encoding="utf-8")

    saves: list = []
    monkeypatch.setattr(
        "prismatic.review_factory.shadow_poller.save_seen",
        lambda seen, path=state: saves.append((set(seen), path)),
    )
    second = poll_once(
        src, policy, _bands(), _tier_engine(), state, tmp_path / "s.jsonl"
    )
    assert second == []
    assert saves == []
    assert state.read_text(encoding="utf-8") == before


def test_poll_failing_gate_produces_skip_call(tmp_path):
    bad = [_run("smoke (ruff lint)", conclusion="failure")] + ALL_GREEN[1:]
    pr = _pr(sha="a" * 40)
    src = FakeSource([pr], checks={"a" * 40: bad})
    out = poll_once(
        src,
        _enabled(_policy(tmp_path)),
        _bands(),
        _tier_engine(),
        tmp_path / "seen.json",
        tmp_path / "s.jsonl",
    )
    assert len(out) == 1
    assert out[0].call == "skip"
    assert any("ruff_clean" in r for r in out[0].reasons)


# ── GhCliPRSource parsing (gh --jq streaming regression) ──────────────
#
# gh --jq streams one JSON value per line, not a JSON array. The filters
# must therefore be wrapped in brackets so _gh always parses a list.
# These tests feed the real parsing path (mocked subprocess only) with
# the exact bytes gh would stream for the wrapped filters.

from unittest import mock  # noqa: E402

from prismatic.review_factory import shadow_poller  # noqa: E402


def _fake_proc(stdout: str):
    proc = mock.Mock()
    proc.returncode = 0
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def _jq_arg(captured: dict) -> str:
    args = captured["args"]
    return args[args.index("--jq") + 1]


def test_get_check_runs_single_run_parses_to_one_dict():
    # gh streams one JSON array per page for the wrapped filter.
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _fake_proc(
            '[{"name": "smoke (ruff lint)", "status": "completed",'
            ' "conclusion": "success"}]\n'
        )

    src = shadow_poller.GhCliPRSource(repo="owner/repo")
    with mock.patch.object(shadow_poller.subprocess, "run", fake_run):
        runs = src.get_check_runs("a" * 40)
    assert runs == [
        {
            "name": "smoke (ruff lint)",
            "status": "completed",
            "conclusion": "success",
        }
    ]
    # The old unwrapped filter streamed a bare object for one run, so
    # list(dict) yielded keys and this crashed with a str .get error.
    assert checks_settled(runs) is True
    jq = _jq_arg(captured)
    assert jq.startswith("[") and jq.endswith("]"), jq


def test_get_check_runs_multiple_runs_parses_to_list():
    def fake_run(args, **kwargs):
        return _fake_proc(
            '[{"name": "a", "status": "completed", "conclusion": "success"},'
            ' {"name": "b", "status": "in_progress", "conclusion": null}]\n'
        )

    src = shadow_poller.GhCliPRSource(repo="owner/repo")
    with mock.patch.object(shadow_poller.subprocess, "run", fake_run):
        runs = src.get_check_runs("b" * 40)
    assert len(runs) == 2
    assert checks_settled(runs) is False


def test_get_check_runs_empty_stays_empty():
    def fake_run(args, **kwargs):
        return _fake_proc("[]\n")

    src = shadow_poller.GhCliPRSource(repo="owner/repo")
    with mock.patch.object(shadow_poller.subprocess, "run", fake_run):
        assert src.get_check_runs("c" * 40) == []


def test_get_pr_files_single_file_returns_one_path():
    # gh streams one JSON array per page for the wrapped filter.
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _fake_proc('["prismatic/review_factory/shadow_poller.py"]\n')

    src = shadow_poller.GhCliPRSource(repo="owner/repo")
    with mock.patch.object(shadow_poller.subprocess, "run", fake_run):
        files = src.get_pr_files(101)
    # The old unwrapped filter streamed a bare string for one file, so
    # the old code returned a list of individual characters.
    assert files == ["prismatic/review_factory/shadow_poller.py"]
    jq = _jq_arg(captured)
    assert jq.startswith("[") and jq.endswith("]"), jq


def test_get_pr_files_multiple_files():
    def fake_run(args, **kwargs):
        return _fake_proc('["a.md", "b.md"]\n')

    src = shadow_poller.GhCliPRSource(repo="owner/repo")
    with mock.patch.object(shadow_poller.subprocess, "run", fake_run):
        assert src.get_pr_files(102) == ["a.md", "b.md"]
