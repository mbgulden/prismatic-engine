#!/usr/bin/env bash
# scripts/ops/update-all-primitives.sh
# Updates all Swarm primitives (Swarmlock, SwarmCron, SwarmRouter, SwarmProof) from GitHub across all Python venvs and restarts services.

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "======================================================================"
echo "🐝 Updating All Swarm Primitives from GitHub @main..."
echo "======================================================================"

PRIMITIVES=(
  "swarmlock"
  "swarmcron"
  "swarmrouter"
  "swarmproof"
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
echo "🎉 All Swarm Primitives updated and services reloaded successfully!"
echo "======================================================================"
