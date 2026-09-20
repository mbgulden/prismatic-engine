# Master Plan: Google Antigravity SDK Cluster Fleet & Satellite Rollout Roadmap

**Status:** Canonical Multi-Phase Rollout Plan
**Owner:** Prismatic Engine Core & Swarm Orchestration Maintainers
**Decision Target:** [`docs/okf-antigravity-sdk-dual-instance-integration.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/okf-antigravity-sdk-dual-instance-integration.md)
**Applicable Nodes:**
- **PVE1** (`100.114.18.91`): Dedicated Server with **Always-On Server GPU** (Fred & Ned)
- **PVE3** (`100.115.231.48`): Dedicated Server (George & Kai)
- **`webtop-hermes`** (`100.83.32.92`): 24/7 Central Gateway, Swarm Hub & SQLite WAL Authority
- **Lightbringer** (`100.93.104.46`): Windows 11 Laptop — **Operator Satellite Client** (Zero Background Queue Dependency)
**Associated Linear Epics:** [GRO-4860](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4860), [GRO-4852](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4852), [GRO-4203](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4203)

---

## 🗺️ Executive Overview & 4-Phase Trajectory

This master roadmap coordinates the operational deployment of the **Google Antigravity SDK** (`google-antigravity-0.1.15`) across Michael Gulden's enterprise Proxmox VE server cluster and development workstations.

### 🏛️ Core Infrastructure Principles
1. **Server-Grade Always-On Fleet**: 24/7 autonomous swarm tasks execute exclusively on dedicated server hardware:
   - **PVE1**: Dedicated enterprise server equipped with an **always-on server GPU**. Executes compute-heavy tasks for **Fred** (Lead Orchestrator) and **Ned** (Verification/Proof).
   - **PVE3**: Dedicated server hosting **George** (Concurrency/Leases) and **Kai** (UI/Mobile/Browser QA).
   - **`webtop-hermes`**: Linux VM 800 hosting the Central Gateway, SQLite WAL store authority, and SwarmLock coordinator.
2. **Resilient Operator Satellite Client**:
   - **Lightbringer** is Michael's personal mobile development laptop.
   - It is integrated via the **Operator Satellite Protocol**: gains interactive in-session SwarmLock protection, live telemetry streaming, and ad-hoc local GPU acceleration for Michael's personal prompt turns.
   - **Zero Background Dependencies**: The Gateway task queue **never** assigns background swarm tasks to Lightbringer. Laptop sleep, travel, or battery depletion causes **zero delays or failures** in the autonomous swarm.

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │            ANTIGRAVITY SDK 4-PHASE SEQUENTIAL ROLLOUT MATRIX            │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
 ┌───────────────────────────────────▼────────────────────────────────────┐
 │ PHASE 1: AGYSDKHarness Core & Dual-Runtime Canary                      │
 │ Plan Directive: directives/06_PHASE_1_AGY_SDK_HARNESS_CANARY.md        │
 │ Linear Epic: [GRO-4861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4861)                                              │
 │ • Deliver prismatic/harnesses/agy_sdk.py with LocalAgentConfig        │
 │ • Implement PRISMATIC_AGY_RUNTIME=cli|sdk dual-runtime canary switch   │
 │ • Exact token telemetry via response.usage_metadata                   │
 │ • OKF Directive: Register Phase 1 harness objective & test receipts   │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
 ┌───────────────────────────────────▼────────────────────────────────────┐
 │ PHASE 2: In-Process SwarmLock, Signals & Compaction Hooks              │
 │ Plan Directive: directives/07_PHASE_2_IN_PROCESS_SWARMLOCK_SIGNALS_COMPACTION.md │
 │ Linear Epic: [GRO-4862](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4862)                                              │
 │ • In-process write fencing via @hooks.pre_tool_call_decide (<2ms)      │
 │ • Real-time typed signals via @hooks.post_tool_call to Gateway/Telegram│
 │ • Directive 05 post-compression state preservation via @hooks.on_compaction│
 │ • OKF Directive: Update Fences 1, 2, 3 in canonical OKF standard      │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
 ┌───────────────────────────────────▼────────────────────────────────────┐
 │ PHASE 3: PVE Cluster Distributed Compute Fleet & Satellite Client      │
 │ Plan Directive: directives/08_PHASE_3_DISTRIBUTED_WORKER_DAEMON_GPU_AFFINITY.md │
 │ Linear Epic: [GRO-4863](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4863)                                              │
 │ • Deliver 24/7 worker daemons on PVE1 (Server GPU) & PVE3 (Concurrency)│
 │ • Gateway task queue routing matching GPU workloads to PVE1            │
 │ • Lightbringer Resilient Satellite Client Protocol (Zero Task Queue)   │
 │ • OKF Directive: Update Fences 4 & 5, node matrix, and PVE runbooks    │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
 ┌───────────────────────────────────▼────────────────────────────────────┐
 │ PHASE 4: Full Fleet Migration, Multimodal QA & Tmux Retirement         │
 │ Plan Directive: directives/09_PHASE_4_FULL_FLEET_MIGRATION_MULTIMODAL_QA_TMUX_RETIREMENT.md │
 │ Linear Epic: [GRO-4864](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4864)                                              │
 │ • Cut over fleet default to PRISMATIC_AGY_RUNTIME=sdk across profiles │
 │ • In-session 375px mobile visual QA via native Image.from_file()       │
 │ • Formal retirement of legacy tmux session and ANSI scraping logic     │
 │ • OKF Directive: Finalize OKF standard, evidence map, and full receipts│
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 📑 Detailed Phased Rollout Matrix

| Phase | Directive Document | Linear Task | Deliverables | OKF Documentation Actions | Verification Gate |
|---|---|---|---|---|---|
| **Phase 1** | [`directives/06_PHASE_1_AGY_SDK_HARNESS_CANARY.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/06_PHASE_1_AGY_SDK_HARNESS_CANARY.md) | [GRO-4861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4861) | `prismatic/harnesses/agy_sdk.py`, `get_agy_harness()`, `PRISMATIC_AGY_RUNTIME` canary flag | Register Phase 1 objective, update test receipts in `docs/okf-antigravity-sdk-dual-instance-integration.md` | `tests/test_agy_sdk_harness.py` ($\ge 8/8$ passed) |
| **Phase 2** | [`directives/07_PHASE_2_IN_PROCESS_SWARMLOCK_SIGNALS_COMPACTION.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/07_PHASE_2_IN_PROCESS_SWARMLOCK_SIGNALS_COMPACTION.md) | [GRO-4862](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4862) | `prismatic/harnesses/agy_sdk_hooks.py`, `pre_tool_call_decide`, `post_tool_call`, `on_compaction` | Update Fences 1, 2, 3 in `docs/okf-antigravity-sdk-dual-instance-integration.md` | `tests/test_agy_sdk_hooks_and_fencing.py` ($\ge 10/10$ passed) |
| **Phase 3** | [`directives/08_PHASE_3_DISTRIBUTED_WORKER_DAEMON_GPU_AFFINITY.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/08_PHASE_3_DISTRIBUTED_WORKER_DAEMON_GPU_AFFINITY.md) | [GRO-4863](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4863) | 24/7 worker daemons on PVE1 & PVE3, Gateway GPU affinity router, Lightbringer satellite protocol | Update Fences 4 & 5, cluster node matrix, and systemd runbooks for PVE1/PVE3 | `tests/test_distributed_sdk_worker.py` ($\ge 8/8$ passed) |
| **Phase 4** | [`directives/09_PHASE_4_FULL_FLEET_MIGRATION_MULTIMODAL_QA_TMUX_RETIREMENT.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/09_PHASE_4_FULL_FLEET_MIGRATION_MULTIMODAL_QA_TMUX_RETIREMENT.md) | [GRO-4864](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4864) | Fleet-wide default cutover (`sdk`), `MultimodalVisualQAGate`, tmux deprecation | Finalize OKF standard, evidence map, and full parity validation | `tests/test_multimodal_visual_qa.py` + full 50+ regression suite |

---

## 🎯 Multi-Agent Governance & Verification Gate

1. **Same-Commit Documentation Requirement**: Every code modification made during Phases 1–4 MUST include the corresponding OKF documentation updates in the exact same git commit.
2. **Fail-Closed Validation Gate**:
   - Every phase completion must pass:
     ```bash
     python3 -m pytest tests/test_okf_docs.py
     python3 scripts/validate_okf_docs.py
     git diff --check
     ```
   - Any broken link, missing test receipt, or hardcoded workstation path triggers an immediate verification rejection.

---

## 📊 Verification Receipts

- **Canonical OKF Standard**: [`docs/okf-antigravity-sdk-dual-instance-integration.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/okf-antigravity-sdk-dual-instance-integration.md)
- **Technical Research Deep-Dive**: [`docs/research/antigravity-sdk-architecture-and-prismatic-synthesis.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/research/antigravity-sdk-architecture-and-prismatic-synthesis.md)
- **Phase 1 Directive**: [`directives/06_PHASE_1_AGY_SDK_HARNESS_CANARY.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/06_PHASE_1_AGY_SDK_HARNESS_CANARY.md)
- **Phase 2 Directive**: [`directives/07_PHASE_2_IN_PROCESS_SWARMLOCK_SIGNALS_COMPACTION.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/07_PHASE_2_IN_PROCESS_SWARMLOCK_SIGNALS_COMPACTION.md)
- **Phase 3 Directive**: [`directives/08_PHASE_3_DISTRIBUTED_WORKER_DAEMON_GPU_AFFINITY.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/08_PHASE_3_DISTRIBUTED_WORKER_DAEMON_GPU_AFFINITY.md)
- **Phase 4 Directive**: [`directives/09_PHASE_4_FULL_FLEET_MIGRATION_MULTIMODAL_QA_TMUX_RETIREMENT.md`](https://prismatic.growthwebdev.com/workspaces?file=directives/09_PHASE_4_FULL_FLEET_MIGRATION_MULTIMODAL_QA_TMUX_RETIREMENT.md)
- **Pre-Requisite Baseline**: Commit `3429b787` (Directive 05, 47/47 regression suite green)
