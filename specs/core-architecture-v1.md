# Architectural Blueprint: Prismatic Engine Core (Phase 1 MVP)

**Author:** Fred (Orchestrator & Infrastructure)  
**Date:** 2026-06-13  
**Status:** PROPOSED  
**Target File Path:** `PRISMATIC_HOME/specs/core-architecture-v1.md`

---

## Executive Summary

The Prismatic Engine Core governs autonomous multi-agent task execution and pipeline routing. However, when agents execute tasks using tools they are actively refactoring, state-drift and runtime crashes inevitably occur. 

To address this, **Phase 1 MVP enforces physical separation between the "Executing Runtime" (the stable, read-only system running agents) and the "Target System" (the codebase/repository under active development).** This document provides the formal architecture specification for isolating these environments, packaging the core, setting up a safe test-and-promote pipeline, defining plugin boundaries, and migrating the current repository structure.

---

## 1. System Architecture

The following diagram illustrates the separation between the **Executing Runtime** (stable execution environment) and the **Target System** (workspace under active development).

```mermaid
graph TD
    subgraph Executing_Runtime ["Executing Runtime (Stable & Isolated)"]
        direction TB
        DAEMON["prismatic-dispatcher (Daemon)"]
        STABLE_VENV["Stable Virtualenv ($PRISMATIC_HOME/.prismatic/venv_stable)"]
        STABLE_CODE["Stable Core Package ($PRISMATIC_HOME/.prismatic/active)"]
        STATE_DB["Event DB ($PRISMATIC_HOME/.prismatic/db/event_router.db)"]
        SYSTEM_CFG["Runtime Config ($PRISMATIC_HOME/.prismatic/config.yaml)"]
        LOCK_REG["Lock Registry ($PRISMATIC_HOME/.antigravity/swarm_locks.json)"]
    end

    subgraph Target_System ["Target System (Active Development)"]
        direction TB
        WORKSPACE["Dev Workspace ($PRISMATIC_HOME/work/prismatic-engine)"]
        DEV_VENV["Dev Virtualenv ($PRISMATIC_HOME/work/prismatic-engine/.venv_dev)"]
        DEV_SRC["Mutable Codebase (prismatic/)"]
        DEV_PLUGINS["Mutable Plugins (plugins/)"]
    end

    subgraph Sandbox_Environment ["Sandbox / CI Test Environment"]
        direction TB
        SANDBOX_DIR["Sandbox Build ($PRISMATIC_HOME/.prismatic/sandbox/build-git_sha)"]
        SANDBOX_VENV["Sandbox Virtualenv ($PRISMATIC_HOME/.prismatic/sandbox/venv)"]
        MOCK_LINEAR["Mock Linear Provider / Webhook"]
    end

    %% Interactions
    DAEMON -->|Reads| SYSTEM_CFG
    DAEMON -->|Queries/Updates| STATE_DB
    DAEMON -->|Controls| LOCK_REG
    DAEMON -->|Loads Code From| STABLE_CODE
    STABLE_CODE -.->|References| STABLE_VENV
    
    %% Agent Execution Lifecycle
    DAEMON -->|Spawns Agent Subprocess| AGENT_RUN["Agent Instance (e.g. Fred/Ned)"]
    AGENT_RUN -->|Reads Contract & Allowed Lanes| CONTRACT["Agent Contract ($PRISMATIC_HOME/.antigravity/contracts/*.json)"]
    AGENT_RUN -->|Modifies Files (Constrained)| WORKSPACE
    
    %% CI/CD Promotion Loop
    WORKSPACE -->|1. Export Archive| SANDBOX_DIR
    SANDBOX_DIR -->|2. Build & Install| SANDBOX_VENV
    SANDBOX_VENV -->|3. Run Tests| MOCK_LINEAR
    MOCK_LINEAR -->|4. Pass & Promote| STABLE_CODE
```

---

## 2. Dual-Runtime Isolation Strategy

### 2.1 Separation Mechanism
To prevent runtime process modification, the executing code and the development workspace must reside in different directory paths with distinct environment variables:

1. **Path-Based Isolation:**
   - **Stable Directory:** `$PRISMATIC_HOME/.prismatic/active` is a symlink pointing to a static, read-only version-controlled release directory (e.g., `$PRISMATIC_HOME/.prismatic/versions/v0.1.0/`).
   - **Active Development Repository:** `$PRISMATIC_HOME/work/prismatic-engine` is the mutable workspace. Agents operate inside this directory but *never* load their runtime libraries from it.

