"""Phase 0 shadow-mode poll adapter — the live-PR wiring (observe-only).

Polls open GitHub PRs on ``mbgulden/prismatic-engine``, builds a
``ShadowInput`` for each new ``(pr_number, head_sha)`` from the PR's CI
check-runs, and calls ``shadow_observer.observe()``. The observer decides;
this adapter never acts on the decision: no merge authority, no RF-4
merge-executor wiring, no state changes on the repo. The decision path
itself (``shadow_observer.evaluate``) stays a pure function — this module
is the network-facing adapter that feeds it, exactly the "poll adapter"
role ``load_input_from_dict`` was designed for.

GitHub access: the ``gh`` CLI, already authenticated on webtop-hermes as
the repo owner. No new credentials, no tokens on disk.

Mapping (fail-safe: anything unknown or unfinished defers or fails the
gate, never invents a green):

- ``ruff_clean``: the ``smoke (ruff lint)`` check-run concluded success.
- ``review_verdict``: the ``review factory gate (tier A)`` check-run
  concluded success → ``"CLEAN"``; a *completed* conclusion of
  ``failure`` → ``"ADVISORY"`` (2026-09-22 recalibration: the gate's
  circular-proof heuristic is a documented false-negative family and
  the demonstrated merge practice treats the gate as advisory — the
  failure is recorded in ``advisory_flags`` and stays visible, but it
  does not block); anything else (cancelled, timed out, skipped,
  never-triggered, missing/absent data) → ``"REJECT"`` (the observer's
  fail-safe default; a deterministic non-CLEAN verdict can never be
  cleared downstream). The advisory split is deliberately narrow: only
  an explicit completed *failure* is advisory. Concurrency
  cancellation, infra timeouts, and absent data carry no evidence
  about the gate's verdict and stay fail-closed.
- ``ci_green_self_hosted``: every self-hosted check that ran for the PR
  head completed with conclusion success; a check that never ran for the
  head (e.g. the path-conditional plugin-load workflow) is N/A and
  excluded. No check-runs payload at all still fails closed.
- ``branch_protection_satisfied``: branch protection is NOT enabled on
  the repo (GitHub refused it on the current plan, 2026-09-22), so this
  is defined mechanically as "every completed check-run on the head SHA
  succeeded and at least one ran" — the combined-status stand-in. When
  real branch protection lands, this should read the required-checks
  state instead.
- ``merge_conflicts``: ``gh`` reports ``mergeable == "CONFLICTING"``.

A PR is only evaluated once its check suite has settled (every check-run
``completed``) and GitHub has computed mergeability — mid-CI PRs are
deferred to the next poll so the agreement metric never scores a
half-finished run. One audit signal is emitted per ``(pr_number,
head_sha)``; new commits re-trigger evaluation.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Protocol

from prismatic.review_factory.shadow_observer import (
    SPEC_DIR,
    ShadowDecision,
    ShadowInput,
    ShadowPolicy,
    RiskBands,
    default_tier_engine,
    load_bands,
    load_input_from_dict,
    load_policy,
    observe,
)
from prismatic.review_factory.policy import PolicyEngine

logger = logging.getLogger(__name__)

REPO = "mbgulden/prismatic-engine"

# Self-hosted webtop-hermes checks that must all be green.
SELF_HOSTED_CHECKS = (
    "smoke (ruff lint)",
    "review factory gate (tier A)",
    "Verify shipped plugins load",
)

RUFF_CHECK_NAME = "smoke (ruff lint)"
RF_GATE_CHECK_NAME = "review factory gate (tier A)"

DEFAULT_STATE_PATH = Path("~/.prismatic/state/shadow-seen.json").expanduser()


# ─────────────────────────────────────────────────────────────────────
# PR source (injectable for tests; gh CLI in production)
# ─────────────────────────────────────────────────────────────────────


class PRSource(Protocol):
    """Something that can list open PRs and their CI state."""

    def list_open_prs(self) -> list[dict[str, Any]]: ...
    def get_pr_files(self, pr_number: int) -> list[str]: ...
    def get_check_runs(self, head_sha: str) -> list[dict[str, Any]]: ...


def _gh(*args: str) -> Any:
    """Run `gh` and parse its JSON output. Raises on failure."""
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()[:200]}"
        )
    return json.loads(proc.stdout or "null")


class GhCliPRSource:
    """Production PR source: the `gh` CLI (authenticated on webtop-hermes)."""

    def __init__(self, repo: str = REPO):
        self.repo = repo

    def list_open_prs(self) -> list[dict[str, Any]]:
        return (
            _gh(
                "pr",
                "list",
                "--repo",
                self.repo,
                "--state",
                "open",
                "--json",
                "number,title,headRefOid,mergeable",
                "--limit",
                "50",
            )
            or []
        )

    def get_pr_files(self, pr_number: int) -> list[str]:
        files = _gh(
            "pr",
            "view",
            str(pr_number),
            "--repo",
            self.repo,
            "--json",
            "files",
            "--jq",
            "[.files[].path]",
        )
        return [str(f) for f in (files or [])]

    def get_check_runs(self, head_sha: str) -> list[dict[str, Any]]:
        runs = _gh(
            "api",
            f"repos/{self.repo}/commits/{head_sha}/check-runs",
            "--paginate",
            "--jq",
            "[.check_runs[] | {name, status, conclusion}]",
        )
        return list(runs or [])


# ─────────────────────────────────────────────────────────────────────
# Mapping: GitHub state -> ShadowInput fields (fail-safe)
# ─────────────────────────────────────────────────────────────────────


def _conclusion(check_runs: list[dict[str, Any]], name: str) -> str | None:
    for run in check_runs:
        if run.get("name") == name and run.get("status") == "completed":
            return run.get("conclusion")
    return None


def checks_settled(check_runs: list[dict[str, Any]]) -> bool:
    """True when every check-run has finished (none queued/in_progress)."""
    return all(run.get("status") == "completed" for run in check_runs)


def ruff_clean(check_runs: list[dict[str, Any]]) -> bool:
    """The ruff smoke check concluded success. Missing/unfinished -> False."""
    return _conclusion(check_runs, RUFF_CHECK_NAME) == "success"


def _tier_a_advisory(check_runs: list[dict[str, Any]] | None) -> bool:
    """True when the tier-A check completed with conclusion failure.

    This is the narrow advisory split from the 2026-09-22 recalibration:
    only an *explicit completed failure* is advisory. Cancelled, timed
    out, skipped, never-triggered, or missing tier-A data stays
    fail-closed because it carries no evidence about the gate's verdict.
    """
    return bool(check_runs) and _conclusion(check_runs, RF_GATE_CHECK_NAME) == "failure"


def review_verdict(check_runs: list[dict[str, Any]]) -> str:
    """RF gate check conclusion -> CLEAN / ADVISORY / REJECT.

    - success -> CLEAN
    - completed failure -> ADVISORY (recorded in advisory_flags, not
      blocking; the failing check stays visible in the decision record)
    - anything else (cancelled, timed out, skipped, never-triggered,
      missing/absent data) -> REJECT, the fail-safe default. A missing
      or non-advisory deterministic verdict can never clear the
      observer's verdict_not_reject gate.
    """
    conclusion = _conclusion(check_runs, RF_GATE_CHECK_NAME)
    if conclusion == "success":
        return "CLEAN"
    if conclusion == "failure":
        return "ADVISORY"
    return "REJECT"


# ─────────────────────────────────────────────────────────────────────
# Never-triggered checks are N/A (recalibration 2026-09-22)
#
# A check is "never triggered" only when it is absent from the check-runs
# for the PR head — the workflow did not run for this PR at all (e.g. the
# `paths:` filter in .github/workflows/plugin-load.yml excluded it). A
# successful check-runs fetch is authoritative for the head SHA (a failed
# fetch raises in the PR source before we ever get here), so absence means
# "never scheduled", not "unknown".
#
# Everything else stays fail-closed: no check-runs payload at all, a check
# that was scheduled but never completed, a failed/cancelled/timed-out
# run, or an unrecognized conclusion all fail the evaluation.
# ─────────────────────────────────────────────────────────────────────

_CHECK_PASS = "pass"
_CHECK_FAIL = "fail"
_CHECK_NA = "na"  # never triggered: excluded from the green requirement


def _check_state(check_runs: list[dict[str, Any]] | None, name: str) -> str:
    """Classify one named check as pass / fail / na (never triggered)."""
    runs = [r for r in (check_runs or []) if r.get("name") == name]
    if not runs:
        # The workflow never ran for this PR head (e.g. a paths: filter
        # excluded it). N/A: excluded from the green requirement.
        return _CHECK_NA
    run = next((r for r in runs if r.get("status") == "completed"), None)
    if run is None:
        return _CHECK_FAIL  # scheduled but unfinished: fail closed
    conclusion = run.get("conclusion")
    if conclusion == "success":
        return _CHECK_PASS
    if conclusion == "skipped":
        # Skip-conclusion rule: a "skipped" check is N/A (excluded). This is
        # safe because a skip that follows a failure is already fail-closed:
        # the failed sibling's own state fails the evaluation, so a cascade
        # skip can never turn a red suite green. The only outcome-relevant
        # case is skip-with-all-siblings-green, which is necessarily the
        # workflow's own conditional logic (e.g. a job-level `if:`). From a
        # check-runs payload we cannot read the job's `if:` expression, so
        # this is the tightest mechanically-enforceable statement of the
        # rule.
        return _CHECK_NA
    # failure, cancelled, timed_out, action_required, stale, None, or any
    # unrecognized conclusion: fail.
    return _CHECK_FAIL


def ci_green_self_hosted(check_runs: list[dict[str, Any]] | None) -> bool:
    """True when every triggered self-hosted check concluded success.

    Checks that never ran for the PR head (absent from the check-runs,
    e.g. the path-conditional plugin-load workflow) are N/A and excluded
    from the requirement. A completed tier-A *failure* is advisory
    (2026-09-22 recalibration): it is excluded here and recorded in
    advisory_flags instead — the failing check stays visible but does
    not block. At least one check must be required (non-N/A) and green;
    no payload at all fails closed.
    """
    if not check_runs:
        return False  # no CI data at all: fail closed, never N/A
    advisory = _tier_a_advisory(check_runs)
    states = [_check_state(check_runs, name) for name in SELF_HOSTED_CHECKS]
    for name, state in zip(SELF_HOSTED_CHECKS, states):
        if state == _CHECK_FAIL and not (advisory and name == RF_GATE_CHECK_NAME):
            return False
    required = [
        state
        for name, state in zip(SELF_HOSTED_CHECKS, states)
        if state != _CHECK_NA
        and not (advisory and name == RF_GATE_CHECK_NAME and state == _CHECK_FAIL)
    ]
    return bool(required) and all(state == _CHECK_PASS for state in required)


def branch_protection_satisfied(check_runs: list[dict[str, Any]]) -> bool:
    """Mechanical stand-in for branch protection (not enabled on the repo).

    True when at least one check ran and every completed check-run on the
    head SHA succeeded — except a completed tier-A *failure*, which is
    advisory (2026-09-22 recalibration) and excluded from the success
    requirement while staying visible in advisory_flags. Revisit when
    real branch protection lands.
    """
    if not check_runs:
        return False
    advisory = _tier_a_advisory(check_runs)
    completed = [
        run
        for run in check_runs
        if run.get("status") == "completed"
        and not (
            advisory
            and run.get("name") == RF_GATE_CHECK_NAME
            and run.get("conclusion") == "failure"
        )
    ]
    return bool(completed) and all(
        run.get("conclusion") == "success" for run in completed
    )


def merge_conflicts(pr: dict[str, Any]) -> bool:
    """True only when GitHub positively reports CONFLICTING."""
    return pr.get("mergeable") == "CONFLICTING"


def mergeability_known(pr: dict[str, Any]) -> bool:
    """False while GitHub is still computing mergeability."""
    return pr.get("mergeable") in ("MERGEABLE", "CONFLICTING")


def build_input_dict(
    pr: dict[str, Any],
    check_runs: list[dict[str, Any]],
    files: list[str],
) -> dict[str, Any]:
    """Assemble the ShadowInput dict for one PR at one head SHA."""
    return {
        "pr_number": int(pr["number"]),
        "pr_title": str(pr.get("title", "")),
        "head_sha": str(pr.get("headRefOid", "")),
        "base_sha": str(pr.get("baseRefOid", "")),
        "changed_files": files,
        "ci_green_self_hosted": ci_green_self_hosted(check_runs),
        "ruff_clean": ruff_clean(check_runs),
        "review_verdict": review_verdict(check_runs),
        "advisory_flags": (
            [RF_GATE_CHECK_NAME] if _tier_a_advisory(check_runs) else []
        ),
        "merge_conflicts": merge_conflicts(pr),
        "branch_protection_satisfied": branch_protection_satisfied(check_runs),
    }


# ─────────────────────────────────────────────────────────────────────
def _record_ci_results(check_runs):
    """Append one ci_result event per settled check run (Phase 0 observe-only).

    Called exactly once per (pr_number, head_sha) -- tied to the poll's
    seen-set, so a re-poll never double-records. Conclusion "success" maps
    to "pass"; any other completed conclusion (failure, cancelled,
    timed out, ...) maps to "fail", matching this module's fail-safe
    mapping. A feed failure is logged and never breaks the poll.
    """
    from prismatic.review_factory.metrics_feed import record_ci_result

    for run in check_runs or []:
        if run.get("status") != "completed":
            continue
        name = run.get("name")
        if not name:
            continue
        try:
            record_ci_result(
                result="pass" if run.get("conclusion") == "success" else "fail",
                runner=str(name),
            )
        except Exception:
            logger.warning(
                "watchdog feed record_ci_result failed for check %r",
                name,
                exc_info=True,
            )


# Dedup state + the poll
# ─────────────────────────────────────────────────────────────────────


def load_seen(state_path: Path = DEFAULT_STATE_PATH) -> set[str]:
    """Keys are 'pr_number:head_sha' already observed."""
    try:
        return set(json.loads(state_path.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen: set[str], state_path: Path = DEFAULT_STATE_PATH) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(sorted(seen)), encoding="utf-8")


def poll_once(
    source: PRSource,
    policy: ShadowPolicy,
    bands: RiskBands,
    tier_engine: PolicyEngine,
    state_path: Path = DEFAULT_STATE_PATH,
    sink: str | Path | None = None,
) -> list[ShadowDecision]:
    """Evaluate every newly-settled open PR. Returns emitted decisions.

    Observe-only: decisions are logged via ``observe()``; nothing here
    acts on them. A decision is recorded in the seen-state only when
    ``observe()`` actually emitted it (returns non-None) — with the
    policy default-off, polls are harmless no-ops.

    Performance: when the policy is disabled the poll short-circuits
    immediately — ``observe()`` would return None for every PR, so no
    per-PR work (check-run/file fetches, mapping) is performed. The
    seen-state file is only rewritten when a decision was actually
    emitted (the only case the seen set can change).
    """
    if not policy.enabled:
        # Default-off: observe() returns None for every PR, so there is
        # nothing to fetch, map, or record. Skip the per-PR source calls
        # (gh API in production) and the seen-file rewrite entirely.
        return []
    seen = load_seen(state_path)
    emitted: list[ShadowDecision] = []

    for pr in source.list_open_prs():
        pr_number = int(pr["number"])
        head_sha = str(pr.get("headRefOid", ""))
        key = f"{pr_number}:{head_sha}"
        if key in seen:
            continue
        if not head_sha:
            logger.warning("PR #%d has no head SHA; skipping", pr_number)
            continue
        if not mergeability_known(pr):
            logger.info(
                "PR #%d mergeability %r not computed yet; deferring",
                pr_number,
                pr.get("mergeable"),
            )
            continue
        try:
            check_runs = source.get_check_runs(head_sha)
        except Exception as exc:  # network/gh failure: defer, never guess
            logger.warning(
                "PR #%d check-runs unreadable (%s); deferring", pr_number, exc
            )
            continue
        if not checks_settled(check_runs):
            logger.info("PR #%d CI still running; deferring to next poll", pr_number)
            continue
        try:
            files = source.get_pr_files(pr_number)
        except Exception as exc:
            logger.warning(
                "PR #%d file list unreadable (%s); deferring", pr_number, exc
            )
            continue

        try:
            inp: ShadowInput = load_input_from_dict(
                build_input_dict(pr, check_runs, files)
            )
        except Exception as exc:  # malformed gh payload: quarantine this PR
            logger.warning(
                "PR #%d payload unmappable (%s: %s); deferring to next poll",
                pr_number,
                type(exc).__name__,
                exc,
            )
            continue
        try:
            if sink is None:
                decision = observe(inp, policy, bands, tier_engine)
            else:
                decision = observe(inp, policy, bands, tier_engine, sink)
        except Exception:  # never let one PR's evaluation abort the poll
            logger.exception(
                "PR #%d evaluation failed; continuing with next PR", pr_number
            )
            continue
        if decision is not None:
            seen.add(key)
            # Watchdog metrics feed (Phase 0 observe-only): this PR head's
            # CI suite has settled -- record each check run's outcome.
            # Append-only; a feed failure must never break the poll.
            _record_ci_results(check_runs)
            emitted.append(decision)
            logger.info(
                "shadow call for PR #%d (%s): %s",
                pr_number,
                head_sha[:8],
                decision.call.upper(),
            )

    # The seen set only changes when a decision is emitted (keys are
    # added only for emitted decisions), so skip the rewrite when there
    # is nothing new to record.
    if emitted:
        save_seen(seen, state_path)
    return emitted


def default_components() -> tuple[ShadowPolicy, RiskBands, PolicyEngine]:
    """Load policy, bands, and tier engine from the deployed spec files."""
    policy = load_policy()
    bands = load_bands(SPEC_DIR / Path(policy.bands_file).name)
    return policy, bands, default_tier_engine(policy)
