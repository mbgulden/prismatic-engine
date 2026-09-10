# Directive 08: PVE Cluster Distributed Compute Fleet, GPU Affinity & Resilient Satellite Client Protocol (Phase 3)

**Status:** Approved Architecture Directive & Operational Standard
**Owner:** Prismatic Engine Core & Distributed Systems Maintainers
**Target Linear Issue:** [GRO-4863](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4863)
**Applicable Nodes:**
- **PVE1** (`100.114.18.91` / `192.168.1.2`): Dedicated 24/7 Proxmox Server with **Always-On Server GPU** (Fred & Ned)
- **PVE3** (`100.115.231.48` / `192.168.1.202`): Dedicated 24/7 Proxmox Server (George & Kai)
- **`webtop-hermes`** (`100.83.32.92`): 24/7 Central Gateway, Swarm Hub & SQLite WAL Authority
- **Lightbringer** (`100.93.104.46` / `192.168.1.58`): Windows 11 Laptop — **Interactive Operator Satellite** (Intermittent/Mobile)
**Prerequisite Commits:** Phase 1 ([GRO-4861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4861)), Phase 2 ([GRO-4862](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4862))
**System of Record:** `prismatic/cli/worker.py` + Gateway Task Queue Ledger + Tailscale WireGuard Mesh + OKF Standard

---

## 🎯 Executive Summary & Mission Objective

Phase 3 establishes an enterprise-grade, 24/7 distributed compute fleet across Michael Gulden's **Proxmox VE (PVE) server cluster**, while integrating **Lightbringer** as a **resilient, interruptible operator satellite client**.

### ⚠️ Critical Architecture Realignment
- **PVE1 is the Always-On Server GPU Node**: Dedicated 24/7 enterprise server hosting **Fred** (Orchestrator/Lead) and **Ned** (Verification/Proof). All heavy GPU inference, batch processing, and asset generation jobs execute here.
- **PVE3 is the Dedicated Compute Node**: 24/7 enterprise server hosting **George** (Concurrency/Leases) and **Kai** (UI/Mobile/Browser QA).
- **PVE2 is on Standby**: Currently undergoing GPU diagnostics; isolated from active queue scheduling.
- **`webtop-hermes` is the Control Plane**: Hosts the Central Gateway, SQLite WAL database authority, and SwarmLock coordinator.
- **Lightbringer is a Mobile Laptop (NOT a Background Worker)**: Lightbringer is Michael's mobile development laptop. It goes to sleep, closes its lid, runs on battery, and travels. **Under no circumstances does the background swarm task queue depend on Lightbringer being awake**.
- **Lightbringer Operator Satellite Protocol**: The Antigravity SDK on Lightbringer provides interactive SwarmLock fencing (preventing accidental file collisions with Fred/Ned), live telemetry streaming to the Hub, and ad-hoc local GPU acceleration *exclusively* for Michael's personal prompt turns while at his desk. When the laptop sleeps, zero swarm tasks drop or stall.

---

## 🏗️ Technical Architecture & Cluster Topology

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │           Prismatic Gateway Central Task Queue (Port 9000)             │
 │  • /api/gateway/tasks/dispatch  (Linear Issues & Swarm Tasks)          │
 │  • /api/gateway/tasks/claim     (PVE Cluster Capability Matcher)       │
 │  • /api/gateway/tasks/heartbeat (Server Worker Lease Renewal)          │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
           Tailscale WireGuard Mesh (100.x.x.x / Direct LAN 192.168.1.x)
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
┌─────────────────────────┐ ┌─────────────────────────┐ ┌─────────────────────────┐
│       NODE: PVE1        │ │       NODE: PVE3        │ │  NODE: webtop-hermes    │
│  (100.114.18.91 / .2)   │ │ (100.115.231.48 / .202) │ │    (100.83.32.92)       │
├─────────────────────────┤ ├─────────────────────────┤ ├─────────────────────────┤
│ • ALWAYS-ON SERVER GPU  │ │ • ALWAYS-ON SERVER      │ │ • Central Gateway (9000)│
│ • Hosts: Fred & Ned     │ │ • Hosts: George & Kai   │ │ • SQLite WAL Authority  │
│ • Capabilities: [gpu,   │ │ • Capabilities:         │ │ • SwarmLock Mutex Host  │
│     orchestration,      │ │     [concurrency,       │ │ • Role: Control Plane   │
│     verification, vllm] │ │      ui_mobile, browser]│ │                         │
│ • Role: Heavy GPU Worker│ │ • Role: Concurrency Wkr │ │                         │
└─────────────────────────┘ └─────────────────────────┘ └─────────────────────────┘
                                     ▲
                                     │ Intermittent / Stateless (Tailscale)
                                     ▼
                        ┌─────────────────────────┐
                        │   NODE: Lightbringer    │
                        │  (100.93.104.46 / .58)  │
                        ├─────────────────────────┤
                        │ • Windows 11 Laptop     │
                        │ • Michael's Workstation │
                        │ • Role: SATELLITE CLIENT│
                        │ • SwarmLock Protected   │
                        │ • Zero Background Queue │
                        │ • Sleeps without Impact │
                        └─────────────────────────┘
```

---

## ⚙️ Component Specifications

### 1. PVE Server Worker Daemon (`prismatic worker`)
File: [`prismatic/cli/worker.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/cli/worker.py)

Deploys as persistent `systemd` services on **PVE1** and **PVE3**:
- **On PVE1** (`hermes-worker@pve1.service`):
  ```bash
  python3 -m prismatic.cli worker \
    --node-id "pve1" \
    --capabilities "server_gpu,orchestration,verification,vllm" \
    --gateway-url "http://100.83.32.92:9000" \
    --poll-interval 1.5
  ```