2. **Environment Variable Safeguards:**
   - The Executing Runtime sets `PYTHONPATH` strictly to exclude the active workspace. It resolves imports from `$PRISMATIC_HOME/.prismatic/active`.
   - The executing dispatcher daemon executes under a dedicated environment context:
     ```bash
     export PRISMATIC_RUNTIME=true
     export PRISMATIC_STATE_DIR=$PRISMATIC_HOME/.prismatic/db
     export PRISMATIC_CONFIG_PATH=$PRISMATIC_HOME/.prismatic/config.yaml
     export PYTHONPATH=$PRISMATIC_HOME/.prismatic/active
     ```

3. **Virtualenv Shadowing:**
   - **`venv_stable`:** Initialized at `$PRISMATIC_HOME/.prismatic/venv_stable`. This environment is used exclusively by the running daemon and agent CLI tools executing actual orchestration tasks.
   - **`venv_dev`:** Initialized at `$PRISMATIC_HOME/work/prismatic-engine/.venv_dev`. This environment is used by developers/agents to run linting, compile code, and run test suites.

### 2.2 Network and IPC Port Routing
If the runtime daemon and test servers run concurrently, port collisions must be avoided:
- **Production/Stable Engine Port:** `PRISMATIC_PORT=9000` (for gRPC or webhook endpoints). **Note: Phase 1 (current) — the dispatcher operates in polling mode only (Linear GraphQL API). Port 9000 binding and HTTP/gRPC server are deferred to Phase 2. The watchdog health check falls back to systemd service status.**
- **Staging/Sandbox Test Port:** `PRISMATIC_PORT=9001` (explicitly bound during sandbox validation).

### 2.3 Executing Runtime Commands
**To run the stable production daemon:**
```bash
# Executed via systemd user unit
$PRISMATIC_HOME/.prismatic/venv_stable/bin/prismatic-engine \
  --config $PRISMATIC_HOME/.prismatic/config.yaml \
  --db $PRISMATIC_HOME/.prismatic/db/event_router.db
```

**To run a local development daemon (safe verification without touching stable state):**
```bash
# Executed inside the target workspace
source $PRISMATIC_HOME/work/prismatic-engine/.venv_dev/bin/activate
export PRISMATIC_STATE_DIR=$PRISMATIC_HOME/work/prismatic-engine/scratch/dev_state
export PRISMATIC_CONFIG_PATH=$PRISMATIC_HOME/work/prismatic-engine/config/default_config.yaml
export PRISMATIC_PORT=9001

python -m prismatic.dispatcher --dev-mode
```

---

## 3. Distribution and Packaging Approach

### 3.1 Core Packaging
The Prismatic Engine Core is packaged as a standard Python source distribution and wheel using `setuptools`:
- **Package Name:** `prismatic-engine`
- **Configuration:** Defined in `pyproject.toml` utilizing modern PEP 517/518 build standards.
- **Modularity:** The core includes the event loop, state database, lock manager, and contract engine. It does *not* ship with external plugins or specific workspace scripts, which are loaded dynamically.

### 3.2 Configuration File Separation
To ensure updating the engine does not overwrite local user modifications:
1. **Default Config Template:** Resides in the source package at `prismatic/config/default_config.yaml`.
2. **User Configuration:** Saved at `$PRISMATIC_HOME/.prismatic/config.yaml`.
3. **Migration Protocol:** 
   Upon package upgrade, the core runs a config migration utility:
   ```bash
   prismatic-admin config migrate --current $PRISMATIC_HOME/.prismatic/config.yaml
   ```
   This script parses the existing YAML config, compares it with the new default schema, appends missing parameters with safe defaults, and preserves existing keys.

### 3.3 State Database Separation
The runtime state is persisted in SQLite at `$PRISMATIC_HOME/.prismatic/db/event_router.db`. The core database utilizes migration version tracking (via lightweight Python SQL scripts or Alembic). When a new version is promoted, a schema check is executed automatically:
```bash
prismatic-admin db upgrade
```

---

## 4. Safe Update Pipeline

To prevent updating a live system with broken code, the engine core utilizes a Sandboxed Test-and-Promote loop.

```
                  [Code Change Committed to deploy-fresh]
                                     │
                                     ▼
                      1. EXPORT TO STATIC SANDBOX
          ($PRISMATIC_HOME/.prismatic/sandbox/build-<sha>)
                                     │
                                     ▼
                         2. COMPILE & ISOLATE
            (Build python wheel & install in sandbox venv)
                                     │
                                     ▼
                       3. SANDBOX INTEGRATION TESTS
             (Mock Linear webhook trigger, process pipeline,
              verify ContractManager sandboxing & Mutex locks)
                                     │
                                     ├── [FAIL] ──> Abort & Log Alert
                                     │
                                     └── [PASS]
                                     │
                                     ▼
                         4. ATOMIC PROMOTION SWAP
            (Update symlink: active -> sandbox/build-<sha>)
                                     │
                                     ▼
                         5. DAEMON RESTART & PING
            (Systemd service restart & health-check verification)
                                     │
                                     ├── [HEALTHY] ──> Log Success & Commit
                                     │
                                     └── [CRASH]
                                     │
                                     ▼
                            6. AUTO-ROLLBACK
               (Revert active symlink & restore daemon)
```

