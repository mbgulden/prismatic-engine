# Directive 07: In-Process SwarmLock Fencing, Real-Time Signals & Directive 05 Compaction Hook (Phase 2)

**Status:** Approved Architecture Directive & Operational Standard
**Owner:** Prismatic Engine Core & Swarm Concurrency Maintainers
**Target Linear Issue:** [GRO-4862](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4862)
**Applicable Nodes:** PVE Cluster (PVE1 Server GPU for Fred & Ned, PVE3 for George & Kai), `webtop-hermes` (Central Gateway/Hub), Lightbringer (Operator Satellite Client)
**Prerequisite Commits:** Phase 1 (`[GRO-4861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4861)`), `3429b787` (Directive 05), `d7f7b649` (Directive 04)
**System of Record:** `prismatic/harnesses/agy_sdk_hooks.py` + `tests/test_agy_sdk_hooks_and_fencing.py` + OKF Standard

---

## 🎯 Executive Summary & Mission Objective

Phase 2 embeds Prismatic Engine's core concurrency, telemetry, and memory protection invariants directly into the in-process execution lifecycle of Google Antigravity SDK agents.

Previously, write fencing and signal streaming relied on external subprocess hooks (`~/.antigravity/hooks/prismatic_hook.py`) that added ~142ms of latency per tool call, suffered from shell environment leaks, and lacked native compaction interception.

Phase 2 replaces external shell hooks with **native Antigravity SDK lifecycle hooks**:
1. **In-Process SwarmLock Write Interception (`@hooks.pre_tool_call_decide`)**: Deflects write collisions in <2ms with synthetic `DecideResult.BLOCK("423 Locked")` and ensures zero dangling leases.
2. **Real-Time Hypervisor Signal Streaming (`@hooks.post_tool_call`)**: Emits typed signals to `/api/gateway/signals/emit` and Telegram (`8190664947`) via `DynamicTelegramThrottler`.
3. **Native Operational State Preservation (`@hooks.on_compaction`)**: Guarantees Directive 05 `### 📌 CRITICAL OPERATIONAL STATE` persistence across context compactions.

---

## 🏗️ Technical Architecture & In-Process Lifecycle

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                    Antigravity SDK Agent Event Loop                    │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                             Tool Call Triggered
                                     │
                                     ▼
         ┌───────────────────────────────────────────────────────┐
         │     @hooks.pre_tool_call_decide                       │
         │  1. Check tool: edit_file, create_file, run_command   │
         │  2. Acquire lease via SwarmLockClient.acquire()       │
         │  3. If locked -> Return DecideResult.BLOCK(423)       │
         │  4. If acquired -> Return DecideResult.ALLOW          │
         └───────────────────────────┬───────────────────────────┘
                                     │
                                     ▼
         ┌───────────────────────────────────────────────────────┐
         │                 Tool Executes In-Process              │
         └───────────────────────────┬───────────────────────────┘
                                     │
                                     ▼
         ┌───────────────────────────────────────────────────────┐
         │     @hooks.post_tool_call / @hooks.on_tool_error      │
         │  1. Emit PrismaticSignal to /api/gateway/signals/emit │
         │  2. Forward to DynamicTelegramThrottler               │
         │  3. finally: Release lease via SwarmLockClient        │
         └───────────────────────────────────────────────────────┘
```

### 1. `PrismaticSDKHooks` Implementation Specification
File: [`prismatic/harnesses/agy_sdk_hooks.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/harnesses/agy_sdk_hooks.py)

**Required Interface**:
```python
from google.antigravity import hooks, ToolContext, DecideResult
from prismatic.client.swarmlock import SwarmLockClient

class PrismaticSDKHooks:
    def __init__(
        self,
        task_id: str,
        agent_id: str,
        gateway_url: str = "http://127.0.0.1:9000",
        lease_seconds: int = 120,
    ) -> None:
        self.task_id = task_id
        self.agent_id = agent_id
        self.client = SwarmLockClient(gateway_url)
        self.held_locks: set[str] = set()

    @hooks.pre_tool_call_decide
    async def pre_tool_call_decide(
        self, tool_name: str, args: dict, context: ToolContext
    ) -> DecideResult:
        ...

    @hooks.post_tool_call
    async def post_tool_call(
        self, tool_name: str, args: dict, result: any, context: ToolContext
    ) -> None:
        ...

    @hooks.on_tool_error
    async def on_tool_error(
        self, tool_name: str, error: Exception, context: ToolContext
    ) -> None:
        ...

    @hooks.on_compaction
    async def on_compaction(
        self, uncompacted_history: list[dict], context: ToolContext
    ) -> dict:
        ...
```