- **On PVE3** (`hermes-worker@pve3.service`):
  ```bash
  python3 -m prismatic.cli worker \
    --node-id "pve3" \
    --capabilities "concurrency,ui_mobile,browser_qa" \
    --gateway-url "http://100.83.32.92:9000" \
    --poll-interval 1.5
  ```

### 2. Node Affinity & Task Scheduling Engine
File: [`prismatic/gateway/tasks.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/gateway/tasks.py)

**Routing Rules**:
- Tasks tagged with `requires_gpu: true`, `node_affinity: "pve1"`, or verification proof generation strictly route to **PVE1** (Fred & Ned).
- Tasks tagged with `requires_browser: true`, `node_affinity: "pve3"`, or concurrency barrages route to **PVE3** (George & Kai).
- Core database migrations and worktree governance route to `webtop-hermes`.
- **Lightbringer Exclusion Guarantee**: The task dispatcher explicitly blocks background queue tasks from being assigned to `node_id: "lightbringer-windows"`.

### 3. Lightbringer Resilient Satellite Client Protocol
File: [`prismatic/client/satellite.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/client/satellite.py)

When Michael opens Antigravity 2.0 on Lightbringer:
1. **Interactive Session Registration**: Registers with Hermes Gateway as `role: "operator_satellite"`.
2. **In-Process SwarmLock Fencing**: Intercepts file edits via `@hooks.pre_tool_call_decide`. Warns Michael if a file is held by Fred or Ned on PVE1, preventing accidental overwrite.
3. **Live Telemetry Emission**: Pushes interactive prompt/tool events to `/api/gateway/signals/emit` for real-time visibility on the Hub Signals Console and Telegram.
4. **Ad-Hoc Local GPU Inference**: Uses `LiteRTAgentConfig(backend='gpu')` strictly for Michael's personal interactive turns to provide instant responses at 0 cloud cost.
5. **Safe Sleep & Disconnect**: When the laptop goes to sleep or disconnects, any active interactive lock is automatically released upon TTL expiration (default 120s), and zero cluster jobs are delayed.

---

## 📋 Mandatory OKF Directives for Phase 3

To keep all AI agents aligned, the executing agent MUST perform the following OKF documentation updates:

### OKF Directive 3.1: Update Node Specialization Matrix & Fences
File: [`docs/okf-antigravity-sdk-dual-instance-integration.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/okf-antigravity-sdk-dual-instance-integration.md)
- Update **Section ⚖️ Dual-Instance Node Specialization Matrix** to reflect the 4-node topology: PVE1 (Always-On GPU), PVE3 (Concurrency/UI), Hermes (Gateway), and Lightbringer (Operator Satellite).
- Update **Fence 4** (Node Affinity & Server Cluster Routing) to route GPU tasks strictly to PVE1.
- Update **Fence 5** (Resilient Satellite Client Protocol) guaranteeing zero background dependency on Lightbringer.

### OKF Directive 3.2: Update Operator Runbook
- Update **Section 📋 Operator Runbook** with canonical systemd service commands for PVE1 and PVE3, and satellite configuration for Lightbringer.

### OKF Directive 3.3: Record Verification Receipts
- Record test execution results under Section `📊 Verification Receipts`:
  - `tests/test_distributed_sdk_worker.py` ($\ge 8/8$ PASSED).
  - SHA-256 digest of updated worker and task routing modules.

### OKF Directive 3.4: Validate Documentation Parity
- Run `python3 -m pytest tests/test_okf_docs.py`
- Run `python3 scripts/validate_okf_docs.py`

---

## 🔒 Invariant & Failure-Mode Fencing

1. **Zero-Laptop-Dependency Invariant**: The Gateway task queue must never assign unattended background swarm tasks to Lightbringer. If Lightbringer is offline, cluster throughput remains 100%.
2. **Server Heartbeat Fail-Closed Invariant**: PVE1 and PVE3 workers emit heartbeats every 15s. If a worker misses 3 heartbeats (45s), tasks are automatically re-queued to healthy cluster nodes.
3. **Interactive Deflection Notice Invariant**: When an operator on Lightbringer attempts to edit a file locked by Fred or Ned on PVE1, the SDK hook must return a human-readable notice with lock holder, task ID, and remaining TTL.

---

## 🧪 Acceptance Criteria & Test Plan

1. **Unit & Integration Tests (`tests/test_distributed_sdk_worker.py`)**:
   - Test worker registration for `pve1` (GPU) and `pve3` (Concurrency/UI).
   - Test Gateway task routing directing GPU tasks to `pve1` and rejecting assignment to satellite nodes.
   - Test satellite registration as `operator_satellite` with zero background queue polling.
   - Test heartbeat renewal and crash failover on PVE nodes.
   - Test clean lease release upon satellite disconnect.
2. **Regression Gate**: Full 47/47 regression suite + Phase 1 & 2 tests must remain green.

---

## 🤖 Peer-Agent Handoff Instructions

- **For Fred (Lead Orchestrator on PVE1)**: Monitor task queue dispatches and execute GPU-accelerated workloads on PVE1.
- **For George (Concurrency Specialist on PVE3)**: Validate that concurrent tasks on PVE3 coordinate leases seamlessly with PVE1 via Hermes.
- **For Ned (Verification Guard on PVE1)**: Verify that test suites and proof receipts run against server-grade hardware, with zero dependency on client laptop state.
