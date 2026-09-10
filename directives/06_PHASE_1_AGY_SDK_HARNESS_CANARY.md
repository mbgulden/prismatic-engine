# Directive 06: Antigravity SDK Harness Core Implementation & Dual-Runtime Canary (Phase 1)

**Status:** Approved Architecture Directive & Operational Standard
**Owner:** Prismatic Engine Core & Swarm Orchestration Maintainers
**Target Linear Issue:** [GRO-4861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4861)
**Applicable Nodes:** PVE Cluster (PVE1 Server GPU for Fred & Ned, PVE3 for George & Kai), `webtop-hermes` (Central Gateway/Hub), Lightbringer (Operator Satellite Client)
**Prerequisite Commits:** `3429b787` (Directive 05), `ea961987` (Directive 03), `d7f7b649` (Directive 04)
**System of Record:** `prismatic/harnesses/agy_sdk.py` + `tests/test_agy_sdk_harness.py` + OKF Standard

---

## 🎯 Executive Summary & Mission Objective

Phase 1 establishes the native Python **Antigravity SDK Harness** ([`prismatic/harnesses/agy_sdk.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/harnesses/agy_sdk.py)), enabling Prismatic Engine to orchestrate Google Antigravity 2.0 instances in-process without relying on `tmux` terminal wrappers, ANSI log scraping, or Bubbletea PTY terminal emulation.

To ensure zero risk to live production swarms, Phase 1 delivers a **dual-runtime canary architecture**:
- `PRISMATIC_AGY_RUNTIME=cli` (default backwards-compatible fallback using `AGYCLIHarness`)
- `PRISMATIC_AGY_RUNTIME=sdk` (canary activation using `AGYSDKHarness`)

Every AI agent working on this task must follow the exact specifications, method signatures, test requirements, and OKF update directives outlined in this document.

---

## 🏗️ Technical Architecture & Component Specifications

```text
                               ┌────────────────────────────────┐
                               │  Fleet Manager / Task Router   │
                               └───────────────┬────────────────┘
                                               │
                                 get_agy_harness(config)
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       │                                               │
                       ▼                                               ▼
         [PRISMATIC_AGY_RUNTIME=cli]                     [PRISMATIC_AGY_RUNTIME=sdk]
        ┌───────────────────────────────┐               ┌───────────────────────────────┐
        │        AGYCLIHarness          │               │        AGYSDKHarness          │
        │ • Spawns tmux subprocess      │               │ • In-process Python coroutine │
        │ • Scrapes ANSI log files      │               │ • Direct async Agent() lifecycle│
        │ • External bash hooks         │               │ • Exact response.usage_metadata│
        └───────────────────────────────┘               └───────────────────────────────┘
```

### 1. `AGYSDKHarness` Implementation Contract
File: [`prismatic/harnesses/agy_sdk.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/harnesses/agy_sdk.py)
Inherits: `AgentHarness` from [`prismatic/harnesses/base.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/harnesses/base.py)

**Required Class Methods**:
```python
class AGYSDKHarness(AgentHarness):
    def __init__(
        self,
        node_id: str = "webtop-hermes",
        model: str = "gemini-3.7-flash",
        gateway_url: str = "http://127.0.0.1:9000",
        local_vllm_url: str | None = "http://192.168.1.230:8000/v1",
        api_key_env: str = "GEMINI_API_KEY",
    ) -> None:
        ...

    async def start_session(
        self,
        session_id: str,
        profile: str,
        task_id: str,
        system_instruction: str | None = None,
    ) -> "SDKSession":
        ...

    async def execute_turn(
        self,
        session_id: str,
        prompt: str,
        attachments: list[any] | None = None,
        timeout_seconds: float = 300.0,
    ) -> HarnessTurnResult:
        ...

    async def close_session(self, session_id: str) -> None:
        ...
```

### 2. Configuration Resolution Engine
- When `model` starts with `gemini-`, configure `LocalAgentConfig` with `GeminiAPIEndpoint` and system instructions.
- When `model` starts with `local-` or `vllm:`, configure `LocalOpenAIAgentConfig` pointing to `http://192.168.1.230:8000/v1` with token discovery.
- When running on `lightbringer-windows` with `use_gpu=True`, support `LiteRTAgentConfig(backend='gpu')` for local Gemma inference.

### 3. Usage & Token Metadata Extraction
Unlike the legacy CLI harness which estimates token usage by parsing log files, `AGYSDKHarness` extracts exact usage metadata directly from `response.usage_metadata`:
- `prompt_tokens = response.usage_metadata.prompt_token_count`
- `completion_tokens = response.usage_metadata.candidates_token_count`
- `thinking_tokens = getattr(response.usage_metadata, "thinking_token_count", 0)`
- `total_tokens = response.usage_metadata.total_token_count`

### 4. Harness Factory & Dual-Runtime Dispatcher
File: [`prismatic/harnesses/__init__.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/harnesses/__init__.py)

```python
def get_agy_harness(runtime_preference: str | None = None) -> AgentHarness:
    runtime = runtime_preference or os.environ.get("PRISMATIC_AGY_RUNTIME", "cli").lower()
    if runtime == "sdk":
        from prismatic.harnesses.agy_sdk import AGYSDKHarness
        return AGYSDKHarness()
    from prismatic.harnesses.agy_cli import AGYCLIHarness
    return AGYCLIHarness()
```

---

## 📋 Mandatory OKF Directives for Phase 1

To keep all AI agents aligned, the executing agent MUST perform the following OKF documentation updates in the same commit as code changes:

### OKF Directive 1.1: Register Phase 1 Objective in Canonical OKF Standard
File: [`docs/okf-antigravity-sdk-dual-instance-integration.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/okf-antigravity-sdk-dual-instance-integration.md)
- Verify that Objective 1 (`AGYSDKHarness Core Implementation`) maps to:
  - **Key Result**: Dual-runtime canary successfully routes turns through `AGYSDKHarness` with zero PTY/tmux dependency when `PRISMATIC_AGY_RUNTIME=sdk`.
  - **Function**: `prismatic/harnesses/agy_sdk.py::AGYSDKHarness.execute_turn`
  - **Evidence**: `tests/test_agy_sdk_harness.py`
  - **System of Record**: SDK usage metadata + Gateway session store

### OKF Directive 1.2: Update Verification Receipts
- Record test execution results and exact pass counts in `docs/okf-antigravity-sdk-dual-instance-integration.md` under Section `📊 Verification Receipts & Cross-References`:
  - `tests/test_agy_sdk_harness.py` (Must achieve $\ge 8/8$ PASSED).
  - SHA-256 digest of `prismatic/harnesses/agy_sdk.py`.

### OKF Directive 1.3: Update Machine Parity and Run Validation
- Run `python3 -m pytest tests/test_okf_docs.py`
- Run `python3 scripts/validate_okf_docs.py` to guarantee zero OKF schema violations or broken links.

---

## 🔒 Invariant & Failure-Mode Fencing

1. **Fail-Closed Fallback Invariant**: If `google.antigravity` fails to import or initialize, `get_agy_harness()` MUST log a warning and fall back cleanly to `AGYCLIHarness` without crashing fleet daemons.
2. **Headless Execution Invariant**: `AGYSDKHarness` must never spawn interactive TTY prompts or require terminal attachment. Non-TTY invocations in cron or CI must succeed 100% of the time.
3. **Usage Accounting Invariant**: Every completed turn must return non-zero `prompt_tokens` and `total_tokens` matching upstream Google API responses to preserve Directive 01 context window hygiene.

---

## 🧪 Acceptance Criteria & Test Plan

1. **Unit Tests (`tests/test_agy_sdk_harness.py`)**:
   - Test harness instantiation with default and custom configs.
   - Test `LocalAgentConfig` resolution for `gemini-3.7-flash` and `gemini-3.7-pro`.
   - Test `LocalOpenAIAgentConfig` resolution for `local-qwen-27b-q8-fred`.
   - Test factory function `get_agy_harness()` honoring `PRISMATIC_AGY_RUNTIME`.
   - Test mock turn execution returning typed `HarnessTurnResult`.
   - Test extraction of `response.usage_metadata`.
   - Test graceful exception handling on upstream API timeout.
   - Test session cleanup on `close_session()`.
2. **Regression Gate**: Full 47/47 regression suite (Directives 01-05) must remain green.

---

## 🤖 Peer-Agent Handoff Instructions

- **For Fred (Lead Orchestrator)**: When dispatching canary tasks, set `env={"PRISMATIC_AGY_RUNTIME": "sdk"}` to validate in-process execution on non-critical tickets.
- **For George (Concurrency Specialist)**: Validate that concurrent SDK harness sessions run in isolated coroutine contexts without cross-session memory pollution.
- **For Ned (Verification Guard)**: Verify that `tests/test_agy_sdk_harness.py` passes with 100% assertion coverage before approving PR handoff.
