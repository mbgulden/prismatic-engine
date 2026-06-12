---
name: prismatic-7-step-loop
description: >-
  Execute tasks through the Prismatic Engine 7-step iterative loop:
  DECOMPOSE → DISPATCH → EXECUTE → REVIEW → FEEDBACK → REFINE → INTEGRATE.
  Eliminates agent drift through deterministic state-machine transitions.
---

# Prismatic 7-Step Iterative Loop

## Trigger
Load this skill when executing any task within the Prismatic Engine framework
or when the user requests structured agent workflows with review gates.

## Overview

The 7-Step Loop is the operational backbone of the Prismatic Engine. It defines
the formal lifecycle of a task from a high-level request down to individual
edits, reviews, and git-integrated merges. By structuring agent workflows into a
strict state-machine-driven loop, the engine achieves:

- **Deterministic Transitions:** Eliminates agent drift — worker agents never
  lose context or perform unauthorized tasks.
- **Human-in-the-Loop (HITL) Adaptability:** Review gates whose behavior
  changes dynamically based on the active Orchestration Mode.
- **Conflict Prevention:** Branch creation, mutex locking, and pre-push
  validations sync with the task state.

## The 7 Steps

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

### Step 1: DECOMPOSE
- **What:** Parse a high-level task and decompose into specialized,
  non-overlapping worker contracts.
- **Who:** SwarmPlanner (LLM-driven decomposer)
- **Input:** User Megaprompt (natural language)
- **Output:** Array of `AgentContract` objects (threadId, role, taskDescription,
  allowedDirectories, readOnlyDirectories, targetHead, budgetLimit,
  localContextMax)
- **Gate:** Valid array of contracts with no overlapping directory permissions

### Step 2: DISPATCH
- **What:** Instantiate worker agents, assign execution branches, configure
  lanes, register threads.
- **Who:** SwarmOrchestrator + ContractManager
- **Input:** Array of AgentContract objects from Step 1
- **Output:** `.antigravity/contracts/<threadId>.json` files, spawned agent
  processes/threads
- **Gate:** All agents provisioned, contracts written to disk

### Step 3: EXECUTE
- **What:** Worker agents execute their assigned contracts. Each agent works
  in its own lane with file-level locking.
- **Who:** Worker agents (Ned, Jules, AGY, Kai, Codex)
- **Input:** AgentContract JSON
- **Output:** Code changes, content, or artifacts committed to the agent's
  execution branch
- **Gate:** Agent reports completion; all files pass syntax/lint checks

### Step 4: REVIEW
- **What:** Staging Governor (Fred) reviews changes. In **Standard Mode**:
  auto-merge on syntax pass. In **Alchemy Mode**: multi-stage quality gates.
- **Who:** Staging Governor (Fred)
- **Input:** Agent's execution branch
- **Output:** Approval decision (merge or feedback)
- **Gate:** All quality gates pass; no blocker-level issues

### Step 5: FEEDBACK
- **What:** Generate structured failure report with exact line references,
  severity levels (blocker/warning), and suggested fixes.
- **Who:** Reviewer agent (Fred or AGY in review mode)
- **Input:** Review output from Step 4 (if issues found)
- **Output:** Structured feedback report
- **Gate:** Feedback report is complete and actionable

### Step 6: REFINE
- **What:** Worker agent applies feedback, re-runs tests, and re-submits
  for review. Bounded retry quota (3 attempts), then escalation.
- **Who:** Original worker agent
- **Input:** Structured feedback report from Step 5
- **Output:** Updated execution branch with fixes
- **Gate:** Changes address all blocker-level issues; loop back to Step 4

### Step 7: INTEGRATE
- **What:** Merge approved changes into production, update contracts, log
  provenance, clean up branches.
- **Who:** Staging Governor (Fred)
- **Input:** Approved execution branch
- **Output:** Merged commit on main/master, updated Linear card, provenance
  log entry
- **Gate:** Merge successful; all contracts archived

## Orchestration Modes

| Dimension | Standard Mode | Alchemy Mode |
|---|---|---|
| **Intake** | Raw user prompt → agent | Structured brief → briefing agent → pipeline |
| **Review** | Basic syntax/lint | Multi-stage quality gates with YAML checklists |
| **Self-Healing** | Compiler loop retries | Structured feedback → bounded retries (3) → escalation |
| **Provenance** | Git commit message | Full JSON tracking: edits, rationales, critiques, gates |
| **Review Rounds** | 0-1 rounds | Minimum 2 stages (draft gate + publishing gate) |

## Pitfalls

- ❌ **Skipping DECOMPOSE:** Feeding a megaprompt directly to a worker agent
  produces unpredictable results ("Mystery Gift Out"). Always decompose first.
- ❌ **Merging without REVIEW:** In Alchemy Mode, bypassing quality gates
  defeats the entire purpose. Review is mandatory.
- ❌ **Unbounded retries:** The REFINE loop must have a hard cap (3 attempts).
  After exhaustion, escalate to a human — do not loop indefinitely.
- ❌ **Mixed lane permissions:** Two agents writing to the same directory
  without mutex locking causes merge conflicts. DECOMPOSE must ensure
  non-overlapping lane assignments.

## Integration with Lane Governance

The 7-step loop integrates with the Prismatic lane governance system:
- **Step 2 (DISPATCH)** assigns each agent a lane with write access to
  specific directories and read-only access to everything else.
- **Step 3 (EXECUTE)** enforces lane ownership via pre-push hooks.
- **Step 4 (REVIEW)** validates that no lane violations occurred during
  execution.
- **Step 7 (INTEGRATE)** merges only if all lane checks pass.

See also: `lane-governance` skill, `alchemy-quality-gates` skill.