### 2. File Mutation Fencing Rules
The `pre_tool_call_decide` hook must inspect:
- `edit_file`, `replace_file_content`: Target argument `TargetFile` or `path`.
- `create_file`, `write_to_file`: Target argument `TargetFile` or `path`.
- `run_command`: Inspect `CommandLine` string. If command contains write keywords (`git commit`, `git push`, `rm `, `cp `, `mv `, `sed -i`, `python3 setup.py`), resolve target as `"git:workspace_mutation"`.
- If the resource is already held by another agent, deflect immediately:
  ```python
  return DecideResult.BLOCK(
      f"Tool blocked: '{resource}' is currently locked by agent '{holder}' "
      f"for task '{task_id}'. TTL remaining: {remaining_ttl}s. Please back off."
  )
  ```

### 3. Directive 05 Compaction State Preservation
The `@hooks.on_compaction` hook intercepts conversation history compaction:
1. Calls `git rev-parse --short HEAD` and `git status --porcelain`.
2. Gathers all locks currently recorded in `self.held_locks`.
3. Formats the operational state anchor using [`prismatic/fleet/manager.py::format_operational_state_anchor`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/fleet/manager.py):
   ```markdown
   ### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)
   - **Active Task ID:** {task_id}
   - **Active SwarmLock Leases:** {held_locks}
   - **Git Branch & HEAD Commit:** {git_head}
   - **Modified Working Files:** {modified_files}
   - **Completed Steps:** {completed_steps}
   - **Immediate Next Step:** {next_step}
   ```
4. Registers `verify_and_anchor_compressed_summary` as a fail-closed post-compaction validator. If the LLM summary omitted the anchor block, the validator programmatically prepends it before saving to the session.

---

## 📋 Mandatory OKF Directives for Phase 2

To keep all AI agents aligned, the executing agent MUST perform the following OKF documentation updates:

### OKF Directive 2.1: Update Fences in Canonical OKF Standard
File: [`docs/okf-antigravity-sdk-dual-instance-integration.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/okf-antigravity-sdk-dual-instance-integration.md)
- Update **Fence 1** (In-Process SwarmLock Interception) with verified latency measurements (<2ms) and deflection behavior.
- Update **Fence 2** (Live Signal & Telemetry Streaming) with Gateway `/api/gateway/signals/emit` and Telegram throttler integration.
- Update **Fence 3** (Native Operational State Preservation) citing Directive 05 compliance.

### OKF Directive 2.2: Register Verification Receipts
- Record test execution results and exact pass counts under Section `📊 Verification Receipts & Cross-References`:
  - `tests/test_agy_sdk_hooks_and_fencing.py` ($\ge 10/10$ PASSED).
  - SHA-256 digest of `prismatic/harnesses/agy_sdk_hooks.py`.

### OKF Directive 2.3: Validate Documentation Parity
- Run `python3 -m pytest tests/test_okf_docs.py`
- Run `python3 scripts/validate_okf_docs.py` to confirm clean validation without hardcoded workstation paths.

---

## 🔒 Invariant & Failure-Mode Fencing

1. **Zero-Dangling-Lease Invariant**: Every lease acquired in `pre_tool_call_decide` MUST be released in a `finally:` block within `post_tool_call` or `on_tool_error`. Unhandled agent exceptions must never orphan a lease.
2. **Deterministic Deflection Invariant**: When a resource is locked, the agent must receive an informative `DecideResult.BLOCK` message rather than an unhandled Python exception.
3. **Compaction Anchor Loss Invariant**: If an LLM summarizer strips the `CRITICAL OPERATIONAL STATE` block, the post-compaction validator MUST fail closed and re-prepend the anchor automatically.

---

## 🧪 Acceptance Criteria & Test Plan

1. **Unit & Integration Tests (`tests/test_agy_sdk_hooks_and_fencing.py`)**:
   - Test `pre_tool_call_decide` acquiring lease for `edit_file` and allowing execution.
   - Test `pre_tool_call_decide` deflecting with `DecideResult.BLOCK` when resource is locked.
   - Test `post_tool_call` successfully emitting telemetry and releasing lease.
   - Test `on_tool_error` releasing lease during tool crashes.
   - Test `on_compaction` injecting `CRITICAL OPERATIONAL STATE` anchor.
   - Test post-compaction validator prepending missing anchor if model drops it.
   - Test multi-tool sequential lease acquisition and cleanup.
2. **Performance Benchmark Test**:
   - Assert in-process hook overhead is $<5\text{ms}$ per tool invocation.
3. **Regression Gate**: Directives 01-05 tests (47/47) + Phase 1 tests ($\ge 8/8$) must remain green.

---

## 🤖 Peer-Agent Handoff Instructions

- **For George (Concurrency Specialist)**: Run concurrency barrage tests verifying that two SDK agents attempting to write to the same file are deflected cleanly without file clobbering.
- **For Kai (UI/Mobile Specialist)**: Monitor the Prismatic Signals Console to verify that in-process tool execution events render in real time.
- **For Ned (Verification Guard)**: Verify that zero leases remain in `/api/gateway/swarmlock/status` following test runs.
