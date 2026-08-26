#!/usr/bin/env bash
set -euo pipefail

echo "================================================================="
echo "  PRISMATIC ENGINE: IMMUTABLE WHEEL PRODUCTION RELEASE PIPELINE"
echo "================================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"
python3 "$SCRIPT_DIR/release_production.py"
