# Prismatic Engine — 7-Step Iterative Loop Specification

**Document Version:** 1.0.0  
**Date:** 2026-06-08  
**Author:** Antigravity (Senior Systems Architect)  
**Status:** Approved for Implementation  
**Linear Issue:** [GRO-816](https://linear.app/growthwebdev/issue/GRO-816)

---

## 1. Overview & Core Philosophy

The **Prismatic 7-Step Iterative Loop** is the operational backbone of the Prismatic Engine. It defines the formal lifecycle of a task as it progresses from a high-level user request (Megaprompt) down to individual code/content edits, reviews, and git-integrated merges. 

By structuring agent workflows into a strict, state-machine-driven loop, the Prismatic Engine achieves:
- **Deterministic Transitions:** Eliminates "agent drift" where worker agents lose context or perform unauthorized tasks.
- **Human-in-the-Loop (HITL) Adaptability:** Integrates review gates whose behavior changes dynamically based on the active **Orchestration Mode**.
- **Conflict Prevention:** Orchestrates branch creation, mutex locking, and pre-push validations in sync with the task state.

```
                  ┌──────────────────────────────┐
                  │      User Megaprompt         │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │  1. DECOMPOSE        │
                     └──────────┬───────────┘
                                │
                                ▼
                     ┌──────────────────────┐
                     │  2. DISPATCH         │
                     └──────────┬───────────┘
                                │
                                ▼
                     ┌──────────────────────┐
                     │  3. EXECUTE          │◄────────────────┐
                     └──────────┬───────────┘                 │
                                │                             │
                                ▼                             │
                     ┌──────────────────────┐                 │
                     │  4. REVIEW           │                 │
                     └──────────┬───────────┘                 │
                                │                             │
                  ┌─────────────┴─────────────┐               │
                  │                           │               │
            (Issues Found)              (Approved)            │ (Refinement Loop)
                  │                           │               │
                  ▼                           ▼               │
       ┌────────────────────┐      ┌────────────────────┐     │
       │  5. FEEDBACK       │      │  7. INTEGRATE      │     │
       └──────────┬─────────┘      └────────────────────┘     │
                  │                                           │
                  ▼                                           │
       ┌────────────────────┐                                 │
       │  6. REFINE         ├─────────────────────────────────┘
       └────────────────────┘
```

---

## 2. The 7-Step Loop Lifecycle

### Step 1: Decompose
* **Objective:** Parse a high-level task/Megaprompt and decompose it into a set of specialized, non-overlapping worker contracts.
* **Mechanism:**
  - The [SwarmPlanner](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/SwarmPlanner.ts) invokes the Gemini LLM with a strict JSON system prompt.
  - The output is an array of `AgentContract` objects.
* **Exit Criteria:** A valid array of `AgentContract` schemas representing the swarm execution plan.

### Step 2: Dispatch
* **Objective:** Instantiated worker agents are assigned to execution branches, lanes are configured, and threads are registered.
* **Mechanism:**
  - The [SwarmOrchestrator](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/SwarmOrchestrator.ts) checks each contract's `targetHead`.
  - Background processes are spawned for `Headless API`, `Local AI`, and `GitHub Jules`.
  - `Antigravity UI` tasks are added to the [UiQueueProcessor](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/UiQueueProcessor.ts) sequential queue.
  - Mutex hooks deploy `.antigravity/swarm.js` CLI to the workspace root.
* **Exit Criteria:** Spawning of the worker environment and creation of `.antigravity/contracts/<threadId>.json`.

### Step 3: Execute
* **Objective:** The worker agent edits the codebase within its assigned lane boundaries while respecting locks.
* **Mechanism:**
  - The worker creates/switches to a git branch matching its contract's prefix (e.g. `content/` or `feature/`).
  - Prior to editing any file, the worker invokes `node .antigravity/swarm.js lock <filepath> <threadId>`.
  - The worker performs edits, runs local validations, and commits.
  - On completion, the agent pushes the branch to remote.
* **Exit Criteria:** Pushed git branch containing the task commits, and lock release calls executed.

### Step 4: Review
* **Objective:** Audit the worker's changes for correctness, styling, compilation, and security.
* **Mechanism:**
  - Triggered automatically on push or branch completion.
  - Reviewers can be automated agents (e.g. `Jules` for code, `AGY` for design), static validation tools (compilers, linters), or a human operator.
  - The reviewer runs git diffs and compiles the staging branch code.
* **Exit Criteria:** A review report containing either `status: "approved"` or `status: "rejected"` with detailed comments.

### Step 5: Feedback
* **Objective:** Formulate a structured payload of issues and transmit it back to the active worker thread.
* **Mechanism:**
  - If the review fails, the orchestrator compiles a feedback payload listing failing files, compilation errors, and reviewer suggestions.
  - The orchestrator updates the state of the thread to `REFINE` and wakes up the worker.
* **Exit Criteria:** Feedback payload successfully written and worker thread notified.

### Step 6: Refine
* **Objective:** The worker adjusts its changes in response to review issues.
* **Mechanism:**
  - The worker reads the feedback file/payload.
  - It acquires the necessary file locks again.
  - It fixes the issues, commits, and pushes the branch.
  - The loop routes back to **Step 4: Review**.
* **Exit Criteria:** Revised branch pushed to remote, transitioning back to `REVIEW` state.

### Step 7: Integrate
* **Objective:** Merge the approved worker branch into the staging/deploy-fresh base branch and resolve the contract.
* **Mechanism:**
  - The Governor Agent (`Fred`) executes the merge of the worker branch into `deploy-fresh`.
  - All remaining locks associated with the worker are cleared.
  - The contract file `.antigravity/contracts/<threadId>.json` is deleted.
  - The orchestrator updates the global swarm status.
* **Exit Criteria:** Successful git merge to staging branch, locks deleted, and contract marked as resolved.

---

## 3. Orchestration Mode Switch

The **Orchestration Mode Switch** controls how many loop steps require human intervention or approval. It adjusts the behavior of the state transitions dynamically:

| Mode | HITL Gate 1: Decompose (Step 1 → 2) | HITL Gate 2: Review (Step 4 → 5/7) | HITL Gate 3: Integrate (Step 7) | Ideal Use Case |
| :--- | :--- | :--- | :--- | :--- |
| **Interactive** | **Required** (Human reviews & edits contracts) | **Required** (Human approves review reports) | **Required** (Human triggers merge) | High-risk migrations, sensitive UI/UX styling. |
| **Collaborative** | *Auto-continue* (Skipped) | **Required** (Human reviews PR/code diffs) | *Auto-continue* (Governor merges on approval) | Core software development (default mode). |
| **Autonomous** | *Auto-continue* (Skipped) | *Auto-continue* (AI review only) | *Auto-continue* (Governor merges automatically) | Overnight batch runs, deep research tasks. |

---

## 4. State Machine Definition

The loop is modeled as a finite state machine (FSM). Below are the states, valid transitions, and event triggers:

### State Transition Table

| Current State | Event | Target State | Action / Side Effect |
| :--- | :--- | :--- | :--- |
| `IDLE` | `MEGAPROMPT_RECEIVED` | `DECOMPOSING` | Launch SwarmPlanner LLM parser |
| `DECOMPOSING` | `PLAN_GENERATED` (Interactive) | `PENDING_PLAN_APPROVAL` | Write contracts to draft, notify human |
| `DECOMPOSING` | `PLAN_GENERATED` (Collab/Auto) | `DISPATCHING` | Commit contracts, proceed directly |
| `PENDING_PLAN_APPROVAL`| `PLAN_APPROVED` | `DISPATCHING` | Transition to dispatcher |
| `PENDING_PLAN_APPROVAL`| `PLAN_REJECTED` | `IDLE` | Cancel task, clear state |
| `DISPATCHING` | `AGENTS_PROVISIONED` | `EXECUTING` | Create contract files, boot executor processes |
| `EXECUTING` | `BRANCH_PUSHED` | `REVIEWING` | Trigger code review agent / linters |
| `REVIEWING` | `REVIEW_FAILED` | `FEEDBACK` | Generate feedback payload, suspend executing |
| `REVIEWING` | `REVIEW_PASSED` (Interactive) | `PENDING_INTEGRATION` | Notify human for merge confirmation |
| `REVIEWING` | `REVIEW_PASSED` (Collab/Auto) | `INTEGRATING` | Hand off to Governor agent |
| `FEEDBACK` | `FEEDBACK_DELIVERED` | `REFINING` | Wake up worker agent with context |
| `REFINING` | `BRANCH_REVISED` | `REVIEWING` | Re-trigger code review agent |
| `PENDING_INTEGRATION` | `INTEGRATION_APPROVED` | `INTEGRATING` | Trigger Governor agent merge |
| `PENDING_INTEGRATION` | `INTEGRATION_REJECTED` | `FEEDBACK` | Send human remarks to feedback |
| `INTEGRATING` | `MERGE_SUCCESS` | `IDLE` | Resolve contract, delete locks, release staging |

---

## 5. Event & Payload Contracts (JSON Schemas)

### 1. Plan Decomposition Output (Step 1)
JSON payload returned by `SwarmPlanner` to describe worker tasks:
```json
{
  "megaprompt": "Implement the auth page and content for active-oahu.",
  "contracts": [
    {
      "threadId": "2026-06-08T06-30-00-123",
      "role": "Frontend Developer",
      "taskDescription": "Implement Login component in src/components/Login.tsx",
      "allowedDirectories": ["src/components/"],
      "readOnlyDirectories": ["src/types/"],
      "targetHead": "Antigravity UI",
      "budgetLimit": 2.50,
      "localContextMax": 16384
    }
  ]
}
```

### 2. Review Report Payload (Step 4)
JSON payload generated by automated review systems (`JulesExecutor` or `AGY` designer):
```json
{
  "threadId": "2026-06-08T06-30-00-123",
  "reviewerId": "jules",
  "status": "rejected",
  "timestamp": 1749326800000,
  "summary": "TypeScript compilation failed due to missing exports in src/types/User.ts.",
  "details": [
    {
      "filePath": "src/components/Login.tsx",
      "line": 14,
      "severity": "error",
      "message": "Property 'email' does not exist on type 'UserConfig'."
    }
  ],
  "recommendations": "Ensure you coordinates changes in user types or use optional properties."
}
```

### 3. Loopback Feedback Payload (Step 5)
JSON payload injected back into the worker agent's context during refinement:
```json
{
  "status": "LOOPBACK_PENDING",
  "iteration": 2,
  "feedbackSource": "Reviewer Agent Jules",
  "message": "Your branch feature/auth-login has compilation errors. Please fix and commit again.",
  "errorLog": "src/components/Login.tsx(14,12): error TS2339: Property 'email' does not exist on type 'UserConfig'."
}
```

---

## 6. Codebase Mapping & Connections

The 7-step loop integrates directly with the existing files in the **Antigravity Orchestration Hub**:

1. **Decompose ── [SwarmPlanner.ts](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/SwarmPlanner.ts)**
   - Utilizes `decomposeMegaprompt` to split prompts.
   - We connect this to a formal state manager to trigger transition from `IDLE` to `DECOMPOSING`.

2. **Dispatch ── [SwarmOrchestrator.ts](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/SwarmOrchestrator.ts) & [ContractManager.ts](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/ContractManager.ts)**
   - `spawnDelegatesFromContracts` reads the decomposed plan and routes to executors (`HeadlessExecutor`, `LocalExecutor`, `UiQueueProcessor`).
   - Writes the contract files to `.antigravity/contracts/<threadId>.json`.

3. **Execute ── [HandoffProtocol.ts](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/HandoffProtocol.ts) & [SwarmLockManager.ts](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/SwarmLockManager.ts)**
   - Enables execution continuation through context-saving handoffs.
   - Enforces scope lanes (`allowedDirectories`) and issues file-level locks.
   - Decoupled headless `swarm.js` allows execution scripts to acquire locks during subprocess commands.

4. **Review / Feedback / Refine ── [JulesExecutor.ts](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/JulesExecutor.ts)**
   - Uses `JulesExecutor` to invoke PR reviews and compile results.
   - Handles loopbacks by re-injecting the feedback markdown into the thread.

5. **Integrate ── [HandoffProtocol.ts](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/HandoffProtocol.ts) & [ContractManager.ts](file:///home/ubuntu/mounts/synology-photo/Workshop/Antigravity%20Orchestration%20Hub/src/engine/ContractManager.ts)**
   - `resolveContract` cleans up contract files.
   - Lock manager releases all locks held by the agent.
   - Git operations merge the branch.

---

## 7. Robust Handoff & Loopbacks

To prevent infinite loops during Step 5 (Feedback) and Step 6 (Refine):
1. **Loop Limit Safeguard:** The orchestrator tracks the iteration counter. If a thread undergoes more than 3 consecutive rejection loops, the orchestrator triggers a **Mode Escalation**, pausing execution and prompting the human operator.
2. **Dynamic Lock Re-assertion:** During refinement, the agent is allowed to request new locks or maintain existing ones, but these are subjected to automatic stale cleanup via the 5-minute heartbeat mechanism to avoid deadlocks.
3. **Pre-Integration Staging Snapshot:** Before merging any code, a staging snapshot is created. If the integration tests fail, the merge is rolled back, the staging branch is restored to its original state, and the work goes back to the Refinement loop.
