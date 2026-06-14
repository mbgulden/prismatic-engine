#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — Pre-Push Hook Installer (GRO-1561)
# ==============================================================================
# Installs the pre-push-hook.py into any git repo that has a PRISMATIC_ENGINE.yaml.
# Usage:
#   ./install-pre-push-hook.sh </path/to/repo>
#
# Or install across all known prismatic-governed repos:
#   ./install-pre-push-hook.sh --all
#
# Or install a single repo:
#   ./install-pre-push-hook.sh /home/ubuntu/work/some-repo
# ==============================================================================
set -euo pipefail

HOOK_SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pre-push-hook.py"
PRISMATIC_REPOS=(
  "/home/ubuntu/work/prismatic-engine"
  "/home/ubuntu/work/active-oahu-tours-mirror"
  "/home/ubuntu/work/agentic-swarm-ops"
  "/home/ubuntu/work/OpenHumanDesignMCP"
  "/home/ubuntu/work/darius-star"
  "/home/ubuntu/work/prismatic-engine-site"
)

install_hook() {
  local repo="$1"
  local hook_dest="$repo/.git/hooks/pre-push"
  local script_dir="$repo/scripts"
  local script_dest="$script_dir/pre-push-hook.py"

  if [ ! -d "$repo/.git" ]; then
    echo "❌ $repo: not a git repository (no .git directory)"
    return 1
  fi

  if [ ! -f "$repo/PRISMATIC_ENGINE.yaml" ]; then
    echo "⚠️  $repo: no PRISMATIC_ENGINE.yaml — hook won't activate fully"
  fi

  # Create scripts/ dir if it doesn't exist
  mkdir -p "$script_dir"

  # Copy the hook script
  cp "$HOOK_SOURCE" "$script_dest"
  chmod +x "$script_dest"

  # Remove old hook file or symlink
  if [ -L "$hook_dest" ] || [ -f "$hook_dest" ]; then
    rm -f "$hook_dest"
  fi

  # Create relative symlink
  ln -s "../../scripts/pre-push-hook.py" "$hook_dest"

  # Verify
  local size
  size=$(stat -c%s "$script_dest" 2>/dev/null || stat -f%z "$script_dest" 2>/dev/null)
  echo "✅ $repo: hook installed ($size bytes) → $(readlink "$hook_dest")"
}

# --- Main ---
if [ ! -f "$HOOK_SOURCE" ]; then
  echo "❌ Hook source not found at: $HOOK_SOURCE"
  echo "   Run this script from the scripts/ directory of the prismatic-engine repo."
  exit 1
fi

if [ "${1:-}" = "--all" ]; then
  echo "Installing pre-push hook across all prismatic-governed repos..."
  for repo in "${PRISMATIC_REPOS[@]}"; do
    install_hook "$repo"
  done
  echo ""
  echo "Done. Verify with: ls -la */scripts/pre-push-hook.py */git/hooks/pre-push"
elif [ -n "${1:-}" ]; then
  install_hook "$1"
else
  echo "Usage: $0 [--all | /path/to/repo]"
  exit 1
fi
