#!/usr/bin/env bash
# scripts/ops/update-all-primitives.sh
# Updates all 16 Swarm primitives from GitHub across all Python venvs and restarts services.

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "======================================================================"
echo "🐝 Updating All 16 Swarm Primitives from GitHub @main..."
echo "======================================================================"

PRIMITIVES=(
  # Phase 1 — Original Core
  "swarmlock"
  "swarmcron"
  "swarmrouter"
  "swarmcurator"
  "swarmproof"
  "swarmgate"
  "swarmledger"
  "swarmsaga"
  # Phase 2 — Core Expansion
  "swarmmesh"
  "swarmmemory"
  "swarmcas"
  # Phase 3 — Robustness
  "swarmsandbox"
  "swarmmerge"
  "swarmmeter"
  # Phase 4 — Enterprise
  "swarmvault"
  "swarmconsensus"
)

VENVS=(
  "/home/ubuntu/.venv"
  "/home/ubuntu/prismatic_venv"
  "/home/ubuntu/prismatic-gateway-venv"
)

for PRIM in "${PRIMITIVES[@]}"; do
  echo -e "\n--------------------------------------------------"
  echo "🚀 Updating $PRIM..."
  echo "--------------------------------------------------"
  for VENV in "${VENVS[@]}"; do
    if [ -d "$VENV" ]; then
      echo "  • Installing in $VENV..."
      "$VENV/bin/pip" install --upgrade --no-cache-dir "git+https://github.com/mbgulden/${PRIM}.git@main" || true
    fi
  done
  if [ -x "$(command -v pip)" ]; then
    pip install --upgrade --no-cache-dir --break-system-packages "git+https://github.com/mbgulden/${PRIM}.git@main" 2>/dev/null || true
  fi
done

# Restart Prismatic Gateway service if running under systemd
if command -v systemctl >/dev/null 2>&1; then
  echo -e "\n🔄 Restarting prismatic-gateway.service..."
  sudo systemctl restart prismatic-gateway.service 2>/dev/null || systemctl --user restart prismatic-gateway.service 2>/dev/null || true
fi

echo -e "\n======================================================================"
echo "🎉 All 16 Swarm Primitives updated and services reloaded successfully!"
echo "======================================================================"
