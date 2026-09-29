"""Resolve the SHA of the currently-running Prismatic release.

The gateway needs to report *which release is actually running*, not which
checkout the code came from. Resolution order:

1. ``PRISMATIC_RELEASE_SHA`` env var — set by the deploy receiver or tests.
2. The release directory name parsed from this file's own path:
   ``.../versions/prismatic-engine-<sha>/prismatic/gateway/release_info.py``.
   Symlinks (e.g. the ``releases/prismatic-engine`` symlink) are resolved first.
3. ``None`` — dev checkout or packaged install with no release dir. Callers
   surface this as JSON null rather than guessing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

RELEASE_DIR_PATTERN = re.compile(r"^prismatic-engine-([0-9a-f]{7,40})$")

ENV_VAR = "PRISMATIC_RELEASE_SHA"

# How far up from this file we look for the release directory. The module
# lives at <release>/prismatic/gateway/release_info.py, so 3 levels up is
# the release dir; a few extra levels tolerate repackaging.
_MAX_WALK_LEVELS = 6


def get_running_sha(anchor: Path | None = None) -> str | None:
    """Return the running release SHA, or None if it cannot be determined."""
    env_sha = os.environ.get(ENV_VAR)
    if env_sha:
        return env_sha.strip() or None

    start = Path(anchor) if anchor is not None else Path(__file__)
    try:
        current = start.resolve()
    except OSError:
        return None
    if current.is_file():
        current = current.parent
    for _ in range(_MAX_WALK_LEVELS):
        match = RELEASE_DIR_PATTERN.match(current.name)
        if match:
            return match.group(1)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None
