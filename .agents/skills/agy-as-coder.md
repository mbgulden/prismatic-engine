---
name: agy-as-coder
description: "AGY writes production code in the repo — analyze → edit → build → test → verify, locally before anything reaches staging. Use when AGY is implementing a feature, fixing a bug with a known root cause, adding tests, writing a Prismatic Engine module, or shipping a refactor. AGY as coder means running the full local loop, not just producing diffs. Pairs with `agy-as-architect` (gets the contract), `agy-as-debugger` (handles root-cause hunts), `agy-tdd-discipline` (writes tests before code), and `agy-as-reviewer` (post-merge audit). Inherits delegation pattern from `agy-delegate-goals-not-tasks` — give AGY the goal and acceptance criteria, not step-by-step edits."
tags: []
related_skills:
  - agy-delegate-goals-not-tasks
  - agy-as-architect
  - agy-as-debugger
  - agy-as-reviewer
  - agy-tdd-discipline
  - agy-secure-coding
  - prismatic-engine-operations
---

# AGY as Coder

> **Philosophy:** Code is a contract with the future. AGY's job is to write code that runs, has tests, passes CI, and would survive the next person to read it. Diff-snippets that compile are not code. Code is the artifact that survives a code review.

## When to Use This Lane

Use `agy-as-coder` when AGY is doing any of:

- Implementing a feature with a defined contract (architect lane produced it, or you have one inline)
- Writing a new module, library, or service
- Adding tests for existing code (TDD lane)
- Shipping a planned refactor
- Wiring integrations (Linear, GitHub, Stripe, Cloudflare, etc.)
- Building Prismatic Engine kernel modules or attach-points
- Scaffolding a new repo, command, or pipeline

**Do NOT use this lane** for: open-ended bug hunts (→ `agy-as-debugger`), architecture decisions (→ `agy-as-architect`), task decomposition (→ `agy-as-planner`), or "I wonder why this fails" (→ `agy-as-debugger` first).

## The Coder's Loop (non-negotiable)

```
1. Read   — load the contract, the existing code, the tests, the conventions
2. Plan   — write a short task list inside AGY's prompt; show your work
3. Edit   — make minimal, focused, contract-aligned changes
4. Build  — run compile/build commands; fix what breaks
5. Test   — run the test suite; if no tests exist, write failing ones first (TDD)
6. Verify — exercise the change end-to-end (curl, browser, screenshot, log inspection)
7. Stage  — commit + push to a branch, NEVER to main or deploy-fresh
```

AGY must NOT skip a stage. The biggest failure mode is "I edited, the build passed, ship it" — that's not verification. Verification means the *thing the user cares about* now works.

## The Dispatch Pattern

**Architecture exists OR you're providing inline contract → AGY runs the local loop:**

```bash
cd ${PRISMATIC_HOME}/work/<project> && agy --prompt-interactive --add-dir ${PRISMATIC_HOME}/work/<project> --print "Goal: implement <feature> per contract at /path/to/contract.md. Acceptance: <testable criteria>. Branch: feature/<name>. Run: <test command>. Verify: <how to prove it works>." 2>&1
```

**Critical: `--add-dir` is required for coder work** (opposite of architect/research). Without it, AGY can't see the workspace and produces hallucinated diffs.

**Critical: branch MUST be `feature/<name>`** — see `prismatic-engine-operations` for the lane rules. Direct push to `main` or `deploy-fresh` is a lane violation.

**Critical: tests must pass and verification must be external**, not "I read the code and it looks right." External = a real curl, a real browser, a real log line, a real test run.

## Required Outputs

Every coder dispatch must return:

### 1. Plan (1-2 paragraphs)
What AGY is about to change, in what order, and why. Show the reasoning, not just the diff.

### 2. Branch + Commits
- Branch name: `feature/<short-kebab>` (Prismatic Engine convention)
- Commit prefix: `[AGY]` (not `[Fred]`, not bare) — see `prismatic-engine-operations`
- One commit per logical change. Not 18 commits. Not 1 mega-commit.

### 3. Test Evidence
```
- <test command>
- <output showing pass/fail>
- <coverage delta if measurable>
```

If the project has no tests, AGY writes them. No "I'll add tests later."

### 4. Verification Evidence
```
- <how the change was exercised>
- <what the output was>
- <before/after or pass/fail signal>
```

For UI: screenshot or DOM inspection. For APIs: curl response. For background jobs: log lines. For data: row count or query result.

