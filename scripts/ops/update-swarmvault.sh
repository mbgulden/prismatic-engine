#!/usr/bin/env bash
set -e
echo "======================================================================"
echo "🔐 Updating SwarmVault from github.com/mbgulden/swarmvault@main..."
echo "======================================================================"
VENVS=("/home/ubuntu/.venv" "/home/ubuntu/prismatic_venv" "/home/ubuntu/prismatic-gateway-venv")
for VENV in "${VENVS[@]}"; do
  if [ -d "$VENV" ]; then
    "$VENV/bin/pip" install --upgrade --no-cache-dir "git+https://github.com/mbgulden/swarmvault.git@main"
  fi
done
if [ -x "$(command -v pip)" ]; then
  pip install --upgrade --no-cache-dir --break-system-packages "git+https://github.com/mbgulden/swarmvault.git@main" 2>/dev/null || true
fi
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl restart prismatic-gateway.service 2>/dev/null || systemctl --user restart prismatic-gateway.service 2>/dev/null || true
fi
echo "✅ SwarmVault update complete!"
