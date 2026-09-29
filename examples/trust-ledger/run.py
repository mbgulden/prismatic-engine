"""Trust ledger in ~30 seconds.

The append-only audit trail behind earned autonomy: every merge, pause, and
tier change is recorded as a signed event. Reads use a disposable database in
a temp dir — nothing touches production state.

Run with: ``python3 examples/trust-ledger/run.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prismatic.review_factory.trust import TrustLedger


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="prismatic-trust-example-"))
    ledger = TrustLedger(db_path=tmp / "trust.db", audit_dir=tmp / "audit")

    ledger.record_event(
        "merge_completed",
        artifact_id="PR-123",
        change_class="docs",
        tier_at_event=0,
        merged_by="michael",
        notes="docs-only change, CI green",
    )

    events = ledger.events(limit=5)
    assert events, "expected at least one event"
    latest = events[-1]
    print(
        f"events in ledger: {len(events)}; latest: {latest['event_type']} {latest['artifact_id']}"
    )
    status = ledger.tier_status()
    print("current_tier:", status["current_tier"])
    assert status["current_tier"] == 0
    print("TRUST_LEDGER_EXAMPLE_OK")


if __name__ == "__main__":
    main()
