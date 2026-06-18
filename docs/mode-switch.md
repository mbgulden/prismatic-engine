# Prismatic Engine — Orchestration Mode Switch Guide

The **Orchestration Mode Switch** controls the degree of human involvement required at key gates throughout the 7-Step Loop. By altering the active mode, teams can pivot between rigid manual staging controls and autonomous agent operations.

---

## 1. Description of Modes

The engine operates in one of three modes, configured via `PRISMATIC_ENGINE.yaml`:

### 1. Interactive Mode (`interactive`)
* **Philosophy**: Complete manual control over every step.
* **Behavior**:
  - The loop halts at all HITL (Human-in-the-Loop) gates.
  - Subtask plans are written to draft files for manual editing before execution.
  - Review reports and code diffs require manual sign-off.
  - Branches are staged but not merged until explicitly triggered by a developer.
* **Best Used For**:
  - Delicate refactorings.
  - High-risk core library migrations.
  - Fine-grained UI/UX polish tasks.
  - Initial configuration debugging.

### 2. Collaborative Mode (`collaborative`) - Default
* **Philosophy**: Hybrid human-agent partnership.
* **Behavior**:
  - Automated planning (`DECOMPOSE` $\rightarrow$ `DISPATCH`) and execution run autonomously.
  - Halts at the **Review Gate** (`Step 4: REVIEW`). A human operator must review git diffs, test logs, and linter reports, then approve/reject.
  - Once human approval is received, integration/staging promotion is executed automatically by the Governor agent.
* **Best Used For**:
  - Routine feature development.
  - Standard product engineering tasks.
  - Bug fixes and visual edits where manual code review is standard practice.

### 3. Autonomous Mode (`autonomous`)
* **Philosophy**: Full autopilot.
* **Behavior**:
  - The loop proceeds from `DECOMPOSE` through `INTEGRATE` without human intervention.
  - Review gates are automated: code validations, compilers, and unit test suites are run. If they pass, the code is immediately promoted.
  - Human escalation only occurs if an error breaker is tripped (e.g., test failures exceeding the refinement iteration limit).
* **Best Used For**:
  - Off-hours batch processes (such as overnight code styling or documentation generations).
  - Minor documentation updates.
  - Automated translation/localization updates.
  - Pre-approved repetitive chores.

---

## 2. Gating and Approval Queue Mechanics

The following matrix defines the validation gates and the action behavior for each mode:

| Mode | Gate 1: Plan Decomposition (Step 1 $\rightarrow$ 2) | Gate 2: Code Review (Step 4 $\rightarrow$ 5/7) | Gate 3: Integration (Step 7) |
| :--- | :--- | :--- | :--- |
| **Interactive** | **Halt** (`PENDING_PLAN_APPROVAL`) | **Halt** (`PENDING_INTEGRATION`) | **Halt** (Awaiting Manual Trigger) |
| **Collaborative** | *Auto-continue* (Skipped) | **Halt** (`PENDING_INTEGRATION`) | *Auto-continue* (Governor Merges) |
| **Autonomous** | *Auto-continue* (Skipped) | *Auto-continue* (AI review/lint only) | *Auto-continue* (Governor Merges) |

### Approval Queue Lifecycle & CLI Interaction

When a pipeline hits a human-in-the-loop gate, the `PipelineStateMachine` transitions to a pending state (such as `PENDING_PLAN_APPROVAL` or `PENDING_INTEGRATION`), writes the current state snapshot to disk, and pauses the dispatcher execution thread.

Developers interact with the approval queue using CLI commands:

#### 1. Checking Active Pending Tasks
```bash
prismatic-engine queue list
```
*Expected Output*:
```
Active Pending Tasks in Approval Queue:
ID          State                   Mode            Origin Issue
GRO-1234    PENDING_PLAN_APPROVAL   interactive     GRO-1234-login-auth
GRO-1565    PENDING_INTEGRATION     collaborative   GRO-1565-seven-step-loop
```

#### 2. Inspecting the Payload Draft
To view the plan details or code changes for a pending task:
```bash
prismatic-engine queue inspect GRO-1234
```

#### 3. Approving a Transition
To approve the plan or merge:
```bash
prismatic-engine queue approve GRO-1234
```
*Action*: The FSM advances to the next step (`DISPATCHING` or `INTEGRATING`) and resumes execution.

#### 4. Rejecting and Providing Feedback
To reject and route back to refinement:
```bash
prismatic-engine queue reject GRO-1234 --reason "Missing email export in user schema."
```
*Action*: FSM transitions to `FEEDBACK`, generates the feedback payload, and wakes up the refiner agent.

---

## 3. Escalation Patterns & Breakers

To prevent runaway loops (such as an agent making infinite refinement edits that exhaust LLM token budgets/credits), the engine implements three escalation layers:

### Layer 1: Loop Limit Safeguard (Refinement Circuit Breaker)
* **Rule**: When transitioning from `REFINE` back to `REVIEW`, the state machine increments `_review_cycles`.
* **Action**: If `_review_cycles >= 3` and the review continues to fail, the engine triggers a **Mode Escalation**:
  1. The pipeline state switches to `interactive`.
  2. The pipeline transitions to `PENDING_INTEGRATION` / suspended execution.
  3. A critical telemetry alert is logged.
  4. The developer is notified (via Slack/Linear comment) to resolve the bug manually.

### Layer 2: Lock Stalling & Heartbeat Timeout
* **Rule**: Worker agents must write a heartbeat file (e.g. updating timestamp in `.antigravity/locks/heartbeat-<threadId>`) every 60 seconds.
* **Action**: If a worker crashes or hangs without releasing its file locks:
  1. The watch dog process detects a heartbeat absence exceeding 5 minutes.
  2. The active workspace locks are force-released.
  3. The FSM transitions to `FAILED` with error metadata `LOCK_HEARTBEAT_TIMEOUT`.

### Layer 3: Pre-Integration Rollbacks
* **Rule**: Prior to executing a merge into `deploy-fresh`, the Governor agent creates a git staging snapshot.
* **Action**: If post-merge verification tests fail:
  1. Staging branch is hard-reset back to the snapshot.
  2. The FSM is routed back to `FEEDBACK` for refinement.
  3. The active orchestration mode is downgraded to `collaborative` to prevent automatic merge re-attempts.
