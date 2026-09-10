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
| **SQLite Multi-Agent Concurrency & WAL Hardening** | Concurrent agent turns (Fred, George, Kai, Ned) write simultaneously to SQLite stores without transient lock contention or unhandled `database is locked` errors. | Mandatory connection PRAGMAs (`WAL`, `busy_timeout=5000`, `synchronous=NORMAL`, `foreign_keys=ON`) and exponential backoff retry loop (`execute_with_retry`). | [`prismatic/fleet/db.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/fleet/db.py), [`tests/test_sqlite_wal_concurrency.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_sqlite_wal_concurrency.py) (6/6 PASSED), and fleet-wide WAL migration hook. |
| **Filesystem Write Fencing (`prismatic exec`)** | Arbitrary scripts, test runs, or subagent tasks execute enclosed in an unconditional SwarmLock lease envelope with fail-closed deflection (423), SIGINT/SIGTERM trapping, and pre-commit Python AST syntax checks. | Supervisor CLI `prismatic exec` lifecycle: acquire SwarmLock → emit `fenced_exec_started` → run subprocess → AST compile check → guaranteed `finally:` release and `fenced_exec_finished`. | [`prismatic/client/exec.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/client/exec.py), [`tests/test_prismatic_exec.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_prismatic_exec.py) (9/9 PASSED), and gateway `/api/gateway/swarmlock/status`. |
| **Telegram Multi-Bot Rate Limiting & Daemon Collision Prevention** | Multi-bot swarm streams progressively to single operator chat (`8190664947`) and launches interactive CLI sessions without triggering Telegram HTTP 429 flood limits or HTTP 409 Conflict daemon crash loops. | `DynamicTelegramThrottler` (0.8s $\rightarrow$ 1.6s $\rightarrow$ 2.5s), HTTP 429 flood recovery (`parameters.retry_after + 0.5s`), and `daemon_collision_guard` auto-pausing/restoring `hermes-gateway@<profile>.service`. | [`prismatic/fleet/telegram.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/fleet/telegram.py), [`tests/test_telegram_streaming_throttler.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_telegram_streaming_throttler.py) (16/16 PASSED), and gateway throttler endpoints. |
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
6. **SQLite Multi-Agent Concurrency & WAL Hardening Fence**:
   - **Invariant**: All database connection initializations across the fleet MUST enforce WAL mode (`PRAGMA journal_mode = WAL;`), a 5000ms busy retry timeout (`PRAGMA busy_timeout = 5000;`), synchronous normal (`PRAGMA synchronous = NORMAL;`), and foreign keys enabled (`PRAGMA foreign_keys = ON;`). Multi-database dual writes must execute inside `execute_with_retry` with exponential backoff to eliminate `database is locked` errors during multi-agent concurrency bursts.
   - **Fleet Migration Hook**: `prismatic fleet sync` scans and checkpoints all existing databases (`~/.hermes/state.db` and all profile databases) via `PRAGMA wal_checkpoint(TRUNCATE);` to ensure zero stale lock files or un-migrated rollback journals.
