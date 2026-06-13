I will begin by viewing the architecture blueprint file `/home/ubuntu/work/prismatic-engine/specs/core-architecture-v1.md` to ensure our package design aligns precisely with the specifications.
I have designed the pip package structure and installation process for the Prismatic Engine Core according to your specifications. 

Below are the complete specification documents. As requested, no code has been executed and no files have been written to disk.

---

### 1. `pyproject.toml` Specification

This configuration file implements modern PEP 517/518 build standards, specifies the dependencies required for the engine, limits Python support to version `3.10` and higher, and sets up script entry points.

```toml
[build-system]
requires = ["setuptools>=61.0.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "prismatic-engine"
version = "0.1.0"
description = "A multi-agent orchestration runtime for the Prismatic Engine Core"
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [
    { name = "Fred", email = "fred@antigravity.ai" }
]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Operating System :: POSIX :: Linux",
    "Topic :: Software Development :: Libraries :: Python Modules",
]
dependencies = [
    "watchdog>=3.0.0",
    "pyyaml>=6.0.2",
    "requests>=2.31.0",
    "sqlite-utils>=3.36",
    "aiohttp>=3.9.0",
]

[project.optional-dependencies]
dev = [
    "black>=23.10.0",
    "mypy>=1.6.0",
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.1",
    "pytest-cov>=4.1.0",
]

[project.urls]
Homepage = "https://github.com/prismatic-engine/prismatic-engine"
Documentation = "https://github.com/prismatic-engine/prismatic-engine/docs"

[project.scripts]
prismatic-engine = "prismatic.cli.client:main"
prismatic-admin = "prismatic.cli.admin:main"

[tool.setuptools]
packages = [
    "prismatic",
    "prismatic.cli",
    "prismatic.core",
    "prismatic.interface",
    "prismatic.utils"
]

[tool.setuptools.package-data]
prismatic = ["config/*.yaml"]
```

---

### 2. Directory Tree Plan

This directory tree defines the repository layout, referencing the structure in Section 6 of the architecture blueprint. Clickable file links point to the active development repository paths:

```
/home/ubuntu/work/prismatic-engine/
├── .github/
│   └── workflows/
│       ├── test.yml
│       └── publish.yml
├── config/
│   └── [default_config.yaml](file:///home/ubuntu/work/prismatic-engine/config/default_config.yaml)          # Base config schema template
├── docs/
│   └── architecture/
├── prismatic/
│   ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/prismatic/__init__.py)
│   ├── config/
│   │   ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/prismatic/config/__init__.py)
│   │   └── [default_config.yaml](file:///home/ubuntu/work/prismatic-engine/prismatic/config/default_config.yaml)  # In-package config template
│   ├── cli/
│   │   ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/prismatic/cli/__init__.py)
│   │   ├── [admin.py](file:///home/ubuntu/work/prismatic-engine/prismatic/cli/admin.py)                 # CLI database upgrades & config migrations
│   │   └── [client.py](file:///home/ubuntu/work/prismatic-engine/prismatic/cli/client.py)                # CLI workspace interfaces
│   ├── core/
│   │   ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/__init__.py)
│   │   ├── [contracts.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/contracts.py)             # [ContractManager] safety constraints
│   │   ├── [dispatcher.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/dispatcher.py)            # Task loop and webhook events router
│   │   ├── [handoff.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/handoff.py)               # [HandoffProtocol] webhook server
│   │   ├── [locking.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/locking.py)               # [SwarmLockManager] concurrency locks
│   │   ├── [orchestration.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/orchestration.py)         # [SwarmOrchestrator] runner
│   │   ├── [planning.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/planning.py)              # [SwarmPlanner] task decomposition
│   │   └── [registry.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/registry.py)              # Plugin and Persona loader
│   ├── interface/
│   │   ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/__init__.py)
│   │   ├── [hooks.py](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/hooks.py)                 # Abstract hook declarations
│   │   └── [plugin.py](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/plugin.py)                # [PrismaticPlugin] base class
│   └── utils/
│       ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/prismatic/utils/__init__.py)
│       └── [telemetry.py](file:///home/ubuntu/work/prismatic-engine/prismatic/utils/telemetry.py)             # [BudgetManager] telemetry
├── tests/
│   ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/tests/__init__.py)
│   ├── mock_providers/
│   │   ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/tests/mock_providers/__init__.py)
│   │   └── [mock_linear.py](file:///home/ubuntu/work/prismatic-engine/tests/mock_providers/mock_linear.py)
│   ├── unit/
│   │   ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/tests/unit/__init__.py)
│   │   ├── [test_locking.py](file:///home/ubuntu/work/prismatic-engine/tests/unit/test_locking.py)
│   │   ├── [test_contracts.py](file:///home/ubuntu/work/prismatic-engine/tests/unit/test_contracts.py)
│   │   └── [test_registry.py](file:///home/ubuntu/work/prismatic-engine/tests/unit/test_registry.py)
│   └── integration/
│       ├── [__init__.py](file:///home/ubuntu/work/prismatic-engine/tests/integration/__init__.py)
│       └── [test_sandbox.py](file:///home/ubuntu/work/prismatic-engine/tests/integration/test_sandbox.py)
├── [LICENSE](file:///home/ubuntu/work/prismatic-engine/LICENSE)
├── [PRISMATIC_ENGINE.yaml](file:///home/ubuntu/work/prismatic-engine/PRISMATIC_ENGINE.yaml)            # Workspace rules
├── [pyproject.toml](file:///home/ubuntu/work/prismatic-engine/pyproject.toml)                   # Packaging specification
├── [README.md](file:///home/ubuntu/work/prismatic-engine/README.md)
├── [setup.cfg](file:///home/ubuntu/work/prismatic-engine/setup.cfg)                        # Legacy compatibility stub
└── [install.sh](file:///home/ubuntu/work/prismatic-engine/install.sh)                       # Runtime deployment script
```

