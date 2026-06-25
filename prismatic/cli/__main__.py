"""Allow `python -m prismatic.cli` to invoke the unified CLI."""

from __future__ import annotations

from prismatic.cli import run

if __name__ == "__main__":
    raise SystemExit(run())
