# Directive 06 (revised 2026-09-27): Harness-Neutral Execution Canary (Phase 1)

**Status:** Revised Architecture Directive & Operational Standard — **supersedes the
2026-09-0X AGY-SDK version below the fold.**
**Revision reason:** Michael cancelled Google Antigravity on 2026-09-23. The
`agy_sdk.py` path is dormant; verification closes over Claude Code CLI, Gemini CLI,
Codex CLI, and Hermes agents. Any canary that assumes the paid Antigravity SDK is
void. The canary's *intent* (prove a harness path before fleet rollout) is unchanged;
only the harness assumption changes.
**Owner:** Prismatic Engine Core & Swarm Orchestration Maintainers
**Target Linear Issue:** [GRO-4861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4861) (historical; AGY-SDK scope closed)
**Applicable Nodes:** PVE Cluster (PVE1 Server GPU for Fred & Ned, PVE3 for George & Kai), `webtop-hermes` (Central Gateway/Hub), Lightbringer (Operator Satellite Client)
**System of Record:** `prismatic/harnesses/base.py` (`AgentHarness` contract) + `prismatic/harnesses/README.md`

> **Original AGY-SDK version (2026-09-0X) — retained for the record, DO NOT EXECUTE:**
> Phase 1 established a native Python Antigravity SDK harness
> (`prismatic/harnesses/agy_sdk.py`) with a dual-runtime canary
> (`PRISMATIC_AGY_RUNTIME=cli` default via `AGYCLIHarness`,
> `PRISMATIC_AGY_RUNTIME=sdk` canary via `AGYSDKHarness`). The AGY SDK was the
> canary subject: in-process coroutines, `response.usage_metadata` accounting,
> `LocalAgentConfig`/`LocalOpenAIAgentConfig` resolution. **This scope is closed.**
> `agy_sdk.py` / `agy_cli.py` remain in-tree as dormant adapters (clean fallback,
> zero cost) in case the service is ever re-adopted — they are explicitly
> **excluded from canary eligibility** while the subscription is cancelled.

---

## Executive Summary & Mission Objective (revised)

Phase 1 establishes the **harness-neutral execution canary**: any new agent-runtime
adapter must prove itself against the `AgentHarness` contract on non-critical work
before the fleet routes production work through it — without relying on any single
vendor's SDK, CLI wrapper, or terminal emulation.

The canary is a **dual-runtime architecture**:
- `PRISMATIC_HARNESS_RUNTIME=stable` (default): the proven harness for the task class.
- `PRISMATIC_HARNESS_RUNTIME=canary` (explicit opt-in): the candidate adapter under evaluation.

Live harnesses eligible as `stable` (verified 2026-09-27):
- `HermesHarness` (`prismatic/harnesses/hermes/adapter.py`) — systemd-managed Hermes
  agents (23 profiles in the harness registry); dispatch/status/logs/cancel via
  spool dir + `systemctl`/`journalctl`.
- Harness-neutral **verification intake** (`prismatic/review_factory/adapters/`):
  `claude_code.py`, `gemini_cli.py`, `codex_cli.py` — L0 pure translators turning
  CLI session output into `ReviewArtifact`s. These are evidence producers for the
  Review Factory, not execution harnesses; a canary may cover them as intake paths.

Every AI agent working on a canary task must follow the exact specifications, method
signatures, test requirements, and OKF update directives below.

---

## Technical Architecture & Component Specifications

```text
                               ┌────────────────────────────────┐
                               │  Fleet Manager / Task Router   │
                               └───────────────┬────────────────┘
                                               │
                                 get_harness(task_class, runtime_preference)
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       │                                               │
                       ▼                                               ▼
         [PRISMATIC_HARNESS_RUNTIME=stable]              [PRISMATIC_HARNESS_RUNTIME=canary]
        ┌───────────────────────────────┐               ┌───────────────────────────────┐
        │     Proven harness adapter    │               │    Candidate harness adapter  │
        │ • Implements AgentHarness     │               │ • Implements AgentHarness     │
        │ • Declares HarnessCapabilities│               │ • Declares HarnessCapabilities│
        │ • Registered in discovery     │               │ • Registered in discovery     │
        │ • e.g. HermesHarness          │               │ • e.g. new CLI harness        │
        └───────────────────────────────┘               └───────────────────────────────┘
```

### 1. Canary Adapter Implementation Contract
File: the candidate adapter module under `prismatic/harnesses/`.
Inherits: `AgentHarness` from `prismatic/harnesses/base.py`.

**Required surface** (all abstract methods honored):
```python
class CandidateHarness(AgentHarness):
    @property
    def name(self) -> str: ...            # stable adapter name

    @property
    def models(self) -> list[str]: ...    # runtime identifiers supported

    def dispatch(self, task: dict) -> str: ...                 # -> harness-local run id
    def status(self, run_id: str) -> dict: ...                 # {status, started_at, completed_at, error}
    def cancel(self, run_id: str) -> bool: ...
    def logs(self, run_id: str, tail: int = 100) -> list[str]: ...
    def cost(self, run_id: str) -> dict: ...                   # {tokens_in, tokens_out, dollars}
```

