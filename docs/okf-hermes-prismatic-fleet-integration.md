# OKF: Hermes Multi-Agent Fleet & Prismatic Engine Integration

**Status:** Canonical Operator & Architecture Standard  
**Owner:** Swarm Orchestration & Fleet Maintainers  
**Scope:** `orchestrator` (Fred), `george`, `kai`, `ned`, `autobot`, `next-step`, `hdengine`  
**System of Record:** SwarmLock Leases + Prismatic Signals + Hermes `state.db` + Telegram Edit Receipts  
**Registry Objective ID:** `hermes-fleet-prismatic-integration`

---

## 🎯 OKF Objectives & Key Results

```text
Objective → Key Result → Function → Evidence
```

| Objective | Key Result | Function / Workflow | System of Record & Evidence |
|---|---|---|---|
| **Zero-Collision Concurrent File Mutation** | Multiple autonomous agents (Fred, George, Kai, Ned) mutate shared workspace documents without clobbering, race conditions, or unhandled file corruption. | Distributed mutex leases via `/api/gateway/swarmlock/acquire` and `/release` with explicit TTL, intention tagging, and collision deflection tracking. | Gateway in-memory lease ledger, collision deflection logs, and Playwright live lease visualizer screenshots. |
| **Filesystem Write Fencing (`prismatic exec`)** | Arbitrary scripts, test runs, or subagent tasks execute enclosed in an unconditional SwarmLock lease envelope with fail-closed deflection (423), SIGINT/SIGTERM trapping, and pre-commit Python AST syntax checks. | Supervisor CLI `prismatic exec` lifecycle: acquire SwarmLock → emit `fenced_exec_started` → run subprocess → AST compile check → guaranteed `finally:` release and `fenced_exec_finished`. | [`prismatic/client/exec.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/client/exec.py), [`tests/test_prismatic_exec.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_prismatic_exec.py) (9/9 PASSED), and gateway `/api/gateway/swarmlock/status`. |
| **Fail-Closed Context & Token Hygiene** | Background autonomous runs never exceed model context windows or trigger degenerate token loops (`Le 0LEASE 0...`). | Dynamic 75% `/compress` threshold enforcement (`int(context_window * 0.75)` with 25% reasoning headroom and vLLM runtime discovery), active session rotation, and authenticated VLLM API key scoping. | `prismatic fleet sync --dynamic`, `tests/test_fleet_manager.py` (6/6 passed), and SQLite `state.db` session token counters. |
| **Unified Multi-Surface Real-Time Streaming** | Swarm activities stream continuously and simultaneously to operator mobile chats, web dashboards, and desktop portals. | Progressive Telegram message editing (0.8s cadence), SSE telemetry stream (`/api/gateway/signals/stream`), and SQLite session synchronization into Hermes Desktop Portal (port 9119). | Telegram message edit receipts (chat `8190664947`), SSE signal packets, and Playwright desktop/mobile snapshots. |
| **Standardized Single-Command Fleet Lifecycle** | Operator onboards, updates, resets, and monitors the entire multi-profile agent fleet using single canonical commands rather than brittle manual configurations. | Systemd template service (`hermes-gateway@.service`), CLI commands (`prismatic fleet status/sync/reset`), and automated profile config migration. | `systemctl status hermes-gateway@<profile>`, CLI outputs, and centralized fleet JSON reports. |

---

## 🏗️ Architecture & Five-Layer Integration Topology

```text
                                  ┌─────────────────────────────────────────┐
                                  │      Michael Gulden (Operator)          │
                                  └───────────────┬─────────────────────────┘
                                                  │
                         ┌────────────────────────┼────────────────────────┐
                         ▼                        ▼                        ▼
               ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
               │  Telegram Bots   │     │  Prismatic Hub   │     │ Hermes Desktop   │
               │  (@Fred, George, │     │  Signals Console │     │ Portal (port 9119│
               │   Kai, Ned)      │     │  (port 9000/SSE) │     │ /chat & sessions)│
               └─────────▲────────┘     └─────────▲────────┘     └─────────▲────────┘
                         │                        │                        │
                         │              ┌─────────┴────────┐               │
                         │              │  Prismatic Hub   │               │
                         │              │  Gateway Server  │               │
                         │              │  (Port 9000)     │               │
                         │              └─────────▲────────┘               │
                         │                        │                        │
                         ├────────────────────────┴────────────────────────┤
                         │        SwarmLock Mutex & Telemetry Engine       │
                         └────────────────────────▲────────────────────────┘
                                                  │
            ┌─────────────────────────────────────┴─────────────────────────────────────┐
            │                     Hermes Multi-Agent Fleet Core                         │
            │                                                                           │
            │   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────┐   │
            │   │  orchestrator │   │    george     │   │      kai      │   │  ned  │   │
            │   │  (Lead/Fred)  │   │ (Concurrency) │   │  (UI/Mobile)  │   │(Proof)│   │
            │   └───────┬───────┘   └───────┬───────┘   └───────┬───────┘   └───┬───┘   │
            │           │                   │                   │               │       │
            └───────────┼───────────────────┼───────────────────┼───────────────┼───────┘
                        ▼                   ▼                   ▼               ▼
            ┌───────────────────────────────────────────────────────────────────────────┐
            │                  Local LLM Server (vLLM Engine)                           │
            │       http://192.168.1.230:8000/v1 (`local-qwen-27b-q8-fred`)            │
            │       Authentication: `VLLM_FRED_API_KEY` (Scoped & Verified)             │
            └───────────────────────────────────────────────────────────────────────────┘
```

---

## 🔒 Invariant & Failure-Mode Fencing

