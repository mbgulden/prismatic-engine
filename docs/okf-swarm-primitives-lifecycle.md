# OKF: Swarm Primitives Lifecycle, Distribution & Sync Protocol

**Status:** Canonical Operator & Architecture Standard  
**Owner:** Prismatic Engine Core & Swarm Infrastructure  
**Scope:** `swarmlock`, `swarmcron`, `swarmrouter`, `swarmproof`  
**System of Record:** GitHub Git Repositories (`@main`) + `okf-swarm-primitives-lifecycle.md`

---

## 🎯 OKF Objectives & Key Results

```text
Objective → Key Result → Function → Evidence
```

| Objective | Key Result | Function / Workflow | System of Record & Evidence |
|---|---|---|---|
| **Zero-Drift Ecosystem Synchronization** | All 4 swarm primitives (`swarmlock`, `swarmcron`, `swarmrouter`, `swarmproof`) stay strictly synchronized between their independent GitHub repos, Prismatic Engine, and live VM services. | One-command shell updaters (`update-*.sh`) pulling exact `@main` commits into isolated venvs. | Git commit SHAs, package metadata, pip receipts, and service status logs. |
| **Decoupled Autonomous Architecture** | Each primitive functions as a standalone zero-dependency library consumable by any external project, while offering drop-in FastAPI routers. | Standard PEP 621 packaging (`pyproject.toml`) + CLI commands + FastAPI factory routers. | Wheel builds (`dist/*.whl`), 100% passing test suites, and PyPI/GitHub releases. |
| **Fail-Closed Execution Safety** | Service updates and schema changes never cause silent runtime deadlocks, memory exhaustion, or unhandled dependency crashes. | DAG dependency checking, atomic file locks, process session isolation, and token budget ceilings. | Automated adversarial test suites (`tests/test_adversarial.py`) and error exit codes. |

---

## 🏗️ Architecture & Source-of-Truth Hierarchy

```
                                  ┌────────────────────────────────┐
                                  │      GitHub Source of Truth    │
                                  │  github.com/mbgulden/<package> │
                                  │           (@main branch)       │
                                  └───────────────┬────────────────┘
                                                  │
                         ┌────────────────────────┼────────────────────────┐
                         ▼                        ▼                        ▼
               ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
               │    swarmlock     │     │    swarmcron     │     │   swarmrouter    │
               │ Distributed Lock │     │  DAG Cron & Log  │     │ Capability & Cost│
               └─────────┬────────┘     └─────────┬────────┘     └─────────┬────────┘
                         │                        │                        │
                         └────────────────────────┼────────────────────────┘
                                                  │
                                                  ▼
                               ┌─────────────────────────────────────┐
                               │       Prismatic Engine Core         │
                               │ • pyproject.toml [dependencies]     │
                               │ • pyproject.toml [primitives]       │
                               │ • scripts/ops/update-all-primitives │
                               └──────────────────┬──────────────────┘
                                                  │
                         ┌────────────────────────┴────────────────────────┐
                         ▼                                                 ▼
          ┌─────────────────────────────┐                   ┌─────────────────────────────┐
          │  Local Dev (Live Reloading) │                   │ Production Server / VM 800  │
          │  pip install -e /path/to/pkg│                   │ /home/ubuntu/.prismatic/    │
          │  (Zero-copy instant sync)   │                   │ update-*.sh (One Command)   │
          └─────────────────────────────┘                   └─────────────────────────────┘
```

---

## 📦 Swarm Primitives Registry

| Primitive | GitHub Repository | Primary Role | Interfaces |
|---|---|---|---|
| **`swarmlock`** | [`github.com/mbgulden/swarmlock`](https://github.com/mbgulden/swarmlock) | Atomic distributed file locking & multi-worker race fencing. | Python API, CLI, FastAPI Router |
| **`swarmcron`** | [`github.com/mbgulden/swarmcron`](https://github.com/mbgulden/swarmcron) | Zero-dependency DAG cron scheduler, machine execution receipts, and self-healing recovery. | Python API, CLI (`swarmcron`), FastAPI Router |
| **`swarmrouter`**| [`github.com/mbgulden/swarmrouter`](https://github.com/mbgulden/swarmrouter)| Deterministic task capability taxonomy, token cost estimation, budget ceilings, and model routing. | Python API, CLI (`swarmrouter`), FastAPI Router |
| **`swarmcurator`**| [`github.com/mbgulden/swarmcurator`](https://github.com/mbgulden/swarmcurator)| Universal task admission, anti-starvation priority aging, and lane-locking queue. | Python API, CLI (`swarmcurator`), FastAPI Router |
| **`swarmproof`** | [`github.com/mbgulden/swarmproof`](https://github.com/mbgulden/swarmproof) | Deterministic RED $\to$ GREEN test oracle, exact-head commit/tree receipts, and verification ledgers. | Python API, CLI (`swarmproof`), Verification Gate |

---

## 🛠️ How It Works in Prismatic Engine

### 1. Formal Dependency Declaration
In [`prismatic-engine/pyproject.toml`](file:///c:/Users/Michael%20Gulden/Github/prismatic-engine/pyproject.toml):

```toml
[project.optional-dependencies]
primitives = [
    "swarmlock @ git+https://github.com/mbgulden/swarmlock.git@main",
    "swarmcron @ git+https://github.com/mbgulden/swarmcron.git@main",
    "swarmrouter @ git+https://github.com/mbgulden/swarmrouter.git@main",
]
verification = [
    "swarmproof @ git+https://github.com/mbgulden/swarmproof.git@main",
]
all = ["prismatic-engine[http,redis,gateway,primitives,verification]"]
```

---

### 2. One-Command Server Update Scripts
Each primitive has a dedicated updater script in [`prismatic-engine/scripts/ops/`](file:///c:/Users/Michael%20Gulden/Github/prismatic-engine/scripts/ops/) that updates all Python virtual environments and cleanly reloads systemd gateway services:

- `scripts/ops/update-swarmlock.sh`
- `scripts/ops/update-swarmcron.sh`
- `scripts/ops/update-swarmrouter.sh`
- `scripts/ops/update-swarmproof.sh`
- `scripts/ops/update-all-primitives.sh` (Updates all 4 primitives in one pass)

---

## 🚀 How to Keep Everything Updated

### Option A: From the Production Server (or VM 800)
```bash
# Update SwarmLock
bash /home/ubuntu/.prismatic/update-swarmlock.sh

# Update SwarmCron
bash /home/ubuntu/.prismatic/update-swarmcron.sh

# Update SwarmRouter
bash /home/ubuntu/.prismatic/update-swarmrouter.sh

# Update SwarmProof
bash /home/ubuntu/.prismatic/update-swarmproof.sh

# Or update all primitives simultaneously:
bash /home/ubuntu/.prismatic/update-all-primitives.sh
```

---

### Option B: Remote One-Liner from Windows / Dev Machine
```powershell
# Update a single primitive via SSH
ssh ubuntu@100.83.32.92 "bash /home/ubuntu/.prismatic/update-swarmcron.sh"

# Or update all primitives across the server
ssh ubuntu@100.83.32.92 "bash /home/ubuntu/.prismatic/update-all-primitives.sh"
```

---

### Option C: Local Development Live Hot-Reloading
For local development where you want immediate zero-copy synchronization:
```bash
pip install -e "c:\Users\Michael Gulden\Github\swarmlock"
pip install -e "c:\Users\Michael Gulden\Github\swarmcron"
pip install -e "c:\Users\Michael Gulden\Github\swarmrouter"
```
Any code edit made in the standalone repositories is immediately live inside `prismatic-engine` and local test runners without running a build or reinstall.
