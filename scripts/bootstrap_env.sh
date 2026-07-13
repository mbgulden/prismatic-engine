#!/usr/bin/env bash
# Bootstrap a local Prismatic Engine development/release environment.
# Safe to run from a clean checkout; does not require credentials.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
export PRISMATIC_STATE_DIR="${PRISMATIC_STATE_DIR:-$ROOT/prismatic_state}"

echo "[bootstrap] repo: $ROOT"
echo "[bootstrap] python: $($PYTHON_BIN --version)"
echo "[bootstrap] venv: $VENV_DIR"
echo "[bootstrap] state: $PRISMATIC_STATE_DIR"

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[release]"

mkdir -p "$PRISMATIC_STATE_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[bootstrap] wrote .env from .env.example"
else
  echo "[bootstrap] .env already exists; leaving it untouched"
fi

if [ ! -f config.local.yaml ]; then
  cp config/prismatic.sample.yaml config.local.yaml
  echo "[bootstrap] wrote config.local.yaml from config/prismatic.sample.yaml"
else
  echo "[bootstrap] config.local.yaml already exists; leaving it untouched"
fi

python scripts/public_launch_smoke.py
python scripts/public_security_readiness_audit.py
python scripts/release_smoke.py

echo "[bootstrap] ready"
