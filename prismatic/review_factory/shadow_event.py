#!/usr/bin/env python3
"""Phase 0 shadow-mode event adapter — per-PR entry point for the event-driven feed.

Invoked by the ``shadow-event-feed`` GitHub Actions workflow on webtop-hermes
for ``pull_request`` events (opened, synchronize, reopened, closed). Evaluates
ONE PR against the shadow merge policy and appends one audit signal per new
``(pr_number, head_sha)`` to ``~/.prismatic/audit/shadow-decisions.jsonl``.

Observe-only: the engine ignores every decision. This adapter reuses the exact
code paths of the 15-minute poller (``GhCliPRSource``, ``build_input_dict``,
``observe``) — the same evaluation, triggered by an event instead of a timer.
Fail-safe mirrors the poller: an unreadable PR, unknown mergeability (open
PRs only), or unsettled CI defers (logged, exit 0); only real config or
programming errors exit non-zero so workflow failures stay visible and
honest.

The repo root (three levels above this file) is prepended to ``sys.path`` so
``prismatic`` resolves to the checkout's source tree.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.review_factory.policy import PolicyEngine  # noqa: E402
from prismatic.review_factory.shadow_observer import (  # noqa: E402
    RiskBands,
    ShadowConfigError,
    ShadowPolicy,
    load_input_from_dict,
    observe,
)
from prismatic.review_factory.shadow_poller import (  # noqa: E402
    DEFAULT_STATE_PATH,
    GhCliPRSource,
    build_input_dict,
    checks_settled,
    default_components,
    load_seen,
    mergeability_known,
    save_seen,
)

logger = logging.getLogger(__name__)

REPO = "mbgulden/prismatic-engine"
# Field set proven against the VM's gh 2.45.0 (baseRefOid is NOT supported).
PR_FIELDS = "number,title,headRefOid,mergeable,state"

EVENT_ACTIONS = ("opened", "synchronize", "reopened", "closed")


def fetch_pr(pr_number: int, repo: str = REPO) -> dict[str, Any]:
    """Fetch one PR's metadata via the gh CLI. Raises RuntimeError when unreadable."""
    proc = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", PR_FIELDS],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh pr view {pr_number} failed: {proc.stderr.strip()[:200]}"
        )
    try:
        data = json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh pr view {pr_number} returned invalid JSON: {exc}")
    if not isinstance(data, dict) or "number" not in data:
        raise RuntimeError(f"gh pr view {pr_number} returned no PR object")
    return data


def evaluate_pr(
    pr_number: int,
    event: str,
    source: GhCliPRSource,
    policy: ShadowPolicy,
    bands: RiskBands,
    tier_engine: PolicyEngine,
    state_path: Path,
    sink: Path | None,
) -> int:
    """Evaluate one PR event. Returns the process exit code."""
    if not policy.enabled:
        logger.info("shadow policy disabled; no-op")
        return 0
    try:
        pr = fetch_pr(pr_number)
    except Exception as exc:
        logger.warning("PR #%d unreadable (%s); deferring", pr_number, exc)
        return 0
    head_sha = str(pr.get("headRefOid", ""))
    if not head_sha:
        logger.warning("PR #%d has no head SHA; deferring", pr_number)
        return 0
    key = f"{pr_number}:{head_sha}"
    seen = load_seen(state_path)
    if key in seen:
        logger.info("PR #%d (%s) already observed; skipping", pr_number, head_sha[:8])
        return 0
    # Closed PRs report mergeable=UNKNOWN (mergeability is moot once resolved);
    # the observation is historical, so the gate is skipped for them.
    if event != "closed" and not mergeability_known(pr):
        logger.info(
            "PR #%d mergeability %r unknown; deferring",
            pr_number,
            pr.get("mergeable"),
        )
        return 0
    try:
        check_runs = source.get_check_runs(head_sha)
    except Exception as exc:
        logger.warning("PR #%d check-runs unreadable (%s); deferring", pr_number, exc)
        return 0
    if not checks_settled(check_runs):
        logger.info("PR #%d CI unsettled; deferring", pr_number)
        return 0
    try:
        files = source.get_pr_files(pr_number)
    except Exception as exc:
        logger.warning("PR #%d file list unreadable (%s); deferring", pr_number, exc)
        return 0
    inp = load_input_from_dict(build_input_dict(pr, check_runs, files))
    if sink is None:
        decision = observe(inp, policy, bands, tier_engine)
    else:
        decision = observe(inp, policy, bands, tier_engine, sink)
    if decision is None:
        logger.info("observer emitted nothing for PR #%d", pr_number)
        return 0
    seen.add(key)
    save_seen(seen, state_path)
    logger.info(
        "shadow %s event for PR #%d (%s): %s",
        event,
        pr_number,
        head_sha[:8],
        decision.call.upper(),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 0 shadow event adapter (observe-only)"
    )
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--event", choices=EVENT_ACTIONS, default="synchronize")
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument("--sink", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s shadow-event %(levelname)s %(message)s",
    )
    try:
        policy, bands, tier_engine = default_components()
    except ShadowConfigError as exc:
        logger.error("shadow config invalid, refusing to evaluate: %s", exc)
        return 1
    try:
        return evaluate_pr(
            args.pr_number,
            args.event,
            GhCliPRSource(args.repo),
            policy,
            bands,
            tier_engine,
            args.state_path or DEFAULT_STATE_PATH,
            args.sink,
        )
    except Exception:  # fail-closed and visible, never silent
        logger.exception("shadow event adapter FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
