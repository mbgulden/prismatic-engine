#!/usr/bin/env bash
# prismatic-dispatcher-wrapper.sh
#
# Thin shell wrapper around the Prismatic Engine dispatcher (prismatic/dispatcher.py).
# Sets PRISMATIC_TEAM_ID and sources the orchestrator profile's .env so the engine
# dispatcher has all the tokens it needs.
#
# This is the Tier 2 GRO-2030 deliverable: the engine dispatcher lives in the engine
# repo at prismatic/dispatcher.py. Profile scripts call into it via this wrapper.
#
# Usage:
#   prismatic-dispatcher-wrapper.sh [--once] [--interval N] [--setup-pipelines]
#
# The wrapper does NOT replace agent_dispatcher.py — both coexist. The profile
# dispatcher keeps the GRO-2024 bypass-detection logic; the engine dispatcher
# provides pipeline templates, telemetry, and richer state management.
set -euo pipefail

# Find orchestrator .env (override with PRISMATIC_ENV if set)
PRISMATIC_ENV="${PRISMATIC_ENV:-${HOME}/.hermes/profiles/orchestrator/.env}"
if [ ! -f "$PRISMATIC_ENV" ]; then
    echo "❌ $PRISMATIC_ENV not found; cannot source orchestrator credentials" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$PRISMATIC_ENV"

# Find engine repo (override with PRISMATIC_HOME if set; default to repo-relative)
PRISMATIC_HOME="${PRISMATIC_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [ ! -d "$PRISMATIC_HOME" ]; then
    echo "❌ $PRISMATIC_HOME not found; engine dispatcher unavailable" >&2
    exit 1
fi

# Required env vars for engine dispatcher
export PRISMATIC_TEAM_ID="${PRISMATIC_TEAM_ID:-b6fb2651-5a1f-4714-9bcd-9eb6e759ffef}"
export LINEAR_API_KEY="${LINEAR_API_KEY:-}"
if [ -z "$LINEAR_API_KEY" ]; then
    echo "❌ LINEAR_API_KEY not set in $PRISMATIC_ENV" >&2
    exit 1
fi

# Pass through state DB if profile wants to share
# (engine defaults to ./prismatic_state/event_router.db; profile uses the orchestrator path)
if [ -z "${PRISMATIC_STATE_DIR:-}" ]; then
    PRISMATIC_STATE_DIR="${HOME}/.hermes/profiles/orchestrator/state/event-router"
fi
export PRISMATIC_STATE_DIR

# Re-source env from shell
set +u
# Add the engine's venv_stable to PATH only if it exists
if [ -d "${PRISMATIC_VENV:-${HOME}/.prismatic/venv_stable}/bin" ]; then
    export PATH="${PRISMATIC_VENV:-${HOME}/.prismatic/venv_stable}/bin:${PATH}"
fi

# exec into the engine dispatcher with all forwarded args
exec python3 -m prismatic.dispatcher serve "$@"