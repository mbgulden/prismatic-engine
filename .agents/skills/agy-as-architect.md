---
name: agy-as-architect
description: "AGY designs systems before any code is written — shapes, contracts, invariants, failure modes, migration paths, decision records. Use when dispatching AGY to architect a new feature, refactor an existing system, plan a Prismatic Engine module, evaluate a tech choice, or produce an architecture decision record (ADR). AGY as architect means: define the shape, surface the trade-offs, name the invariants — never just 'refactor this'. Pairs with `agy-as-planner` (decomposes into tasks) and `agy-as-reviewer` (validates the result). Inherits delegation pattern from `agy-delegate-goals-not-tasks` — give AGY the goal and the decision under design, not a TODO list."
tags: []
related_skills:
  - agy-delegate-goals-not-tasks
  - agy-as-planner
  - agy-as-reviewer
  - agy-as-coder
  - prismatic-engine-operations
---

# AGY as Architect

> **Philosophy:** Architecture is the *shape* of a system before any code is written. AGY's job is to make the shape explicit, surface the trade-offs, name the invariants, and produce artifacts that survive the next person to touch the system. Architecture that doesn't reduce future work is just sketching.

## When to Use This Lane

Use `agy-as-architect` when AGY is doing any of:

- Designing a new feature, service, module, or library
- Proposing a refactor that crosses more than one file or one subsystem
- Picking between competing technical approaches (frameworks, datastores, protocols)
- Designing a Prismatic Engine kernel module or attach-point
- Producing an Architecture Decision Record (ADR) for a non-trivial decision
- Evaluating a third-party dependency or platform (capability-fit + lock-in cost)
- Producing a system-shape diagram (components, edges, ownership) for a greenfield area

**Do NOT use this lane** for: code changes (→ `agy-as-coder`), bug investigations (→ `agy-as-debugger`), task decomposition (→ `agy-as-planner`), or PR review (→ `agy-as-reviewer`).

## The Architect's Loop

AGY runs a 5-stage loop. Don't skip stages.

```
1. Frame        — what decision is actually being made, and what for whom
2. Constraints  — hard limits, soft preferences, must-not-violate invariants
3. Shape        — components, edges, ownership, data flow
4. Trade-offs   — name 2-4 real alternatives; pick one with explicit reasoning
5. Contract     — concrete interfaces / APIs / file shapes another agent can execute against
```

Every architecture artifact AGY produces must answer all five.

## The Dispatch Pattern (how to talk to AGY in this lane)

**Give AGY the goal + the decision under design. Do not give AGY a TODO list.**

```bash
cd /tmp && agy --prompt-interactive --print "Goal: design the <X> subsystem for <project>. Decision under design: <the actual question>. Hard constraints: <invariants>. Soft preferences: <bias>. Output: <ADR markdown + interface stubs + 1 diagram>. Do NOT modify any code." 2>&1
```

For research-heavy architecture work, point AGY at anchors first, then let it investigate:

```
"...Read /path/to/repo-shape.md and /path/to/decision-context.md as anchors. Then investigate further (web, repos, papers) as needed. Produce the artifacts listed above."
```

**Pitfall:** `--add-dir` on architecture work triggers AGY's builder instinct — it will start editing files. For pure architecture, **launch from `/tmp` with no `--add-dir`**. Architecture artifacts are markdown + stubs, not commits.

## Required Artifacts (what AGY must produce)

Every architecture dispatch must return **all** of these, in this order, in one bundle:

### 1. Frame (one paragraph)
- The decision under design in one sentence
- Who is affected (users, agents, operators)
- What changes if we get it wrong
- The time horizon (a quarter? a year? forever?)

### 2. Constraints
- **Hard invariants** — things that must not be violated (security, compatibility, regulatory, business)
- **Soft preferences** — bias toward simplicity, performance, ecosystem fit, etc.
- **Non-goals** — explicitly out of scope

