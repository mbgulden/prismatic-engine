#!/usr/bin/env python3
"""Discover and maintain Prismatic Engine SQLite state databases.

The runner is intentionally filesystem-driven: operators configure one or more
state roots, and the script finds every ``*.db`` file below those roots.  Each
SQLite database is checked, committed out of any transaction, then maintained
with ``VACUUM`` and ``ANALYZE``.  Failures are per-database so one corrupt or
locked DB does not mask the rest of the fleet.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOTS = (
    Path.home() / ".prismatic" / "db",
    Path(
        os.environ.get("PRISMATIC_STATE_DIR", str(REPO_ROOT / "prismatic_state"))
    ).expanduser(),
)


@dataclass(frozen=True)
class VacuumResult:
    path: Path
    ok: bool
    before_bytes: int | None
    after_bytes: int | None
    message: str

    @property
    def delta_bytes(self) -> int | None:
        if self.before_bytes is None or self.after_bytes is None:
            return None
        return self.after_bytes - self.before_bytes


def parse_roots(raw: str | None = None) -> list[Path]:
    """Parse comma-separated roots from CLI/env, or return both canonical roots."""

    raw = raw if raw is not None else os.environ.get("PRISMATIC_VACUUM_ROOTS")
    if not raw:
        return list(DEFAULT_ROOTS)
    return [Path(part).expanduser() for part in raw.split(",") if part.strip()]


def discover_databases(roots: Sequence[Path]) -> list[Path]:
    """Return sorted unique SQLite database paths under existing roots."""

    found: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.suffix == ".db":
            found.add(root.resolve())
            continue
        for path in root.rglob("*.db"):
            if path.is_file():
                found.add(path.resolve())
    return sorted(found)


def maintain_database(
    path: Path, *, dry_run: bool = False, timeout: float = 30.0
) -> VacuumResult:
    """Run integrity check, VACUUM, and ANALYZE against one SQLite database."""

    try:
        before = path.stat().st_size
    except OSError as exc:
        return VacuumResult(path, False, None, None, f"stat failed: {exc}")

    if dry_run:
        return VacuumResult(
            path,
            True,
            before,
            before,
            "dry-run: would run integrity_check, VACUUM, ANALYZE",
        )

    try:
        conn = sqlite3.connect(str(path), timeout=timeout)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity = row[0] if row else "missing integrity_check result"
            if integrity != "ok":
                return VacuumResult(
                    path,
                    False,
                    before,
                    path.stat().st_size,
                    f"integrity_check failed: {integrity}",
                )

            # VACUUM cannot run inside an open transaction.  Commit any implicit
            # transaction before the rebuild, then commit ANALYZE statistics.
            conn.commit()
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
            conn.commit()
        finally:
            conn.close()
        after = path.stat().st_size
        return VacuumResult(path, True, before, after, "vacuum/analyze ok")
    except sqlite3.Error as exc:
        try:
            after = path.stat().st_size
        except OSError:
            after = None
        return VacuumResult(path, False, before, after, f"sqlite error: {exc}")
    except OSError as exc:
        return VacuumResult(path, False, before, None, f"filesystem error: {exc}")


def format_result(result: VacuumResult) -> str:
    delta = "n/a" if result.delta_bytes is None else str(result.delta_bytes)
    status = "OK" if result.ok else "FAIL"
    before = "n/a" if result.before_bytes is None else str(result.before_bytes)
    after = "n/a" if result.after_bytes is None else str(result.after_bytes)
    return f"{status} {result.path} before={before} after={after} delta={delta} {result.message}"


def run(
    roots: Sequence[Path], *, dry_run: bool = False, timeout: float = 30.0
) -> list[VacuumResult]:
    databases = discover_databases(roots)
    return [
        maintain_database(path, dry_run=dry_run, timeout=timeout) for path in databases
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="VACUUM and ANALYZE all Prismatic Engine SQLite state DBs"
    )
    parser.add_argument(
        "--roots",
        default=None,
        help="Comma-separated roots or .db files. Defaults to PRISMATIC_VACUUM_ROOTS or both canonical state roots.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List databases and planned maintenance without mutating them",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="SQLite busy timeout per DB in seconds",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = parse_roots(args.roots)
    print("Prismatic SQLite vacuum runner")
    print("roots=" + ",".join(str(root) for root in roots))
    results = run(roots, dry_run=args.dry_run, timeout=args.timeout)
    if not results:
        print("WARN no .db files discovered")
        return 0
    for result in results:
        print(format_result(result))
    failed = [result for result in results if not result.ok]
    print(
        f"summary total={len(results)} ok={len(results) - len(failed)} failed={len(failed)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