### 4.1 Steps of the Promotion Pipeline

1. **Static Export:** The deployment runner exports the current code commit to a clean directory:
   ```bash
   git archive --format=tar.gz -o /tmp/prismatic-build.tar.gz HEAD
   mkdir -p $PRISMATIC_HOME/.prismatic/sandbox/build-$GIT_SHA
   tar -xzf /tmp/prismatic-build.tar.gz -C $PRISMATIC_HOME/.prismatic/sandbox/build-$GIT_SHA
   ```
2. **Compile:** A clean virtualenv is built inside the sandbox directory:
   ```bash
   python3 -m venv $PRISMATIC_HOME/.prismatic/sandbox/build-$GIT_SHA/venv
   source $PRISMATIC_HOME/.prismatic/sandbox/build-$GIT_SHA/venv/bin/activate
   pip install --upgrade pip build
   pip install $PRISMATIC_HOME/.prismatic/sandbox/build-$GIT_SHA/
   ```
3. **Sandbox Integration Testing:**
   - A mock test execution is triggered on port `9001`.
   - The test script feeds a mock Linear signal into the dispatcher, simulates an agent task generation, verifies `ContractManager` applies directory constraints, and ensures `SwarmLockManager` locks and unlocks successfully.
4. **Staged Promotion (Canary):**
   - Staged rollout is handled at the task routing layer. The dispatcher will route only a specific metadata label (e.g. issues tagged `prismatic-canary`) through the sandboxed runner environment.
5. **Atomic Promotion (Active Symlink Swap):**
   - If tests pass, the main active symlink is updated atomically:
     ```bash
     ln -sfn $PRISMATIC_HOME/.prismatic/sandbox/build-$GIT_SHA $PRISMATIC_HOME/.prismatic/active
     ```
   - Restart the daemon unit:
     ```bash
     systemctl --user restart prismatic-dispatcher.service
     ```
6. **Watchdog and Auto-Rollback:**
   - The daemon writes its process ID and a heartbeat to `$PRISMATIC_HOME/.prismatic/run/heartbeat.pid`.
   - A watchdog background script (referencing the patterns in `Watchdog.ts` and `StallDetector.ts`) monitors the startup sequence.
   - If the daemon crashes on boot or throws repeated exceptions within 120 seconds, the watchdog triggers a rollback:
     ```bash
     # Revert active symlink to previous stable path
     PREV_STABLE=$(readlink -f $PRISMATIC_HOME/.prismatic/previous)
     ln -sfn $PREV_STABLE $PRISMATIC_HOME/.prismatic/active
     systemctl --user restart prismatic-dispatcher.service
     ```

---

## 5. Plugin Architecture Interface

To allow diverse extensions (Design, Video, Audio, Asset management) to hook into the Prismatic Core without mutating its source code, we define a strict plugin interface boundary.

### 5.1 The Abstract Boundary
Plugins are python packages that expose a class inheriting from the abstract base class `PrismaticPlugin`. The core loads plugins dynamically from the `$PRISMATIC_HOME/plugins/` directory (or configured path) by searching for directories containing a `plugin-manifest.yaml`.

### 5.2 `plugin-manifest.yaml` Format
Every plugin must include a manifest file defining metadata, entry points, compatibility constraints, and registration details:

```yaml
name: "vram-observability"
version: "1.0.0"
description: "Monitors GPU memory allocation and integrates warnings into the BudgetManager"
author: "Fred (agent:fred)"
entry_point: "vram_observability.plugin:VRAMObservabilityPlugin"
core_version_constraint: ">=1.0.0, <2.0.0"

# External Python packages required by this plugin
dependencies:
  pip:
    - GPUtil>=1.4.0

# Persona extensions this plugin adds to the engine
personas:
  - id: "GPU-COMPUTE-OBSERVER"
    displayName: "GPU Compute Observability Specialist"
    systemPrompt: |
      You are the GPU Compute Observability Specialist. You analyze GPU usage,
      detect memory leaks, and advice on optimal batch configurations.
      --- HARD RESTRICTIONS ---
      1. Write reports only into the designated analytics folder.
    defaultAllowedDirectories: ["reports/gpu/"]
    defaultReadOnlyDirectories: ["src/", "infra/"]
    preferredHead: "Headless API"
    maxActions: 10
```

