#!/usr/bin/env python3
"""Phase 0 shadow-mode poll entry point (observe-only).

Invoked by the `prismatic-shadow-poll` systemd timer on webtop-hermes
(every 15 minutes). Polls open GitHub PRs, evaluates each newly-settled
one against the shadow merge policy, and appends one audit signal per
decision to ~/.prismatic/audit/shadow-decisions.jsonl.

Observe-only: the engine ignores every decision. Exit 0 on a clean poll;
non-zero on exception so timer failures are visible in the journal.

The repo root (parent of this script's directory) is prepended to
sys.path so `prismatic` resolves to the deployed source tree — whose
`review_factory/spec/` holds the versioned policy — rather than the
site-packages install, which does not ship the spec files.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.review_factory.shadow_poller import (  # noqa: E402
    GhCliPRSource,
    default_components,
    poll_once,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s shadow-poll %(levelname)s %(message)s",
)


def main() -> int:
    policy, bands, tier_engine = default_components()
    decisions = poll_once(GhCliPRSource(), policy, bands, tier_engine)
    print(
        f"shadow-poll done: {len(decisions)} decision(s) emitted "
        f"(policy {policy.version}, enabled={policy.enabled})"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # visible in the timer journal, never silent
        print(f"shadow-poll FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