### 5. Lane Audit
- Did you touch any files outside your lane? (→ `prismatic-engine-operations` lane gates)
- Did you push to `main` or `deploy-fresh`? (→ violation, undo + replay on branch)
- Did you ship a TODO without a Linear issue ID? (→ write the issue)
- Did you leave documentation out of the same commit? (→ fix in this same dispatch; see `agy-documentation-on-the-fly`)

## Test-First Default

If the contract has acceptance criteria, **AGY writes the failing test first** (RED), then the implementation (GREEN), then refactors (REFACTOR). This is non-negotiable for:

- New functions/methods
- New API endpoints
- New CLI commands
- Bug fixes with reproducible input

If the project has no test infrastructure, AGY's first commit is the test infrastructure. No exceptions.

For trivial changes (typo, single-line config, naming), test-first is overhead — AGY should note "trivial change, no test added, because: <reason>" and move on.

## Code Quality Defaults

AGY writes code that:

- **Compiles/builds cleanly** — no warnings introduced
- **Has tests** — at minimum covering the new behavior and the obvious failure modes
- **Has no secrets** — no `Authorization: Bearer ***` literals, no API keys in code, no credentials in test fixtures (use mocks/env)
- **Has no dead code** — no commented-out blocks, no "I'll fix this later" comments without Linear issue IDs
- **Has typed interfaces at boundaries** — function signatures, CLI args, API contracts
- **Has error semantics** — every error path returns a typed error with a recovery hint, not a swallowed string
- **Has logging at meaningful boundaries** — external calls, retries, state transitions, but never secret values
- **Updates docs in the same commit** — see `agy-documentation-on-the-fly`

## Forbidden Moves

| Move | Why forbidden | What to do instead |
|---|---|---|
| Push to `main` or `deploy-fresh` | Lane violation; staging governor is the only merge path | Use `feature/<name>` branch; open PR |
| `--add-dir` on research/architecture work | Triggers builder instinct, AGY edits code instead of designing | Launch from `/tmp`, no `--add-dir` |
| Edit code outside the lane | Lane gating (`prismatic-engine-operations`) | Hand off to the owning lane agent (Fred/AGY/Ned/Kai) |
| Add a TODO without a Linear issue ID | TODOs without owners rot | Create the Linear issue, link it in the comment |
| Ship without running the build | "It compiles in my head" is not verification | Run the build; capture output |
| Ship without running tests | "I'll add tests in the next PR" is a lie | Run tests in this dispatch |
| `--no-verify` / `--force` / `--no-hooks` | Bypasses the safety net | Fix the underlying issue |
| Comment out failing tests | Hides the failure | Investigate; if the test is wrong, fix it; if the code is wrong, fix the code |
| Magic numbers from inline values | Loses context for the next person | Constants with names |

## Common Failure Modes

| Symptom | Root cause | Fix |
|---|---|---|
| AGY edits files but the build still fails | Skipped the build step | Re-dispatch with explicit "run `<build cmd>` and paste output" |
| AGY ships without tests | TDD skipped | Re-dispatch with `## Test Plan` section before `## Implementation` |
| AGY pushes to `main` | Lane violation | Revert + replay on `feature/<name>` branch; flag lane-violation to Fred |
| AGY produces a 500-line diff in one go | No planning, no incremental commits | Re-dispatch with explicit plan + per-commit acceptance |
| AGY's verification is "I read the diff" | Internal verification only | Re-dispatch with `## External Verification: <curl/screenshot/log>` |
| AGY ignores `--add-dir` boundary and edits `content/` | Lane violation | Patch the lane list in `prismatic-engine-operations`; re-dispatch with explicit "do not touch <files>" |
| AGY leaves a TODO without an issue | Hygiene drift | Create Linear issue + edit the comment |

## Companion Artifacts

Pair `agy-as-coder` with:

- **`agy-as-architect`** — architect produces the contract; coder implements against it
- **`agy-as-debugger`** — when something breaks and root cause is unknown
- **`agy-as-reviewer`** — post-merge audit on the coder's PR
- **`agy-tdd-discipline`** — for non-trivial code, the test-first discipline
- **`agy-secure-coding`** — secret-leak guardrails, OWASP patterns
- **`agy-documentation-on-the-fly`** — docs ship with code, never follow-up
- **`prismatic-engine-operations`** — lane rules, locking protocol, branch/commit conventions

## North Star

> "Code without tests is a hypothesis. Code without verification is a promise. Code without a contract is a guess."