### 5.3 Python Hook System
The core exposes lifecycle and event hooks. Plugins subscribe to these hooks by implementing the respective methods:

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, List

class PluginContext:
    def __init__(self, config: Dict[str, Any], db_connection: Any):
        self.config = config
        self.db = db_connection

class AgentContract:
    def __init__(self, thread_id: str, persona_id: str, allowed_dirs: List[str]):
        self.thread_id = thread_id
        self.persona_id = persona_id
        self.allowed_dirs = allowed_dirs

class PrismaticPlugin(ABC):
    @abstractmethod
    def on_init(self, context: PluginContext) -> None:
        """Executed when the core dispatcher initializes."""
        pass

    @abstractmethod
    def register_tools(self) -> List[Dict[str, Any]]:
        """Return a list of tool definitions to append to agent contexts."""
        return []

    # --- Core Lifecycle Hooks ---
    def before_task_execution(self, contract: AgentContract) -> None:
        """Called immediately before an agent worker is spawned."""
        pass

    def after_task_execution(self, contract: AgentContract, execution_result: Dict[str, Any]) -> None:
        """Called immediately after an agent worker exits."""
        pass

    def on_state_transition(self, issue_id: str, from_state: str, to_state: str) -> None:
        """Triggered when a Linear ticket changes status."""
        pass
```

### 5.4 Plugin Sandboxing
1. **Dynamic Importing:** Plugins are imported dynamically. During initialization, the loader validates that the plugin's dependencies are installed. To isolate plugin execution, the core runs each hook execution inside a try-catch scope to prevent plugin crashes from taking down the core dispatcher event loop.
2. **Security & Directory Bounds:** If a plugin registers custom tools, those tools must validate paths using the active task's `AgentContract` (exactly like the Hub's `ContractManager.ts` pattern). If a tool tries to write outside the contract's allowed directory bounds, the core throws a safety exception before executing the file operation.

---

## 6. Directory Structure for the Core Repository

The core repository should remain clean and focused on orchestration logic, interfaces, and testing utilities. Plugins and local scripts are separated into target subfolders.

```
prismatic-engine/ (Core Repository)
├── .github/
│   └── workflows/
│       ├── test.yml                 # Runs unit & integration tests on pull requests
│       └── publish.yml              # Publishes python package & triggers staging build
├── config/
│   └── default_config.yaml          # Base configuration schema template
├── docs/
│   └── architecture/                # Detailed specifications and flowcharts
├── prismatic/                       # Core Package Source Code
│   ├── __init__.py
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── admin.py                 # Core admin controls (db upgrade, config migration)
│   │   └── client.py                # Command line workspace interfaces
│   ├── core/
│   │   ├── __init__.py
│   │   ├── contracts.py             # ContractManager: generates & enforces agent constraints
│   │   ├── dispatcher.py            # Polling event loop and task router (Linear listener)
│   │   ├── handoff.py               # HandoffProtocol: ephemeral webhook server for yields
│   │   ├── locking.py               # SwarmLockManager: workspace concurrency mutexes
│   │   ├── orchestration.py         # SwarmOrchestrator: manages dual-track task execution
│   │   ├── planning.py              # SwarmPlanner: decomposes prompts based on persona constraints
│   │   └── registry.py              # Persona and plugin loading management
│   ├── interface/
│   │   ├── __init__.py
│   │   ├── hooks.py                 # Abstract hook declarations
│   │   └── plugin.py                # PrismaticPlugin base class and manifest parser
│   └── utils/
│       ├── __init__.py
│       └── telemetry.py             # BudgetManager: tracks compute budgets & usage telemetry
├── tests/
│   ├── __init__.py
│   ├── mock_providers/              # Mocks for Linear API, webhook nudges, and local CLI
│   ├── unit/                        # Core unit tests (locking, contract syntax, registry)
│   └── integration/                 # Sandbox integration tests
├── LICENSE
├── PRISMATIC_ENGINE.yaml            # Workspace governance rules for repository edits
├── pyproject.toml                   # Python build system metadata
├── README.md
└── setup.cfg
```

---

## 7. VM-Level Sandbox Isolation (Addendum)

Per Michael's directive, the sandbox/test environment must be **physically isolated** from the live runtime, not just logically separated on the same VM.

### 7.1 Isolation Topology

```
┌─────────────────────────────────────────────────────┐
│  PVE3 (or dedicated VM)                             │
│  ┌───────────────────────────────────────────────┐  │
│  │  Live Runtime ($PRISMATIC_HOME/.prismatic/)   │  │
│  │  - Dispatcher daemon (port 9000)              │  │
│  │  - Stable venv, state DB, lock registry       │  │
│  │  - Agents: Fred, Ned, Kai, Jules              │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  PVE1 (or separate server)                          │
│  ┌───────────────────────────────────────────────┐  │
│  │  Sandbox Environment                          │  │
│  │  - Docker container OR                        │  │
│  │  - Restricted user: prismatic-sandbox         │  │
│  │  - Port 9001 (isolated gRPC/webhook)          │  │
│  │  - Mock Linear provider                       │  │
│  │  - No access to live state DB or lock reg     │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 7.2 Implementation Options

