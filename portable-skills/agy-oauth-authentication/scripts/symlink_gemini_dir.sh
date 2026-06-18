#!/usr/bin/env bash
# Symlink the AGY .gemini config directory for a Hermes profile.
# Usage: scripts/symlink_gemini_dir.sh <profile_name> [real_home]
#
# Idempotent. Safe to run multiple times.
# If the target already exists as a real directory, moves it aside first
# (the previous AGY binary's state stays in the original location; symlink
# continues to point at it, so no data is lost).

set -euo pipefail

PROFILE="${1:-}"
REAL_HOME="${2:-$HOME}"
PROFILE_HOME="$REAL_HOME/.hermes/profiles/$PROFILE/home"
LINK_PATH="$PROFILE_HOME/.gemini"
TARGET="$REAL_HOME/.gemini"

if [ -z "$PROFILE" ]; then
  echo "Usage: $0 <profile_name> [real_home]" >&2
  exit 1
fi

if [ ! -d "$PROFILE_HOME" ]; then
  echo "❌ Profile home does not exist: $PROFILE_HOME" >&2
  exit 1
fi

if [ ! -d "$TARGET" ]; then
  echo "❌ Target AGY config does not exist: $TARGET" >&2
  echo "   Run 'agy' interactively first to initialize the native config." >&2
  exit 1
fi

# Ensure parent dir exists
mkdir -p "$PROFILE_HOME"

# Idempotent: if link already correct, exit
if [ -L "$LINK_PATH" ] && [ "$(readlink -f "$LINK_PATH")" = "$(readlink -f "$TARGET")" ]; then
  echo "✅ Symlink already in place: $LINK_PATH → $TARGET"
  exit 0
fi

# If a real directory exists at the link path, move it aside
if [ -d "$LINK_PATH" ] && [ ! -L "$LINK_PATH" ]; then
  BACKUP="$PROFILE_HOME/.gemini.bak.$(date +%s)"
  echo "⚠️  Existing real dir at $LINK_PATH — moving to $BACKUP"
  mv "$LINK_PATH" "$BACKUP"
fi

ln -sfn "$TARGET" "$LINK_PATH"
echo "✅ Symlink created: $LINK_PATH → $TARGET"
echo "   Verify: readlink -f $LINK_PATH"
echo "   Test:   python3 -c \"import json; print(json.load(open('$LINK_PATH/antigravity-cli/antigravity-oauth-token'))['token']['expiry'])\""
