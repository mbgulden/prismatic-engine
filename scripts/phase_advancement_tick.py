#!/usr/bin/env python3
"""Daily heartbeat wrapper for the phase-advancement machinery.

Repo convention: scripts/ holds the runnable wrappers; the logic lives in
the module. The post-merge step installs the systemd timer
``prismatic-phase-advancement.timer`` that runs this script daily on the VM.

The heartbeat evaluates the current phase's mechanical exit criteria and
files an AdvancementRequest when they pass. Requesting is NOT executing:
the phase still moves only through PhaseAdvancement.execute(), which needs
the master switch (advancements_enabled: true) + Michael's approval record
+ live evidence. Until the ladder advances, this tick files nothing and
changes nothing.

    python scripts/phase_advancement_tick.py [--evidence-pointers FILE] \
        [--requester NAME]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prismatic.review_factory.phase_advancement import main

if __name__ == "__main__":
    raise SystemExit(main())