**Option A: Docker Container (Preferred)**
```bash
# Sandbox container with strict resource limits
docker run --rm \
  --name prismatic-sandbox \
  --cpus=4 --memory=8g \
  --network=prismatic-sandbox-net \
  -v $SANDBOX_BUILD:/build:ro \
  prismatic-sandbox:latest
```

**Option B: Restricted Linux User**
```bash
# Dedicated user with no sudo, isolated home
sudo adduser --disabled-password --no-create-home prismatic-sandbox
sudo chroot /opt/prismatic-sandbox /bin/su - prismatic-sandbox
```

### 7.3 Config Externalization for VM Topology

Step 1 must read environment variables for cross-VM routing:

```yaml
# $PRISMATIC_HOME/.prismatic/config.yaml
sandbox:
  host: ${PRISMATIC_SANDBOX_HOST:-pve1}
  port: ${PRISMATIC_SANDBOX_PORT:-9001}
  transport: ${PRISMATIC_SANDBOX_TRANSPORT:-docker}  # docker | ssh | local-user
  build_path: ${PRISMATIC_SANDBOX_BUILD_PATH:-/opt/prismatic-sandbox/builds}
```

### 7.4 Promotion Pipeline (Updated)

Step 4 of the promotion pipeline now deploys to the **remote sandbox VM**, not a local directory:

```
[Code Commit] → [Export Archive] → [SCP to PVE1] → [Docker Build]
→ [Sandbox Tests on PVE1] → [PASS] → [Atomic Symlink Swap on PVE3]
→ [Daemon Restart] → [Watchdog Health Check]
```

---

## 8. Migration Path to v1

The transition from the current state to the decoupled architecture will follow a 5-step migration path:

### Step 1: Externalize Configurations & Database (Current State -> Next 2 Days)
- Refactor `prismatic/dispatcher.py` to remove hardcoded paths to state directories.
- Redirect SQLite database writes from `./prismatic_state` to `os.environ.get("PRISMATIC_STATE_DIR")` defaulting to `$PRISMATIC_HOME/.prismatic/db`.
- Separate user configurations from the repository files, reading strictly from `$PRISMATIC_HOME/.prismatic/config.yaml`.

### Step 2: Establish the Stable Executing Environment (Next 4 Days)
- Build the initial python wheel using `python3 -m build`.
- Initialize `$PRISMATIC_HOME/.prismatic/venv_stable/` and install the package using pip.
- Configure systemd user services to launch the dispatcher out of the stable environment folder:
  `$PRISMATIC_HOME/.prismatic/venv_stable/bin/prismatic-engine`.

### Step 3: Implement the Plugin Loader & Interface (Next 7 Days)
- Add `prismatic/interface` modules.
- Refactor the dispatcher's agent launching routines to read persona configurations dynamically from registered plugin manifests rather than relying on a hardcoded list.
- Install the first utility plugin (e.g. `hermes-plugin-swarm-manager`) into `$PRISMATIC_HOME/plugins/` to verify dynamic import operations.

### Step 4: Configure the Sandbox Promotion Pipeline (Next 10 Days)
- Write the test-and-promote script `scripts/deploy_core.sh`.
- Set up the pre-commit git hook rules in `$PRISMATIC_HOME/work/prismatic-engine/.git/hooks/pre-push` to enforce the lanes defined in `PRISMATIC_ENGINE.yaml`.
- Integrate the watchdog monitor to trigger rollbacks if the daemon crashes on boot.

### Step 5: Cutover and Lock Validation (Next 12 Days)
- Transition running agents to read and write locks strictly via the compiled version of `prismatic-lock` inside `$PRISMATIC_HOME/.prismatic/venv_stable/`.
- Perform a live trial using a non-production repository. Ensure the executing agent runtime is unaffected when refactoring code in the development workspace.
