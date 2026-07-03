#!/usr/bin/env bash
set -euo pipefail

# Weekly Prismatic Engine SQLite state maintenance.
# Override roots with a comma-separated PRISMATIC_VACUUM_ROOTS list, or pass
# arguments through to the Python runner (for example: --dry-run).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/vacuum_state_dbs.py" "$@"
