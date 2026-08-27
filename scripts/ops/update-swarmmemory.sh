#!/usr/bin/env bash
# scripts/ops/update-swarmmemory.sh
# Updates SwarmMemory from github.com/mbgulden/swarmmemory@main across all Python venvs and restarts services.

set -e

echo "======================================================================"
echo "🧠 Updating SwarmMemory from github.com/mbgulden/swarmmemory@main..."
echo "======================================================================"

VENVS=(
  "/home/ubuntu/.venv"
  "/home/ubuntu/prismatic_venv"
  "/home/ubuntu/prismatic-gateway-venv"
)

for VENV in "${VENVS[@]}"; do
  if [ -d "$VENV" ]; then
    echo "📦 Installing latest swarmmemory in $VENV..."
    "$VENV/bin/pip" install --upgrade --no-cache-dir "git+https://github.com/mbgulden/swarmmemory.git@main"
  fi
done

if [ -x "$(command -v pip)" ]; then
  pip install --upgrade --no-cache-dir --break-system-packages "git+https://github.com/mbgulden/swarmmemory.git@main" 2>/dev/null || true
fi

if command -v systemctl >/dev/null 2>&1; then
  echo "🔄 Restarting prismatic-gateway.service..."
  sudo systemctl restart prismatic-gateway.service 2>/dev/null || systemctl --user restart prismatic-gateway.service 2>/dev/null || true
fi

echo "======================================================================"
echo "✅ SwarmMemory update complete!"
echo "======================================================================"
