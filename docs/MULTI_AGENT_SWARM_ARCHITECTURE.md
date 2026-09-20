# Prismatic Multi-Agent Swarm Architecture Specification

> **Document Status**: Active Working Draft<br>
> **Target File**: `docs/MULTI_AGENT_SWARM_ARCHITECTURE.md`<br>
> **Governance**: SwarmLock Protocol Exclusive Mutation Matrix

---

## Executive Summary & System Model
This specification defines the dual-agent autonomous orchestration and verification architecture within Prismatic Engine, establishing empirical guarantees for multi-agent co-authoring without race conditions or lost updates.

---

## Section 1: Orchestration Engine & Topological Wave Dispatch (Authored by Fred)

### 1.1 Architectural Role & Daemon Topology
The Orchestrator agent (`fred`) operates as the continuous coordination apex within the Prismatic Multi-Agent Swarm. Running under `hermes-orchestrator-gateway.service`, Fred evaluates inbound work orders from Linear ([GRO-3319](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3319)), user instructions over Telegram, and system events routed through the Prismatic Gateway IPC bridge.

### 1.2 Wave Dispatch & Topological Partitioning
When complex tasks require modifications across shared resources, Fred decomposes the dependency graph into **Topological Execution Waves**:
1. **Wave $\alpha$ (Dependency Analysis)**: Partitioning code modules, identifying blast radiuses, and calculating shared surface intersections.
2. **Wave $\beta$ (Sequential Acquisition)**: Obtaining exclusive distributed leases via `POST /api/gateway/swarmlock/acquire` before any disk mutation begins.
3. **Wave $\gamma$ (Staged Handoff)**: Emitting milestone telemetry signals to `/api/gateway/signals/emit` and explicitly releasing leases so downstream agents (such as `george`) can acquire exclusive locks without contention.

### 1.3 Telegram Token Streaming & Dual Telemetry Pipeline
Fred bridges conversational messaging and system observability through a unified dual-emission pipeline:
- **Telegram Channel**: Leverages progressive Telegram message updates (`tool_progress: all`, `thinking_progress: true`, `busy_ack_detail: true`) to stream tokens and tool actions directly to mobile operators.
- **Prismatic Gateway Signals Feed**: Integrates the `prismatic_telemetry` plugin hooks (`pre_tool_call`, `post_tool_call`, `pre_llm_call`) to asynchronously publish structured telemetry over Server-Sent Events (`/api/signals/stream`) and WebSocket (`/ws/events`), populating the Prismatic Hub Signals console in real time.

---

## Section 2: Verification Proof Engine & Clean-Room Attestation (Authored by George)

### 2.1 Architectural Role & Independent Peer Auditor
The Verifier agent (`george`) provides fail-closed attestation and proof generation across the Prismatic Multi-Agent Swarm. Operating under `hermes-gateway-george.service`, George never assumes prior agent claims are factual without empirical handle checks (`Get-FileHash`, `view_file`, and deterministic SHA-256 digests).

### 2.2 Pre-Acquisition Deflection & Mutex Invariant
During the initial co-authoring attempt on `docs/MULTI_AGENT_SWARM_ARCHITECTURE.md`, George's lease acquisition attempt was deflected by the SwarmLock kernel (`ok: false, status: deflected, holder: fred`). George enforced the Swarm Deflection Protocol:
1. **Deflection Backoff**: Captured collision metadata without altering target filesystem state.
2. **Telemetry Registration**: Gateway automatically emitted `collision_deflected` and `lock_deflected` signals with severity `lease` / `warning`.
3. **Topological Wait**: Polled the lock registry until Fred released lease `128658bb-37d2-467d-a0f3-dede19acaa6e`.
4. **Post-Release Claim**: Acquired atomic lease `96c8c817-b60c-4c15-bab9-da8cf0bf41aa` with zero contention.

### 2.3 Verification Proof Packet & Clean-Room Handoff
George validates all workspace modifications against the **6 Anti-Deception Invariants**:
- **Invariant 1 (Observable Execution Proof)**: Pytest execution with exact exit code 0 (`tests/test_multi_agent_concurrency_barrage.py`).
- **Invariant 2 (Exact-Head Tree Proof)**: Immutable commit SHA (`git rev-parse HEAD`) and tree SHA binding.
- **Invariant 3 (Deterministic Log Digest)**: DLD hashing over execution stdout/stderr streams to ensure non-forgeable receipts.
- **Invariant 4 (Playwright Visual Handoff)**: Full-fidelity browser audits across 1440px desktop and 375px mobile viewports, rendering directly in active brain artifact storage.

---

## Section 3: Dual-Agent Swarm Concurrency Matrix & Consensus Ledger
<!-- [COLLABORATIVE CONSENSUS MATRIX] -->
| Metric / Pillar | Fred (Orchestrator) | George (Verifier) | Swarm Consensus |
| :--- | :--- | :--- | :--- |
| **Primary Domain** | Distributed Work Dispatch & Telegram Steering | Clean-Room Attestation & Evidence Proofs | Unified Topological Execution |
| **Concurrency Guard** | SwarmLock Pre-Execution Lease | SwarmProof Post-Execution Verification | Zero Clobbering / Zero Race Conditions |
| **Telemetry Transport** | Server-Sent Events (SSE) & WebSocket `/ws/events` | Deterministic Log Digest (DLD) Receipts | Real-Time Live Hub Observability |
| **Failure Recovery** | Exponential Backoff on Deflection | Stale Lease Operator Eviction | Self-Healing Autonomous Mesh |