7. **Telegram Multi-Bot Rate Limiting & Daemon Collision Prevention Fence**:
   - **Dynamic Cadence Invariant**: Multiple bots streaming progressive updates to chat `8190664947` coordinate edit pacing via `DynamicTelegramThrottler`. Cadence dynamically scales based on concurrent streamer count: `0.8s` for $\le 1$ active bot, `1.6s` for 2 active bots, and `2.5s` for 3+ active bots. This ensures total per-chat edit rate never exceeds Telegram flood limits (~20–30 req/min).
   - **HTTP 429 Flood Recovery**: `TelegramStreamer.edit_message` catches `httpx.HTTPStatusError` (and HTTP 429 status codes), extracts `parameters.retry_after` (default 3.0s), backs off for `retry_after + 0.5s`, and retries without crashing the calling agent loop.
   - **Unclosed Markdown Delimiter Resilience (HTTP 400 Fallback)**: Mid-stream progressive edits often emit unclosed code blocks (e.g. ```` ```python\n ... ````) or dangling emphasis symbols (`*`, `_`) that trigger Telegram's `400 Bad Request: can't parse entities`. `TelegramStreamer.edit_message` and `TelegramStreamer.start` detect HTTP 400 and immediately retry without `parse_mode`, delivering the turn cleanly as plain text without crashing or halting the agent turn.
   - **Daemon Collision Prevention (HTTP 409)**: Before launching interactive terminal sessions, `prismatic chat --profile <profile>` checks whether `systemctl is-active hermes-gateway@<profile>.service` returns 0. If active, `daemon_collision_guard` auto-pauses the service, executes the interactive session, and restores the systemd unit in a `finally:` block, preventing Telegram polling conflicts.
8. **Post-Compression Context & State Preservation Fence**:
   - **Operational State Anchor**: Whenever context compression fires (at 75% threshold or via operator trigger), the compression routine injects the structured, un-summarizable operational state header verbatim at the very top of the compressed turn:
     ```markdown
     ### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)
     - **Active Task ID:** {task_id}
     - **Active SwarmLock Leases:** {held_locks}
     - **Git Branch & HEAD Commit:** {git_head}
     - **Modified Working Files:** {modified_files}
     - **Completed Steps:** {completed_steps}
     - **Immediate Next Step:** {next_step}
     ```
   - **State Extraction Pre-Hook**: Queries `/api/gateway/swarmlock/status` for active mutex leases held by the agent, inspects session turns in `state.db` for the active Linear issue ID (e.g. `GRO-4852`), completed steps, and immediate next step, and queries `git rev-parse --short HEAD` + `git status --porcelain`.
   - **Post-Compression Verification Gate**: Intercepts the summarizer LLM output. If the model dropped or corrupted `CRITICAL OPERATIONAL STATE` or the active `task_id`, the gate programmatically prepends the exact operational header (fail-closed) before committing the turn to SQLite `state.db`.
   - **Context Headroom Guarantee**: Condenses bloated conversations (e.g. 50,000 tokens) to below 10,000 tokens while preserving 100% of live operational handles, completely eliminating post-compression amnesia and command looping.

---

## 📋 Operator Runbook & Canonical Commands

### 1. Check Fleet Status
```bash
python3 -m prismatic.cli fleet status --dynamic
```
Returns profile name, active session ID, context window, 75% threshold, headroom, token count, utilization %, and systemd service status.

### 2. Synchronize Fleet Hygiene & Migrate Databases to WAL
```bash
python3 -m prismatic.cli fleet sync --dynamic
```
Iterates across all registered profiles, discovers actual model context length from upstream vLLM `/v1/models`, calculates 75% threshold with 25% headroom, updates `config.yaml`, and automatically runs the WAL migration and truncation checkpoint across all 25+ SQLite `state.db` files.

### 3. Trigger State-Preserving Operational Context Compression
```bash
python3 -m prismatic.cli fleet compress test_agent --force --json
```
Runs the state extraction pre-hook, compresses conversation history while preserving the operational state anchor, passes through the post-compression verification gate, and updates `state.db` with guaranteed <10,000 tokens.

### 4. Run Fenced Subprocess Under SwarmLock Mutex (`prismatic exec`)
```bash
python3 -m prismatic.cli exec \
  --resource "prismatic/mesh/tailscale.py" \
  --task "GRO-4852" \
  --agent-id "kai" \
  --lease-seconds 120 \
  -- python3 build_component.py
```
Safely acquires a mutex lease, streams process output, traps signals, verifies python syntax, and guarantees lease release.

### 5. Interactive Chat Session with Daemon Collision Guard
```bash
python3 -m prismatic.cli chat --profile george -- -q "Status report"
# or
python3 -m prismatic.cli fleet chat --profile kai
```
Checks for running `hermes-gateway@<profile>.service`, auto-pauses the daemon to prevent HTTP 409 Conflict polling errors, executes the interactive chat session, and restores the systemd service upon exit.

### 6. Run Unified Multi-Surface Streaming Audit
```bash
python3 scripts/run_unified_streaming_swarm_audit.py
```
Executes the four-agent sequential audit (Fred → George → Kai → Ned) across Telegram, Prismatic Hub Signals, SwarmLock, and Hermes Dashboard with `DynamicTelegramThrottler` pacing.

### 7. Capture Visual Audit Evidence (Playwright)
```bash
NODE_PATH=/home/ubuntu/work/prismatic-engine/node_modules node /home/ubuntu/.gemini/antigravity-cli/brain/9762816f-cd24-4d2d-b3d2-b5455aaeb213/scratch/capture_unified_streaming_evidence.js
```
Captures 1440x900 desktop and 375x812 mobile screenshots across the Signals console and Hermes Dashboard portal.

---

## 📊 Verification Receipts

- **Post-Compression Context & State Preservation**: [`tests/test_compression_preservation.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_compression_preservation.py) (7/7 PASSED)
- **Telegram Multi-Bot Rate Limiting & Daemon Collision Prevention**: [`tests/test_telegram_streaming_throttler.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_telegram_streaming_throttler.py) (19/19 PASSED)
- **SQLite Concurrency & WAL Hardening**: [`tests/test_sqlite_wal_concurrency.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_sqlite_wal_concurrency.py) (6/6 PASSED)
- **Fenced Execution Supervisor**: [`tests/test_prismatic_exec.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_prismatic_exec.py) (9/9 PASSED)
- **Unit Tests**: [`tests/test_fleet_manager.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_fleet_manager.py) (6/6 PASSED)
- **Concurrency Barrage**: [`tests/test_multi_agent_concurrency_barrage.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_multi_agent_concurrency_barrage.py) (5/5 PASSED)
- **Documentation Parity**: [`tests/test_okf_docs.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_okf_docs.py) (7/7 PASSED)
- **Unified Audit Report**: [`docs/UNIFIED_STREAMING_SWARM_AUDIT.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/UNIFIED_STREAMING_SWARM_AUDIT.md) (SHA-256: `0472a672714a19f10831fe87b090d4b3d59ba88a64fb49731d117b5981fc1863`)
