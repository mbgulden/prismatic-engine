# Prismatic Engine — Research Synthesis Report

**Author:** AGY (Senior Systems Architect)  
**Date:** June 8, 2026  
**Issue Reference:** [GRO-818](https://linear.app/growthwebdev/issue/GRO-818)  
**Status:** Completed

---

## 1. Summary of Individual Research Reports

This synthesis report consolidates all research, specifications, and reviews for the Prismatic Engine workspace coordination layer. The individual reports are summarized below:

*   **`AGY-briefing.md`** (Kai, 2026-06-07)  
    Briefs the AGY agent to perform an architectural review of the Prismatic Engine v1 spec. It establishes the mission goals, sets constraints (e.g., self-hosted, Linux server runtimes, active-oahu testbed), and outlines evaluation dimensions (soundness, challenges, minimal viable protocol).
*   **`agy-implementation-plan.md`** (AGY, 2026-06-08)  
    Reviews the v1 architecture, validating the hybrid locking design while uncovering major bugs: absolute path drift, local vs centralized lockfile isolation, lack of semantic dependency checks, and cron-based stale lock cleanup. It details a headless `swarm.js` adaptation strategy using lazy lock pruning and maps out a 4-phase implementation roadmap.
*   **`agy-core-boundary-validation.md`** (AGY, 2026-06-08)  
    Audits the python codebase, identifying hardcoded agent-specific references (`agy`, `jules`, `codex`) in the core dispatcher (`prismatic/dispatcher.py`) and recommending a decoupled, configuration-driven model using `PRISMATIC_ENGINE.yaml`. It also analyzes Claude Code's 12-hour self-correcting refinement loop, and discusses real-world parallelization patterns like Git Worktrees and STORM.
*   **`agy-claude-code-build-pattern.md`** (AGY, 2026-06-08)  
    Deep-dives into Claude Code's iterative build pattern, validating five hypotheses regarding task decomposition, sequential execution, self-healing compiler loops, context curation, and final reporting. It compares this loop side-by-side with Prismatic's 7-step outer orchestrator loop, suggesting features like local self-healing retries and label-driven caching checkpoints.
*   **`core-evaluation.md`** (Kai, 2026-06-07)  
    Defines the problem space of multi-agent workspace coordination, showing that dispatch alone is insufficient and workspace governance is required to avoid file/logical collisions. It divides the engine into four core subsystems (Dispatch, Governance, Visibility, Refinement) and outlines the core-vs-plugin boundary and orchestration mode switch.
*   **`fred-briefing.md`** (Kai, 2026-06-07)  
    Acts as an integration briefing for Fred (the orchestrator agent), detailing how his existing dispatch code should incorporate the governance and refinement patterns. It summarizes the bugs AGY identified, outlines the implementation phases, and maps out the legacy research and spec files to be moved.

---

## 2. Contradictions Between Reports

The following contradictions and inconsistencies were identified across the reports:

1.  **Lock Location & Schema Configuration**:  
    In `fred-briefing.md`, the template config file lists `locks: centralized: "/home/ubuntu/.antigravity/swarm_locks.json"`. However, in `agy-core-boundary-validation.md`, AGY proposes dynamic settings containing `settings: locks_dir: "/home/ubuntu/.antigravity"`. One references a specific file path while the other references a directory.
    *   **Resolution:** The configuration will explicitly define `centralized: "/home/ubuntu/.antigravity/swarm_locks.json"` to define the exact path to the JSON database.
2.  **Locking CLI Runtime Integration**:  
    `fred-briefing.md` notes that the TypeScript-based `SwarmLockManager` from the VS Code extension can either be "ported to headless Python or wrap `swarm.js`". However, AGY's implementation plan explicitly settles on the hybrid `swarm.js` CLI wrapper because it eliminates rewrite overhead and ensures instant compatibility for Node runtimes.
    *   **Resolution:** Implement the hybrid `swarm.js` CLI wrapper.
3.  **Lanes Scope Definitions**:  
    In `fred-briefing.md` (lines 88-99) and `agy-core-boundary-validation.md` (lines 209-230), the YAML structures represent slightly different agent registries. Specifically, Jules' lane in the first briefing is `[]` (read_only), but in the second it is explicitly configured with `read_only: true` and a `branch_prefix: "fix/"`.
    *   **Resolution:** Adopt the more detailed schema from `agy-core-boundary-validation.md` containing `read_only: true` for Jules.

---

## 3. Settled vs. Open Decisions

### Settled Decisions
1.  **Repository-Relative Path Keys**: Lock keys in `swarm_locks.json` must be stored as repository-relative paths resolved against the git root (using `git rev-parse --show-toplevel`) to prevent cross-workspace path drift.
2.  **Centralized Lock File**: The database file must be centralized at `/home/ubuntu/.antigravity/swarm_locks.json` instead of local per-checkout configurations.
3.  **Lazy Lock Pruning**: Stale locks will be pruned dynamically during every `lock`, `unlock`, and `status` command, avoiding external cron dependencies.
4.  **Core decoupling**: The core dispatcher must be refactored to remove hardcoded agent executable paths and labels, moving them into the dynamic `PRISMATIC_ENGINE.yaml` registry.
5.  **Sequential Task Order**: Agents must execute tasks sequentially to prevent environment build collisions and token budget bloat.

### Open Decisions
1.  **JSON vs. SQLite Lock Registry**: The current `swarm.js` relies on a simple JSON array. For high concurrency, SQLite offers ACID transaction safety, whereas JSON is prone to write-race conditions. Transitioning the CLI to SQLite remains an open design upgrade.
2.  **AST Semantic Dependency Scanning**: Implementing static AST analysis in the pre-push hook to detect transitive code breakage across lanes is defined as a long-term goal but the precise tool stack (e.g. ESLint, Pyright) is not yet settled.
3.  **Staging Deploy-On-Success Gateways**: Details of how Fred automates staging deploys and reviews after merging PRs are still being finalized.

---

## 4. AGY Recommended Roadmap

We recommend executing the implementation phases in the following order:

*   **Phase 1: Conventions & Scopes (Immediate)**  
    Deploy the declarative rules via `PRISMATIC_ENGINE.yaml` in target repositories. Update each agent's system prompt / SOUL.md with lane constraints and the `[AgentName] comment` commit message convention.
*   **Phase 2: Centralized Locking CLI (Short Term)**  
    Refactor `swarm.js` to handle repository-relative paths, centralized locking at `/home/ubuntu/.antigravity/swarm_locks.json`, lazy pruning, and a `heartbeat` command.
*   **Phase 3: Git Hook Validation (Medium Term)**  
    Implement a Python-based git `pre-push` hook that validates the agent's branch name prefix, lane boundaries, lock ownership of modified files, and commit prefixes before pushes are processed.
*   **Phase 4: Visibility Dashboard (Medium Term)**  
    Create a lightweight web dashboard showing active locks, agent status, stale warnings, and recent runs history parsed from `run_records.db`.
*   **Phase 5: 7-Step Loop & Code Decoupling (Longer Term)**  
    Refactor `prismatic/dispatcher.py` to load agents dynamically. Build the review, feedback, refinement, and integration steps along with the interactive/collaborative/autonomous mode switch.

---

## 5. Authoritative List of File Locations

Below is the definitive reference mapping where all Prismatic Engine files reside on the shared server:

### Prismatic Engine Package (`/home/ubuntu/work/prismatic-engine/`)
*   **Source Code Directory:** `/home/ubuntu/work/prismatic-engine/prismatic/`
    *   Core Dispatcher: `file:///home/ubuntu/work/prismatic-engine/prismatic/dispatcher.py`
    *   Pipeline Router: `file:///home/ubuntu/work/prismatic-engine/prismatic/router.py`
    *   Workspace Loader: `file:///home/ubuntu/work/prismatic-engine/prismatic/workspace.py`
    *   Deduplication DB: `file:///home/ubuntu/work/prismatic-engine/prismatic/dedup.py`
    *   Agent Tracker: `file:///home/ubuntu/work/prismatic-engine/prismatic/run_records.py`
    *   Hermes Adapter: `file:///home/ubuntu/work/prismatic-engine/prismatic/agents/hermes.py`
    *   Signal Providers: `file:///home/ubuntu/work/prismatic-engine/prismatic/providers/signals/`
    *   Linear Task Provider: `file:///home/ubuntu/work/prismatic-engine/prismatic/providers/tasks/linear.py`
*   **Research Folder:** `/home/ubuntu/work/prismatic-engine/research/`
    *   Multi-Agent Git Landscape: `file:///home/ubuntu/work/prismatic-engine/research/01-multi-agent-git-coordination-landscape.md`
*   **Specifications Folder:** `/home/ubuntu/work/prismatic-engine/specs/`
    *   Architecture Spec v1: `file:///home/ubuntu/work/prismatic-engine/specs/prismatic-engine-architecture-v1.md`
    *   7-Step Loop Specification: `file:///home/ubuntu/work/prismatic-engine/specs/7-step-loop-specification.md`
    *   Loop State Mock (JS): `file:///home/ubuntu/work/prismatic-engine/specs/prismatic-loop-state-machine-mock.js`
    *   Loop State Mock (TS): `file:///home/ubuntu/work/prismatic-engine/specs/prismatic-loop-state-machine-mock.ts`
    *   Flow Diagram (SVG): `file:///home/ubuntu/work/prismatic-engine/specs/prismatic_loop_flow.svg`
*   **Reports Folder:** `/home/ubuntu/work/prismatic-engine/reports/`
    *   AGY Briefing: `file:///home/ubuntu/work/prismatic-engine/reports/AGY-briefing.md`
    *   AGY Implementation Plan: `file:///home/ubuntu/work/prismatic-engine/reports/agy-implementation-plan.md`
    *   AGY Core Boundary Validation: `file:///home/ubuntu/work/prismatic-engine/reports/agy-core-boundary-validation.md`
    *   Claude Code Build Pattern: `file:///home/ubuntu/work/prismatic-engine/reports/agy-claude-code-build-pattern.md`
    *   Core Evaluation (Kai): `file:///home/ubuntu/work/prismatic-engine/reports/core-evaluation.md`
    *   Fred Briefing (Kai): `file:///home/ubuntu/work/prismatic-engine/reports/fred-briefing.md`
    *   Research Synthesis Report (This File): `file:///home/ubuntu/work/prismatic-engine/reports/agy-synthesis-report.md`
    *   Proposed SOUL Amendments: `file:///home/ubuntu/work/prismatic-engine/reports/agy-proposed-soul-amendments.md`
*   **Test Plans Folder:** `/home/ubuntu/work/prismatic-engine/test-plans/`
    *   Test Batches Spec: `file:///home/ubuntu/work/prismatic-engine/test-plans/test-batches-v1.md`

### Target Repository (Active Oahu Static)
*   **Repo Root:** `/home/ubuntu/work/active-oahu-static/`
*   **Site Directory:** `/home/ubuntu/work/active-oahu-static/site/`
*   **Central Config:** `file:///home/ubuntu/work/active-oahu-static/site/PRISMATIC_ENGINE.yaml`

### Lock Registry
*   **Central DB File:** `/home/ubuntu/.antigravity/swarm_locks.json` (configured via env var `SWARM_LOCKS_DIR`)