---

### 3. `install.sh` Design

This deployment script sets up the **Executing Runtime** under a dedicated `$PRISMATIC_HOME` path. It is fully compatible with headless Linux systems and supports both **PVE3** (live runtime execution) and **PVE1** (sandboxed runtime execution). 

```bash
#!/usr/bin/env bash

# Exit immediately on failures or undefined variable accesses
set -euo pipefail

# 1. Input and Environment Validations
if [ -z "${PRISMATIC_HOME:-}" ]; then
    echo "Error: The PRISMATIC_HOME environment variable is not defined." >&2
    echo "Please set it before execution (e.g., export PRISMATIC_HOME=/home/ubuntu/.prismatic_home)" >&2
    exit 1
fi

echo "=================================================================="
echo "Prismatic Engine Core - Stable Runtime Installer"
echo "Target PRISMATIC_HOME: $PRISMATIC_HOME"
echo "System Time: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
echo "=================================================================="

# Check for essential system commands
for cmd in python3 pip ln mkdir cp rsync; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: Required command '$cmd' is not available." >&2
        exit 1
    fi
done

# Ensure Python version is >= 3.10
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "Error: Python >= 3.10 is required. Found Python $PYTHON_VERSION" >&2
    exit 1
fi

# Locate root of the workspace source directory
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$SRC_DIR/pyproject.toml" ]; then
    SRC_DIR="$(pwd)"
    if [ ! -f "$SRC_DIR/pyproject.toml" ]; then
        echo "Error: Could not locate pyproject.toml in $(pwd)." >&2
        exit 1
    fi
fi

# Path Layout Specifications
VERSION="0.1.0"
VERSION_DIR="$PRISMATIC_HOME/.prismatic/versions/v$VERSION"
ACTIVE_SYM="$PRISMATIC_HOME/.prismatic/active"
PREV_SYM="$PRISMATIC_HOME/.prismatic/previous"
VENV_DIR="$PRISMATIC_HOME/.prismatic/venv_stable"
DB_DIR="$PRISMATIC_HOME/.prismatic/db"
RUN_DIR="$PRISMATIC_HOME/.prismatic/run"
BIN_DIR="$PRISMATIC_HOME/bin"
PLUGINS_DIR="$PRISMATIC_HOME/plugins"
CONFIG_FILE="$PRISMATIC_HOME/.prismatic/config.yaml"

# 2. Directory Structure Creation
echo "Initializing directory hierarchy..."
mkdir -p "$VERSION_DIR"
mkdir -p "$DB_DIR"
mkdir -p "$RUN_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$PLUGINS_DIR"

# 3. Code Staging
echo "Staging source files to static path $VERSION_DIR..."
# Exclude developer environment cache folders and local venvs
rsync -a --exclude='.git' --exclude='.venv*' --exclude='__pycache__' \
      --exclude='*.egg-info' --exclude='build' --exclude='dist' \
      "$SRC_DIR/" "$VERSION_DIR/"

# 4. Atomic Symlink Updates (Active Version Pointer)
echo "Updating active core runtime symlink..."
if [ -L "$ACTIVE_SYM" ]; then
    PREV_TARGET=$(readlink -f "$ACTIVE_SYM")
    if [ "$PREV_TARGET" != "$VERSION_DIR" ]; then
        echo "Saving previous active target to $PREV_SYM"
        ln -sfn "$PREV_TARGET" "$PREV_SYM"
    fi
fi
ln -sfn "$VERSION_DIR" "$ACTIVE_SYM"

# 5. Virtual Environment Initialization
echo "Setting up stable virtualenv at $VENV_DIR..."
python3 -m venv "$VENV_DIR"

# Activate the venv
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

# Update package management utilities
echo "Upgrading base virtualenv tooling..."
pip install --quiet --upgrade pip setuptools wheel

# Install the staged package in non-editable mode inside the venv
echo "Installing package prismatic-engine from active path ($ACTIVE_SYM)..."
pip install "$ACTIVE_SYM"

# 6. Configuration Management
DEFAULT_CONFIG="$ACTIVE_SYM/prismatic/config/default_config.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Installing default configuration template..."
    if [ -f "$DEFAULT_CONFIG" ]; then
        cp "$DEFAULT_CONFIG" "$CONFIG_FILE"
    else
        # Fallback to root configuration template if nested packaging isn't populated yet
        cp "$ACTIVE_SYM/config/default_config.yaml" "$CONFIG_FILE"
    fi
else
    echo "Existing user configuration found at $CONFIG_FILE. Preserving."
    # Run automatic configurations migrations if the admin utility exists
    if [ -f "$VENV_DIR/bin/prismatic-admin" ]; then
        echo "Checking for necessary configuration schema updates..."
        "$VENV_DIR/bin/prismatic-admin" config migrate --current "$CONFIG_FILE" || true
    fi
fi

# 7. Symlink CLI Entry Points
echo "Creating user-space binary symlinks..."
ln -sfn "$VENV_DIR/bin/prismatic-engine" "$BIN_DIR/prismatic-engine"
ln -sfn "$VENV_DIR/bin/prismatic-admin" "$BIN_DIR/prismatic-admin"

# 8. Installation Verification and Testing
echo "Validating installation status..."

# Ensure we are fully isolated. Setting PYTHONPATH strictly resolves the 
# import path of the engine from the active target, not a developer path.
export PYTHONPATH=""
IMPORT_CHECK=$(python3 -c "
try:
    import sys
    sys.path.insert(0, '$ACTIVE_SYM')
    import prismatic
    print('SUCCESS')
except Exception as e:
    print(f'FAILED: {e}')
")

if [ "$IMPORT_CHECK" != "SUCCESS" ]; then
    echo "Error: Failed to verify imports of 'prismatic' package from $ACTIVE_SYM: $IMPORT_CHECK" >&2
    exit 1
fi

# Verify commands can execute properly
if ! "$BIN_DIR/prismatic-engine" --help &>/dev/null && ! "$BIN_DIR/prismatic-engine" -h &>/dev/null; then
    echo "Warning: CLI execution check yielded non-zero return code. Check path bindings."
fi

echo "=================================================================="
echo "Installation Successful!"
echo "------------------------------------------------------------------"
echo "Environment Details:"
echo "  - PRISMATIC_HOME:    $PRISMATIC_HOME"
echo "  - Stable Binaries:   $BIN_DIR"
echo "  - Active Symlink:    $ACTIVE_SYM -> $(readlink -f "$ACTIVE_SYM")"
echo "  - Python Venv:       $VENV_DIR"
echo "------------------------------------------------------------------"
echo "Please add the binary folder to your shell PATH environment:"
echo "  export PATH=\$PATH:$BIN_DIR"
echo "=================================================================="
```

---

### Key Decoupled Architecture Highlights

- **Import Routing via Symlinks:** Staging source files in `$PRISMATIC_HOME/.prismatic/versions/v0.1.0` and mapping `$PRISMATIC_HOME/.prismatic/active` as a symlink allows the executing daemon and worker processes to dynamically switch codebases atomically (via a symlink swap) when promoted by the CI suite on **PVE1**, without restarting long-running configurations.
- **Isolated Entry Points:** By symlinking `$VENV_DIR/bin/prismatic-engine` into a user-space directory (such as `$PRISMATIC_HOME/bin/`), executions are bound to the stable dependencies installed in `venv_stable` but resolve package imports from `$PRISMATIC_HOME/.prismatic/active` when run with:
  ```bash
  export PYTHONPATH=$PRISMATIC_HOME/.prismatic/active
  ```
- **Configuration Version Stability:** The script copies the template configuration without overwriting user overrides at `$PRISMATIC_HOME/.prismatic/config.yaml`. New parameters introduced in subsequent versions are merged using `prismatic-admin config migrate`.
