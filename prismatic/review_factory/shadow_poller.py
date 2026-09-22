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
  concluded success → ``"CLEAN"``, anything else → ``"REJECT"`` (the
  observer's fail-safe default; a deterministic non-CLEAN verdict can
  never be cleared downstream).
- ``ci_green_self_hosted``: every known self-hosted check completed with
  conclusion success.
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
            ".files[].path",
        )
        return [str(f) for f in (files or [])]

    def get_check_runs(self, head_sha: str) -> list[dict[str, Any]]:
        runs = _gh(
            "api",
            f"repos/{self.repo}/commits/{head_sha}/check-runs",
            "--paginate",
            "--jq",
            ".check_runs[] | {name, status, conclusion}",
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


def review_verdict(check_runs: list[dict[str, Any]]) -> str:
    """RF gate check conclusion -> CLEAN, anything else -> REJECT.

    REJECT is the fail-safe default: a missing or failed deterministic
    verdict can never clear the observer's verdict_not_reject gate.
    """
    return (
        "CLEAN"
        if _conclusion(check_runs, RF_GATE_CHECK_NAME) == "success"
        else "REJECT"
    )


def ci_green_self_hosted(check_runs: list[dict[str, Any]]) -> bool:
    """All known self-hosted checks completed with conclusion success."""
    return all(
        _conclusion(check_runs, name) == "success" for name in SELF_HOSTED_CHECKS
    )


def branch_protection_satisfied(check_runs: list[dict[str, Any]]) -> bool:
    """Mechanical stand-in for branch protection (not enabled on the repo).

    True when at least one check ran and every completed check-run on the
    head SHA succeeded. Revisit when real branch protection lands.
    """
    if not check_runs:
        return False
    return all(
        run.get("conclusion") == "success"
        for run in check_runs
        if run.get("status") == "completed"
    ) and any(run.get("status") == "completed" for run in check_runs)


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
        "merge_conflicts": merge_conflicts(pr),
        "branch_protection_satisfied": branch_protection_satisfied(check_runs),
    }


# ─────────────────────────────────────────────────────────────────────
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
    """
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

        inp: ShadowInput = load_input_from_dict(build_input_dict(pr, check_runs, files))
        if sink is None:
            decision = observe(inp, policy, bands, tier_engine)
        else:
            decision = observe(inp, policy, bands, tier_engine, sink)
        if decision is not None:
            seen.add(key)
            emitted.append(decision)
            logger.info(
                "shadow call for PR #%d (%s): %s",
                pr_number,
                head_sha[:8],
                decision.call.upper(),
            )

    save_seen(seen, state_path)
    return emitted


def default_components() -> tuple[ShadowPolicy, RiskBands, PolicyEngine]:
    """Load policy, bands, and tier engine from the deployed spec files."""
    policy = load_policy()
    bands = load_bands(SPEC_DIR / Path(policy.bands_file).name)
    return policy, bands, default_tier_engine(policy)
