"""
prismatic-admin — system administration CLI for the Prismatic Engine.

Handles config migration and database schema upgrades per the core
architecture spec sections 3.2-3.3.

Usage:
    prismatic-admin config migrate --current <path>
    prismatic-admin db upgrade [--db-path <path>]

Spec reference: specs/core-architecture-v1.md
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# ── Default paths (overridable via env) ──────────────────
PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME", os.path.expanduser("~")))
CONFIG_DIR = PRISMATIC_HOME / ".prismatic"
DB_DIR = CONFIG_DIR / "db"

DEFAULT_DB_PATH = DB_DIR / "event_router.db"
DEFAULT_USER_CONFIG = CONFIG_DIR / "config.yaml"
DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "config" / "default_config.yaml"


# ── Config Migration ────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    """Load a YAML file, returning empty dict if missing or corrupt."""
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _deep_merge(defaults: dict, user: dict) -> dict:
    """Merge user values into defaults, preserving user keys.

    Adds missing keys from defaults; never overwrites existing user keys.
    Recursively merges nested dicts.
    """
    result = dict(user)  # Start with user config — preserves user values
    for key, default_value in defaults.items():
        if key not in result:
            result[key] = default_value
        elif isinstance(default_value, dict) and isinstance(result[key], dict):
            result[key] = _deep_merge(default_value, result[key])
        # If user has a non-dict value where default has a dict (or vice versa),
        # keep user's value — user explicitly set it.
    return result


def _save_yaml(path: Path, data: dict) -> None:
    """Save dict as YAML, creating parent directories as needed."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def cmd_config_migrate(current_path: str | None = None) -> int:
    """Migrate user config: merge missing defaults, preserve user settings.

    Args:
        current_path: Path to the current user config file.
                      Defaults to $PRISMATIC_HOME/.prismatic/config.yaml

    Returns:
        0 on success, 1 on failure.
    """
    user_config_path = Path(current_path) if current_path else DEFAULT_USER_CONFIG
    template_path = DEFAULT_TEMPLATE

    if not template_path.exists():
        print(
            f"prismatic-admin: warning — default config template not found at "
            f"{template_path}. Migration skipped (no defaults to merge)."
        )
        return 1

    defaults = _load_yaml(template_path)
    user = _load_yaml(user_config_path)

    if not user:
        # No existing user config — copy template as-is
        print(
            f"prismatic-admin: no existing config at {user_config_path}. "
            f"Creating from template."
        )
        shutil.copy2(template_path, user_config_path)
        print(f"  Created: {user_config_path}")
        return 0

    # Backup the existing config
    backup_path = user_config_path.with_suffix(".yaml.bak")
    shutil.copy2(user_config_path, backup_path)
    print(f"prismatic-admin: backed up existing config to {backup_path}")

    # Merge defaults into user config (user values preserved)
    merged = _deep_merge(defaults, user)
    _save_yaml(user_config_path, merged)

    # Report what was added
    added = [k for k in defaults if k not in user]
    if added:
        print(f"  Added {len(added)} new default keys: {', '.join(sorted(added))}")
    else:
        print("  No new keys to add — config is up to date.")

    return 0


# ── Database Upgrade ────────────────────────────────────

# Track the current schema version. Increment when adding new tables/columns.
CURRENT_SCHEMA_VERSION = 1

SCHEMA_MIGRATIONS: dict[int, str] = {
    1: """
    -- Schema v1: Initial event router schema
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS processed_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedup_key TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        processed_at TEXT NOT NULL DEFAULT (datetime('now')),
        source_agent TEXT,
        target_agent TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_dedup_key ON processed_events(dedup_key);
    CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_events(processed_at);
    """,
}


def _get_db_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version from the DB, or 0 if unversioned."""
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def cmd_db_upgrade(db_path: str | None = None) -> int:
    """Run pending database schema migrations.

    Args:
        db_path: Path to the SQLite database file.
                 Defaults to $PRISMATIC_HOME/.prismatic/db/event_router.db

    Returns:
        0 on success, 1 on failure.
    """
    target_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    # Ensure directory exists
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_path))
    try:
        current_version = _get_db_version(conn)

        if current_version >= CURRENT_SCHEMA_VERSION:
            print(
                f"prismatic-admin: db schema is current "
                f"(v{current_version}, latest v{CURRENT_SCHEMA_VERSION})."
            )
            return 0

        print(
            f"prismatic-admin: upgrading db from v{current_version} "
            f"to v{CURRENT_SCHEMA_VERSION}..."
        )

        for version in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
            if version not in SCHEMA_MIGRATIONS:
                print(
                    f"prismatic-admin: error — no migration defined for v{version}"
                )
                return 1

            print(f"  Applying migration v{version}...")
            conn.executescript(SCHEMA_MIGRATIONS[version])

        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        conn.commit()

        print(
            f"prismatic-admin: db upgrade complete. "
            f"Now at v{CURRENT_SCHEMA_VERSION}."
        )
        return 0

    except Exception as e:
        print(f"prismatic-admin: db upgrade failed: {e}", file=sys.stderr)
        conn.rollback()
        return 1
    finally:
        conn.close()


# ── CLI Entry Point ─────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic-admin",
        description="System administration CLI for the Prismatic Engine.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # config migrate
    config_parser = sub.add_parser(
        "config", help="Manage configuration"
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)

    migrate_parser = config_sub.add_parser(
        "migrate", help="Migrate user config with new defaults"
    )
    migrate_parser.add_argument(
        "--current",
        dest="current_path",
        help="Path to current user config (default: $PRISMATIC_HOME/.prismatic/config.yaml)",
    )

    # db upgrade
    db_parser = sub.add_parser("db", help="Manage database")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)

    upgrade_parser = db_sub.add_parser(
        "upgrade", help="Run pending schema migrations"
    )
    upgrade_parser.add_argument(
        "--db-path",
        help="Path to database (default: $PRISMATIC_HOME/.prismatic/db/event_router.db)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "config" and args.config_command == "migrate":
        sys.exit(cmd_config_migrate(args.current_path))

    elif args.command == "db" and args.db_command == "upgrade":
        sys.exit(cmd_db_upgrade(getattr(args, "db_path", None)))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