### 3. System Shape
- **Components** — named boxes with one-line responsibilities
- **Edges** — who calls whom, what flows across each edge
- **Ownership** — which agent / service / module owns state
- **Failure modes** — for each edge, what happens when it breaks (timeout, partial, replay, idempotency)
- **Diagram** — at minimum an ASCII box diagram; if visual is needed, AGY produces SVG/Excalidraw

### 4. Trade-off Matrix

```
| Alternative   | Pros                       | Cons                       | Rejected because… |
|---------------|----------------------------|----------------------------|-------------------|
| A: <approach> | <benefits>                 | <costs>                    | <reason>          |
| B: <approach> | <benefits>                 | <costs>                    | <reason>          |
| C: chosen     | <benefits>                 | <costs>                    | (selection)       |
```

At least 2 rejected alternatives. The chosen option must have an explicit "we accept these costs" rationale.

### 5. Contract (executable interface)

Concrete artifacts another agent can implement against:

- File paths and module boundaries
- Function signatures / API shapes / CLI surfaces
- Data schemas (JSON, Protobuf, database tables)
- Error taxonomy (codes + recovery behavior)
- Test surface (what tests would prove the contract holds)
- Migration path (if changing existing shape: rollout, rollback, compat window)

### 6. Risk Register

```
| Risk                         | Likelihood | Impact | Mitigation            | Owner |
|------------------------------|------------|--------|-----------------------|-------|
| <what could break>           | H/M/L      | H/M/L  | <how we plan to avoid>| <who> |
```

Top 5-10 risks. No hand-waving.

## Quality Bar

An architecture artifact from AGY is **good** if:

- ✅ A second agent can pick it up cold and implement against the contract without asking questions
- ✅ The trade-off matrix has at least 2 rejected alternatives with explicit reasoning
- ✅ Failure modes are named for every edge, not glossed
- ✅ The chosen option explicitly states the costs accepted
- ✅ Invariants are testable, not vibes
- ✅ Risk register has owners and mitigations, not platitudes

An architecture artifact is **bad** if:

- ❌ "Just refactor this" with no shape
- ❌ Single-option advocacy with no rejected alternatives
- ❌ Edges without failure modes
- ❌ "Consider X" without saying "and we picked Y because Z"
- ❌ Code changes snuck into an architecture dispatch

## Patterns AGY Should Default To

- **Prismatic Engine kernel thinking** — design as attach-points, not monoliths. Anything that can be a kernel module should be one; anything that's harness-specific stays in the shim layer.
- **Harness-agnostic first** — engine features must not require Hermes or any specific harness to operate. Hermes/OpenClaw/etc. are optional shims.
- **Fail-loud, not fail-silent** — every external dependency has a timeout, every error has a code, every retry has a budget.
- **Boring > clever** — pick the technology that gets out of your way. Novel = cost.
- **State at the edges** — keep state in the place that owns it. No shared mutable state across components without an explicit protocol.

## Common Failure Modes

| Symptom | Root cause | Fix |
|---|---|---|
| AGY produces 5 pages of prose, no contract | Confused architect with researcher | Force `## 5. Contract` section to be the largest in the artifact |
| AGY picks option A and dismisses B/C in one line | Trade-off matrix skipped | Require explicit rejected-because column |
| AGY edits code during architecture work | `--add-dir` triggered builder instinct | Launch from `/tmp`, no `--add-dir` |
| AGY's diagram is missing | Treated as optional | Make diagram mandatory; ASCII minimum, SVG preferred |
| AGY produces "migrate to Rust" advice | Didn't read constraints | Include hard constraints in the dispatch; re-dispatch with explicit constraint list |

## Companion Artifacts

Pair `agy-as-architect` with:

- **`agy-as-planner`** — once the contract is decided, decompose into tasks
- **`agy-as-coder`** — implements against the contract
- **`agy-as-reviewer`** — validates the implementation matches the architecture
- **`prismatic-engine-operations`** — if designing inside the Engine: lane rules, locking protocol, branch/commit conventions

## North Star

> "Architecture is the cheapest place to change your mind. Code is the most expensive. Make the shape right before you write the line."
