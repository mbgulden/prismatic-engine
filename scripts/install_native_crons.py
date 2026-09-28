#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))
_loaded_prismatic = sys.modules.get("prismatic")
if _loaded_prismatic is not None:
    module_file = getattr(_loaded_prismatic, "__file__", "") or ""
    if not module_file.startswith(str(REPO_ROOT)):
        for name in list(sys.modules):
            if name == "prismatic" or name.startswith("prismatic."):
                sys.modules.pop(name, None)

from prismatic.native_crons import (  # noqa: E402
    CRONTAB_BLOCK_BEGIN as BEGIN,
    CRONTAB_BLOCK_END as END,
    read_user_crontab,
    render_crontab_block,
    replace_crontab_managed_block,
    write_user_crontab,
)


def render_block() -> str:
    return render_crontab_block()


def replace_managed_block(existing: str, block: str) -> str:
    return replace_crontab_managed_block(existing, block)


def read_crontab() -> str:
    # The manual installer keeps the historical contract: a missing or
    # unreadable crontab reads as empty so a fresh install still writes the block.
    return read_user_crontab() or ""


def write_crontab(content: str) -> None:
    write_user_crontab(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install PE-native cron definitions into user crontab")
    parser.add_argument("--dry-run", action="store_true", help="Print the resulting crontab without installing")
    args = parser.parse_args(argv)

    new_content = replace_managed_block(read_crontab(), render_block())
    if args.dry_run:
        print(new_content, end="")
        return 0
    write_crontab(new_content)
    print("Installed PE-native cron managed block")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
