# OKF: Google Antigravity SDK Cluster Fleet & Resilient Satellite Client Integration

**Status:** Canonical Operator & Architecture Standard
**Owner:** Prismatic Engine Core & Antigravity Orchestration Maintainers
**Scope:** PVE Cluster (`pve1` Server GPU, `pve3`), `webtop-hermes` (Gateway/Hub), `lightbringer-windows` (Operator Satellite), `AGYSDKHarness`
**System of Record:** SwarmLock Mutex Leases + Prismatic Signals + Antigravity SDK Sessions + Tailscale Mesh Identity
**Registry Objective ID:** `antigravity-sdk-cluster-integration`

---

## 🎯 OKF Objectives & Key Results

```text
Objective → Key Result → Function → Evidence
```

| Objective | Key Result | Function / Workflow | System of Record & Evidence |
|---|---|---|---|
| **In-Process SwarmLock & File Mutation Fencing** | 100% of write tool calls (`edit_file`, `create_file`, `write_to_file`, mutating `run_command`) are intercepted in-process before execution. Zero concurrent write collisions across instances without subprocess overhead (<2ms). | Native SDK `@hooks.pre_tool_call_decide` querying `SwarmLockClient.acquire()` with immediate synthetic deflection (`423 Locked`) upon contention; guaranteed `@hooks.post_tool_call` release. | [`prismatic/gateway/server.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/gateway/server.py), Gateway lease ledger, collision deflection logs, and Playwright live lease visualizer. |
| **Real-Time Hypervisor Signal Streaming** | Every SDK agent thought, tool call, execution result, and error streams live to Prismatic Hub and mobile Telegram without ANSI parsing or terminal scraping lag (<50ms latency). | Native SDK `@hooks.post_tool_call` and `@agent.on_turn_end` emitting typed signals to Gateway `/api/gateway/signals/emit`, paced through `DynamicTelegramThrottler`. | SSE signal stream (`/api/gateway/signals/stream`), Telegram edit receipts (`8190664947`), and Playwright desktop/mobile snapshots. |
| **Native Post-Compression Context Preservation** | Conversation memory compaction never discards active Linear task IDs, active SwarmLock leases, git HEAD hashes, or immediate next steps (Directive 05 compliance). | Native SDK `@hooks.on_compaction` extracting operational state and programmatically injecting the `### 📌 CRITICAL OPERATIONAL STATE` anchor block with fail-closed re-anchoring. | [`prismatic/fleet/manager.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/fleet/manager.py), [`tests/test_compression_preservation.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_compression_preservation.py), and SQLite `state.db` verified summaries. |
| **PVE Cluster Distributed Compute & GPU Affinity** | 24/7 background swarm tasks execute on dedicated server hardware: GPU workloads to **PVE1** (Fred & Ned server GPU), concurrency & UI workloads to **PVE3** (George & Kai), control plane to **Hermes**. | 24/7 systemd worker daemons (`hermes-worker@pve1.service`, `hermes-worker@pve3.service`) polling Gateway `/api/gateway/tasks/claim` with server capability matching. | Gateway task queue ledger, PVE worker systemd logs, and worker heartbeat health endpoints. |
| **Resilient Operator Satellite Client Protocol** | Michael's development laptop (**Lightbringer**) participates interactively in SwarmLock and telemetry when awake, with **zero background task dependencies**. Laptop sleep/travel never stalls the swarm. | SDK satellite registration (`role: "operator_satellite"`); interactive in-session lock warnings; ad-hoc local GPU acceleration for Michael's personal prompt turns; automatic TTL lease drain on sleep. | Gateway client registry, Tailscale connection status, and interactive deflection logs. |
| **Multimodal Visual QA & Mobile Viewport Audits** | Antigravity agents visually inspect rendered Web UI and 375px mobile snapshots directly in-session to verify accessibility, layout integrity, and logo guidelines. | Native SDK `Image.from_file()` ingestion into Gemini multimodal vision context; automated pass/fail assertion for mobile layout, logo contrast, and ARIA attributes. | Playwright screenshot artifacts, audit verification ledgers, and multimodal audit receipts. |

---

## 🏗️ Architecture & Infrastructure Topology

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
       Tailscale WireGuard Mesh (100.x.x.x / Direct LAN 192.168.1.x, RTT <5ms)
    ───────────────────────────────────────┬───────────────────────────────────────
                                           │
         ┌─────────────────────────────────┼─────────────────────────────────┐
         ▼                                 ▼                                 ▼
┌─────────────────────────────────┐ ┌─────────────────────────────────┐ ┌─────────────────────────────────┐
│     NODE: PVE1 (Always-On)      │ │     NODE: PVE3 (Always-On)      │ │  NODE: webtop-hermes (Central)  │
│  IP: 100.114.18.91 / 192.168.1.2│ │ IP: 100.115.231.48 / .202       │ │  IP: 100.83.32.92 (VM 800)      │
├─────────────────────────────────┤ ├─────────────────────────────────┤ ├─────────────────────────────────┤
│ • DEDICATED SERVER GPU          │ │ • DEDICATED SERVER              │ │ • Central Gateway (Port 9000)   │
│ • Hosts: Fred & Ned             │ │ • Hosts: George & Kai           │ │ • SQLite WAL Master Authority   │
│ • vLLM Server (192.168.1.230)   │ │ • Playwright Browser Engine     │ │ • Local Unix Domain SwarmLock   │
│ • Capabilities: [server_gpu,    │ │ • Capabilities: [concurrency,   │ │ • Telemetry Event Hub           │
│     orchestration, verification]│ │     ui_mobile, browser_qa]      │ │ • Role: Control Plane Coord.    │
│ • Role: 24/7 Compute Worker     │ │ • Role: 24/7 Concurrency Worker │ │                                 │
└─────────────────────────────────┘ └─────────────────────────────────┘ └─────────────────────────────────┘
                                                   ▲
                                                   │ (Intermittent / Operator-Driven)
                                                   ▼
                                    ┌─────────────────────────────────┐
                                    │    NODE: Lightbringer (Laptop)  │
                                    │   IP: 100.93.104.46 / .58       │
                                    ├─────────────────────────────────┤
                                    │ • Windows 11 Mobile Workstation │
                                    │ • Michael's Interactive Station │
                                    │ • Role: OPERATOR SATELLITE      │
                                    │ • In-Process SwarmLock Fencing  │
                                    │ • Live Telemetry Broadcast      │
                                    │ • Ad-Hoc Local GPU (LiteRT)     │
                                    │ • ZERO Background Dependencies  │
                                    │ • Sleeps Without Impact         │
                                    └─────────────────────────────────┘
```

---

## ⚖️ Cluster Fleet & Satellite Node Specialization Matrix

| Node | Hardware & Uptime Profile | Local / Cloud Engine | Assigned Swarm Roles | Background Queue Dependency |
|---|---|---|---|---|
| **PVE1** (`100.114.18.91`) | Dedicated Proxmox Server, **Always-On Server GPU**, 24/7 Uptime | Dedicated Server GPU + vLLM (`192.168.1.230:8000`) + Cloud Gemini | **Fred** (Lead Orchestrator) & **Ned** (Verification Guard); batch GPU processing; asset destruction | **Critical Primary**: 24/7 background queue consumer |
| **PVE3** (`100.115.231.48`) | Dedicated Proxmox Server, 24/7 Uptime | High-vCPU + Playwright Chromium Engine + Cloud Gemini | **George** (Concurrency Specialist) & **Kai** (UI/Mobile); browser rendering; test barrages | **Critical Primary**: 24/7 background queue consumer |
| **PVE2** (`100.119.225.27`) | Proxmox Server (GPU under diagnosis) | Maintenance / Standby | Reserved for GPU troubleshooting and secondary cluster failover | **Offline / Standby**: Excluded from active queue |
| **`webtop-hermes`** (`100.83.32.92`) | Linux VM 800 (Ubuntu 24.04), 24/7 Uptime | LocalAgentConfig (Gemini API) + Local Gateway Services | Central Gateway, SQLite WAL authority, SwarmLock coordinator, fleet hygiene | **Control Plane**: Manages state, leases, and queues |
| **Lightbringer** (`100.93.104.46`) | **Windows 11 Laptop (Mobile/Intermittent)**; sleeps, travels | `LiteRTAgentConfig(backend='gpu')` (personal) + Cloud Gemini | **Michael's Interactive Station**: interactive coding, manual agent runs, visual UI inspection | **ZERO (Exempt)**: Laptop sleep/travel never stalls the swarm |

---

## 🔒 Invariant & Failure-Mode Fencing

### 1. In-Process SwarmLock Interception Fence (`pre_tool_call_decide`)
- **Invariant**: No Antigravity agent or operator session on any node may execute a file mutation tool (`edit_file`, `create_file`, `write_to_file`, or modifying `run_command`) without acquiring a confirmed SwarmLock lease from the Prismatic Gateway.
- **Operator Protection**: When Michael edits code on Lightbringer, the SDK hook warns him if Fred or Ned is actively mutating that file on PVE1, preventing accidental overwrite.
- **Deflection Latency**: In-process hook resolves in $<2\text{ms}$ (vs 142ms legacy subprocess hook). Returns synthetic `DecideResult.BLOCK("423 Locked")` without crashing the session.

### 2. Live Signal & Telemetry Streaming Fence (`post_tool_call`)
- **Invariant**: Every tool execution and turn completion must emit a structured signal to `/api/gateway/signals/emit` within 50ms.
- **Unified Visibility**: Interactive sessions on Lightbringer and autonomous daemon turns on PVE1/PVE3 stream simultaneously to the Prismatic Hub Signals Console (port 9000/SSE) and Telegram (`8190664947`) via `DynamicTelegramThrottler`.

### 3. Native Operational State Preservation Fence (Directive 05 Alignment)
- **Invariant**: Memory compaction must programmatically preserve the structured operational state header:
  ```markdown
  ### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)
  - **Active Task ID:** {task_id}
  - **Active SwarmLock Leases:** {held_locks}
  - **Git Branch & HEAD Commit:** {git_head}
  - **Modified Working Files:** {modified_files}
  - **Completed Steps:** {completed_steps}
  - **Immediate Next Step:** {next_step}
  ```
- **Fail-Closed Gate**: The post-compaction validator re-anchors the header automatically if the summarizer LLM omits it.

### 4. PVE Cluster Affinity & Server Queue Routing Fence
- **Invariant**: All unattended background swarm tasks MUST be routed exclusively to dedicated 24/7 servers (**PVE1**, **PVE3**, **Hermes**).
- **GPU Workload Routing**: Tasks requiring GPU compute (spritesheet matrices, batch model inference) route strictly to **PVE1**.
- **Concurrency & UI Workload Routing**: Tasks requiring multi-agent concurrency or browser rendering route to **PVE3**.

### 5. Resilient Operator Satellite Client Protocol (Zero Laptop Dependency)
- **Invariant**: The Gateway task queue is strictly forbidden from assigning background queue jobs to Lightbringer.
- **Stateless & Interruptible**: When Lightbringer closes its lid, goes to sleep, or disconnects, **zero background swarm tasks fail or stall**. Any active interactive leases held by Lightbringer expire cleanly via TTL (default 120s) without blocking the fleet.

### 6. Multimodal Visual QA & Layout Compliance Fence
- **Invariant**: Frontend web modifications must be verified using native multimodal vision (`Image.from_file()`) against 375px mobile viewport screenshots before task completion is certified.

---

## 📋 Operator Runbook & Canonical Commands

### 1. Start 24/7 Background Worker on PVE1 (Server GPU Node)
```bash
# Deployed as systemd service: hermes-worker@pve1.service on PVE1
python3 -m prismatic.cli worker \
  --node-id "pve1" \
  --capabilities "server_gpu,orchestration,verification,vllm" \
  --gateway-url "http://100.83.32.92:9000" \
  --poll-interval 1.5
```

### 2. Start 24/7 Background Worker on PVE3 (Concurrency & UI Node)
```bash
# Deployed as systemd service: hermes-worker@pve3.service on PVE3
python3 -m prismatic.cli worker \
  --node-id "pve3" \
  --capabilities "concurrency,ui_mobile,browser_qa" \
  --gateway-url "http://100.83.32.92:9000" \
  --poll-interval 1.5
```

### 3. Launch Interactive Operator Session on Lightbringer (Laptop)
```powershell
# Run from Lightbringer Windows terminal — registers as satellite client
python -m prismatic.cli satellite `
  --node-id "lightbringer-windows" `
  --gateway-url "http://100.83.32.92:9000" `
  --enable-swarmlock `
  --enable-signals
```

### 4. Dispatch Task to Server Fleet with Node Affinity
```bash
# Dispatches GPU task directly to PVE1 server
python3 -m prismatic.cli task dispatch \
  --task-id "GRO-4863" \
  --prompt "Generate 16-bit arcade destruction matrix on server GPU" \
  --node-affinity "pve1" \
  --timeout 300
```

---

## 📊 Verification Receipts & Cross-References

- **Master Rollout Roadmap**: [`docs/plans/antigravity-sdk-dual-instance-rollout-roadmap.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/plans/antigravity-sdk-dual-instance-rollout-roadmap.md)
- **Phase 1 Directive (SDK Harness)**: [`directives/06_PHASE_1_AGY_SDK_HARNESS_CANARY.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/06_PHASE_1_AGY_SDK_HARNESS_CANARY.md)
- **Phase 2 Directive (Hooks & Compaction)**: [`directives/07_PHASE_2_IN_PROCESS_SWARMLOCK_SIGNALS_COMPACTION.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/07_PHASE_2_IN_PROCESS_SWARMLOCK_SIGNALS_COMPACTION.md)
- **Phase 3 Directive (PVE Fleet & Satellite)**: [`directives/08_PHASE_3_DISTRIBUTED_WORKER_DAEMON_GPU_AFFINITY.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/08_PHASE_3_DISTRIBUTED_WORKER_DAEMON_GPU_AFFINITY.md)
- **Phase 4 Directive (Fleet Cutover & QA)**: [`directives/09_PHASE_4_FULL_FLEET_MIGRATION_MULTIMODAL_QA_TMUX_RETIREMENT.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/09_PHASE_4_FULL_FLEET_MIGRATION_MULTIMODAL_QA_TMUX_RETIREMENT.md)
- **Phase 1 In-Process SDK Harness**: [`tests/test_agy_sdk_harness.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_agy_sdk_harness.py) (11/11 PASSED)
- **Phase 1 Canary Execution & Dual-Runtime Switch**: Verified `PRISMATIC_AGY_RUNTIME=cli|sdk` with 100% clean fallback
- **Post-Compression Verification**: [`tests/test_compression_preservation.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_compression_preservation.py) (7/7 PASSED)
- **Telegram Throttling Verification**: [`tests/test_telegram_streaming_throttler.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_telegram_streaming_throttler.py) (19/19 PASSED)
- **SQLite WAL Concurrency**: [`tests/test_sqlite_wal_concurrency.py`](https://prismatic.growthwebdev.com/workspaces?file=tests/test_sqlite_wal_concurrency.py) (6/6 PASSED)
- **Linear Issue Tracking**: [GRO-4861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4861), [GRO-4863](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4863), [GRO-4852](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4852)