Additionally the adapter MUST declare `HarnessCapabilities` (streaming_logs,
cost_tracking, concurrent_runs, supports_cancel, supports_timeout,
max_context_tokens) and MUST be discoverable by `HarnessDiscoveryManager`
(`prismatic/harnesses/discovery.py`) so it appears in the harness registry.

### 2. Configuration Resolution Engine
- Runtime selection reads `PRISMATIC_HARNESS_RUNTIME` (default `stable`).
- `stable` resolves per task class to the proven adapter (e.g. Hermes target profile).
- `canary` resolves to the candidate adapter **only** when the adapter is registered
  and its declared capabilities cover the task; otherwise selection fails closed to
  `stable` with a logged warning.
- AGY adapters (`agy_sdk`, `agy_cli`) are never eligible for either slot while the
  Antigravity subscription is cancelled.

### 3. Usage & Cost Accounting
Unlike the void AGY-SDK plan (which read `response.usage_metadata`), accounting
flows through the contract: every completed run MUST return a `cost()` dict with
non-zero `tokens_in`/`tokens_out` (or `dollars`) so Directive 01 context-window
hygiene is preserved regardless of vendor.

### 4. Harness Factory & Dual-Runtime Dispatcher
The dispatcher lives wherever the fleet router resolves harnesses; its contract:

```python
def get_harness(task_class: str, runtime_preference: str | None = None) -> AgentHarness:
    runtime = (runtime_preference or os.environ.get("PRISMATIC_HARNESS_RUNTIME", "stable")).lower()
    if runtime == "canary":
        candidate = resolve_canary_adapter(task_class)   # registered + capability-checked
        if candidate is not None:
            return candidate
        logger.warning("canary requested but no eligible adapter; failing closed to stable")
    return resolve_stable_harness(task_class)
```

---

## Mandatory OKF Directives for Phase 1 (revised)

The executing agent MUST perform the following documentation updates in the same
commit as code changes:

### OKF Directive 1.1: Register the Canary in the Harness Standard
File: `prismatic/harnesses/README.md`
- Record the canary objective, the candidate adapter's `name`, and the task classes
  it is eligible for.
- **Key Result**: canary routes non-critical turns through the candidate adapter
  with zero `AgentHarness` contract violations and full `cost()` accounting.
- **Evidence**: contract-conformance tests + canary selection tests (see below).

### OKF Directive 1.2: Update Verification Receipts
- Record test execution results and exact pass counts in the PR body and the
  verification receipt: contract-conformance suite (must be 100% green),
  canary-selection suite, fallback suite.
- `docs/okf-antigravity-sdk-dual-instance-integration.md` is **historical** —
  do not extend it; it documents the void AGY-SDK scope.

### OKF Directive 1.3: Machine Parity and Validation
- Run the harness-related test subset plus `python3 scripts/validate_okf_docs.py`
  to guarantee zero OKF schema violations or broken links.

---

## Invariant & Failure-Mode Fencing

1. **Fail-Closed Fallback Invariant**: if the canary adapter fails to import,
   initialize, or declare capabilities, selection MUST log a warning and fall back
   cleanly to the `stable` harness without crashing fleet daemons.
2. **Headless Execution Invariant**: a canary adapter must never spawn interactive
   TTY prompts or require terminal attachment. Non-TTY invocations in cron or CI
   must succeed 100% of the time.
3. **Usage Accounting Invariant**: every completed canary run must return a
   non-zero `cost()` dict matching the adapter's declared `cost_tracking`
   capability, preserving Directive 01 context-window hygiene.
4. **No-Vendor-Lock Invariant**: a canary adapter must not require a paid
   third-party service that Michael has cancelled. (This is the clause that voids
   the original AGY-SDK scope.)

---

## Acceptance Criteria & Test Plan

1. **Contract-conformance tests**: the candidate adapter passes a shared suite
   asserting every `AgentHarness` abstract method, `HarnessStatus` value mapping,
   and `HarnessCapabilities` declaration. 100% required.
2. **Canary-selection tests**: `PRISMATIC_HARNESS_RUNTIME=stable` → stable adapter;
   `=canary` with eligible candidate → candidate; `=canary` with ineligible/missing
   candidate → stable + warning logged.
3. **Fallback tests**: candidate import failure → stable, no exception escapes.
4. **Regression Gate**: the full harness + review-factory related suite stays green.

---

## Peer-Agent Handoff Instructions

- **For Fred (Lead Orchestrator)**: when dispatching canary tasks, set
  `env={"PRISMATIC_HARNESS_RUNTIME": "canary"}` on non-critical tickets only;
  never on production fleet work until the canary graduates.
- **For George (Concurrency Specialist)**: validate that concurrent canary runs
  respect the adapter's declared `concurrent_runs` cap without cross-run state
  pollution.
- **For Ned (Verification Guard)**: verify the contract-conformance suite passes at
  100% before approving any canary PR handoff; a single contract violation fails
  the canary.