1. **Context Bloat & Token Degradation Fence**:
   - **Invariant**: No Hermes session is permitted to accumulate more than 75% of its effective model context window without an automatic `/compress` invocation (25% reserved for reasoning headroom). Manual thresholds >= 90% are strictly rejected.
   - **Failure Mode**: When unmanaged sessions approach context window limits, AWQ 4-bit quantization breaks down, producing repetitive single-character loops.
   - **Mitigation**: `FleetManager.resolve_profile_thresholds()` dynamically discovers context length from upstream vLLM models and config, calculates 75% threshold, and `FleetManager.run_auto_hygiene()` rotates bloated sessions.
2. **Distributed Mutation Fence (SwarmLock)**:
   - **Invariant**: No agent may author, refactor, or delete shared documentation or code files without holding an active SwarmLock lease (`/api/gateway/swarmlock/acquire`).
   - **Failure Mode**: Uncoordinated parallel edits clobber preceding changes, leading to lost work or corrupt markdown tables.
   - **Mitigation**: Gateway enforces single-lease acquisition per resource; concurrent requests are deflected with `423 Locked` and registered in the deflection ledger.
3. **Telemetry & Audit Signal Fence**:
   - **Invariant**: Every state change, phase dispatch, lease acquisition, and verification seal MUST emit a typed telemetry signal (`/api/gateway/signals/emit`).
   - **Failure Mode**: Silent failures in background subagents go unnoticed by the operator until long after task deadlines.
   - **Mitigation**: Real-time SSE stream pushes events directly into the browser dashboard and updates the mobile status visualizer.
4. **Desktop Portal & Database Sync Fence**:
   - **Invariant**: Agent turns recorded in SQLite `state.db` MUST populate `profile_name`, `last_activity_at`, and `active=1` across both profile-specific stores and the root `~/.hermes/state.db`.
   - **Failure Mode**: The Hermes Web Dashboard displays stale or empty chat histories when querying profile-agnostic endpoints.
   - **Mitigation**: Multi-database dual-write pattern synchronizes session entries to both stores atomically.
5. **Filesystem Write Fencing & Process Supervisor Fence (`prismatic exec`)**:
   - **Invariant**: Subagents, scripts, and build tasks mutating workspace files must execute enclosed within `prismatic exec`. Commands are strictly fail-closed: if the target resource is already locked by another agent, the supervisor deflects immediately with `423 Locked` without executing the child process. Upon normal termination, non-zero failure, unhandled crash, or `SIGINT`/`SIGTERM` cancellation, the SwarmLock lease is unconditionally released in a `finally:` block with `fenced_exec_finished` telemetry signal emitted, guaranteeing 0 dangling leases.
   - **Pre-Commit Syntax Validation**: For `.py` resources, the supervisor validates AST integrity via `py_compile.compile(resource, doraise=True)`. If invalid syntax is detected, it emits a `CRITICAL` `pre_commit_syntax_failure` telemetry signal to Prismatic Hub and exits with status 1 before releasing the lease.

---

## 📋 Operator Runbook & Canonical Commands

### 1. Check Fleet Status
```bash
python3 -m prismatic.cli fleet status --dynamic
```
Returns profile name, active session ID, context window, 75% threshold, headroom, token count, utilization %, and systemd service status.

### 2. Synchronize Fleet Hygiene with Dynamic vLLM Discovery
```bash
python3 -m prismatic.cli fleet sync --dynamic
```
Iterates across all registered profiles, discovers actual model context length from upstream vLLM `/v1/models` and config metadata, dynamically calculates the 75% threshold with 25% headroom, and updates `config.yaml`.

### 3. Run Fenced Subprocess Under SwarmLock Mutex (`prismatic exec`)
```bash
python3 -m prismatic.cli exec \
  --resource "prismatic/mesh/tailscale.py" \
  --task "GRO-4852" \
  --agent-id "kai" \
  --lease-seconds 120 \
  -- python3 build_component.py
```
Safely acquires a mutex lease, streams process output, traps signals, verifies python syntax, and guarantees lease release.

### 4. Run Unified Multi-Surface Streaming Audit
```bash
python3 scripts/run_unified_streaming_swarm_audit.py
```
Executes the four-agent sequential audit (Fred → George → Kai → Ned) across Telegram, Prismatic Hub Signals, SwarmLock, and Hermes Dashboard.

### 5. Capture Visual Audit Evidence (Playwright)
```bash
NODE_PATH=/home/ubuntu/work/prismatic-engine/node_modules node /home/ubuntu/.gemini/antigravity-cli/brain/9762816f-cd24-4d2d-b3d2-b5455aaeb213/scratch/capture_unified_streaming_evidence.js
```
Captures 1440x900 desktop and 375x812 mobile screenshots across the Signals console and Hermes Dashboard portal.

---

## 📊 Verification Receipts

- **Fenced Execution Supervisor**: [`tests/test_prismatic_exec.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_prismatic_exec.py) (9/9 PASSED)
- **Unit Tests**: [`tests/test_fleet_manager.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_fleet_manager.py) (6/6 PASSED)
- **Concurrency Barrage**: [`tests/test_multi_agent_concurrency_barrage.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_multi_agent_concurrency_barrage.py) (5/5 PASSED)
- **Documentation Parity**: [`tests/test_okf_docs.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_okf_docs.py) (7/7 PASSED)
- **Unified Audit Report**: [`docs/UNIFIED_STREAMING_SWARM_AUDIT.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/UNIFIED_STREAMING_SWARM_AUDIT.md) (SHA-256: `0472a672714a19f10831fe87b090d4b3d59ba88a64fb49731d117b5981fc1863`)
