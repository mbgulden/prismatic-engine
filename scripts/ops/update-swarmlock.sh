#!/usr/bin/env bash
# scripts/ops/update-swarmlock.sh
# Updates SwarmLock from github.com/mbgulden/swarmlock@main across all Python venvs and restarts services.

set -e

echo "======================================================================"
echo "🔒 Updating SwarmLock from github.com/mbgulden/swarmlock@main..."
echo "======================================================================"

VENVS=(
  "/home/ubuntu/.venv"
  "/home/ubuntu/prismatic_venv"
  "/home/ubuntu/prismatic-gateway-venv"
)

# 1. Update in active virtual environments
for VENV in "${VENVS[@]}"; do
  if [ -d "$VENV" ]; then
    echo "📦 Installing latest swarmlock in $VENV..."
    "$VENV/bin/pip" install --upgrade --no-cache-dir "git+https://github.com/mbgulden/swarmlock.git@main"
  fi
done

# 2. Also install in user space if no venv is found
if [ -x "$(command -v pip)" ]; then
  pip install --upgrade --no-cache-dir --break-system-packages "git+https://github.com/mbgulden/swarmlock.git@main" 2>/dev/null || true
fi

# 3. Restart Prismatic Gateway service if running under systemd
if command -v systemctl >/dev/null 2>&1; then
  echo "🔄 Restarting prismatic-gateway.service..."
  sudo systemctl restart prismatic-gateway.service 2>/dev/null || systemctl --user restart prismatic-gateway.service 2>/dev/null || true
fi

echo "======================================================================"
echo "✅ SwarmLock update complete!"
echo "======================================================================"
