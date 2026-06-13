#!/usr/bin/env bash
# ============================================================
# Prismatic Engine — One-Command Setup
# ============================================================
# Installs the package, creates default config directory,
# and prints next steps.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/mbgulden/prismatic-engine/main/install.sh | bash
#   # or
#   chmod +x install.sh && ./install.sh
#
# Works on Linux and macOS.  Requires Python 3.10+ and pip.
# ============================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { printf "${CYAN}%s${NC}\n" "$*"; }
ok()    { printf "${GREEN}✓ %s${NC}\n" "$*"; }
warn()  { printf "${YELLOW}⚠ %s${NC}\n" "$*"; }
err()   { printf "${RED}✗ %s${NC}\n" "$*"; }

# ── Detect OS ───────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Linux*)   PLATFORM="linux" ;;
    Darwin*)  PLATFORM="macos" ;;
    *)
        err "Unsupported OS: $OS"
        err "Prismatic Engine requires Linux or macOS."
        exit 1
        ;;
esac
info "Detected platform: $PLATFORM"

# ── Check prerequisites ─────────────────────────────────────
info "Checking prerequisites..."

if ! command -v python3 &> /dev/null; then
    err "Python 3 not found.  Please install Python 3.10+ first."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
REQUIRED_VERSION="3.10"
if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -1)" != "$REQUIRED_VERSION" ]; then
    err "Python $REQUIRED_VERSION+ required.  Found: $PYTHON_VERSION"
    exit 1
fi
ok "Python $PYTHON_VERSION"

if ! command -v pip3 &> /dev/null && ! python3 -m pip --version &> /dev/null; then
    err "pip not found.  Please install pip for Python 3."
    exit 1
fi
ok "pip found"

# ── Determine install method ────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/setup.py" ] || [ -f "$SCRIPT_DIR/pyproject.toml" ] || [ -f "$SCRIPT_DIR/setup.cfg" ]; then
    # Local install (from cloned repo or tarball)
    info "Found package in $SCRIPT_DIR — installing from source..."
    cd "$SCRIPT_DIR"
    python3 -m pip install -e .
    INSTALL_METHOD="source"
else
    # PyPI install
    info "Installing from PyPI..."
    python3 -m pip install prismatic-engine
    INSTALL_METHOD="pypi"
fi
ok "Prismatic Engine installed ($INSTALL_METHOD)"

# ── Create default config directory ─────────────────────────
CONFIG_DIR="${PRISMATIC_HOME:-$HOME}/.prismatic"
if [ ! -d "$CONFIG_DIR" ]; then
    mkdir -p "$CONFIG_DIR"
    info "Created config directory: $CONFIG_DIR"
fi

# ── Initialize default config ───────────────────────────────
info "Initializing default configuration..."
if command -v prismatic-engine &> /dev/null; then
    prismatic-engine init
elif [ -f "$HOME/.local/bin/prismatic-engine" ]; then
    "$HOME/.local/bin/prismatic-engine" init
else
    warn "prismatic-engine command not found in PATH — skipping 'init'"
fi

# ── Systemd Service Generation ──────────────────────────────
if [ "$PLATFORM" = "linux" ] && [ -d "/etc/systemd/system" ]; then
    echo ""
    info "Systemd detected. You can run Prismatic Engine as a service."
    
    ENGINE_BIN=$(command -v prismatic-engine || echo "$HOME/.local/bin/prismatic-engine")
    
    echo ""
    echo "Run the following to set up the service:"
    echo "-----------------------------------------------------------"
    echo "cat <<EOF | sudo tee /etc/systemd/system/prismatic.service"
    echo "[Unit]"
    echo "Description=Prismatic Engine Coordinator"
    echo "After=network.target"
    echo ""
    echo "[Service]"
    echo "Type=simple"
    echo "User=$USER"
    echo "ExecStart=$ENGINE_BIN serve"
    echo "Restart=always"
    echo "Environment=LINEAR_API_KEY=your_key_here"
    echo "Environment=PRISMATIC_TEAM_ID=your_team_id"
    echo ""
    echo "[Install]"
    echo "WantedBy=multi-user.target"
    echo "EOF"
    echo ""
    echo "sudo systemctl daemon-reload"
    echo "sudo systemctl enable prismatic"
    echo "sudo systemctl start prismatic"
    echo "-----------------------------------------------------------"
fi

# ── Print next steps ────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  🎉  Prismatic Engine installed successfully!"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Set your Linear API key:"
echo "     export LINEAR_API_KEY=\"lin_api_xxxxxxxxxxxx\""
echo ""
echo "  2. (Optional) Set your team ID:"
echo "     export LINEAR_TEAM_ID=\"GRO\""
echo ""
echo "  3. Initialize default config:"
echo "     prismatic-engine init"
echo ""
echo "  4. Start the coordinator:"
echo "     prismatic-engine serve"
echo ""
echo "  Config directory: $CONFIG_DIR"
echo "  Documentation:   https://github.com/mbgulden/prismatic-engine"
echo ""
echo "═══════════════════════════════════════════════════════════"
echo ""
