---
name: orchestrator-delegation-discipline
description: Delegate multi-step work to specialized worker lanes (e.g. Jules, AGY/Antigravity, Codex) instead of doing every review and implementation step in the parent agent.
---

# Orchestrator Delegation Discipline

Use this skill when the user wants the orchestrator to behave like a true coordinator: split work across specialized lanes, keep the parent agent focused on routing and verification, and avoid doing all research/review/implementation in one context.

## When to use

- A task has multiple independent workstreams.
- The user explicitly asks to delegate work to Jules, AGY/Antigravity, Codex, or other specialist lanes.
- A review or implementation task is large enough that one context would get cluttered.
- The user prefers end-to-end rollout with clear ownership boundaries.

## Operating principles

0. **Golden thread autonomous execution**: When the user has a project registry with `next_action` fields, push forward proactively. After completing a task, immediately identify and execute the next one — don't pause to ask. Update the registry after each work session.

1. **Route work to the right lane.**
   - **Ned (primary executor):** The WORKHORSE. Ned handles the heavy lifting that Fred would normally do — code implementation, module extraction, refactoring, verification. Ned runs on `openai-codex` / `gpt-5.5` (OAuth, primary) with `deepseek-v4-pro` fallback. Ned picks up `agent:ned` Linear issues. **After completing work, Ned posts results as a Linear comment on the issue (NOT to chat), swaps label `agent:ned` to `agent:fred`, and moves on.** Ned's cron job delivers to `local` (silent) to avoid cluttering Fred's chat. Ned is the DEFAULT executor — if Fred finds himself about to do implementation work, he should create an `agent:ned` issue instead and let Ned handle it.
   - **Fred (reviewer/orchestrator):** Reviews Ned's work, integrates PRs, handles orchestration, routing, and deployment. Fred's `agent:fred` label means "review needed" — Ned's completed work lands here for Fred to verify and merge. Fred does NOT do implementation work directly when Ned can handle it. The lane split: Ned EXECUTES, Fred REVIEWS.
   - Jules: async PR-producing implementation, docs, schemas, runbooks, bounded repo changes. **Also: research, interviews, knowledge synthesis, code review, documentation — Jules can act as ANY role when given a goal + repo + output format.** Jules is GitHub-native: always work through a repo. Frame tasks as goals with context, parameters, and deliverables. **Warning: Jules produces patches against a base commit — if another agent modifies the same files between Jules' base and HEAD, patches will fail to apply.** Prefer Jules for work on files that won't be concurrently modified, or run Jules sessions sequentially for files with shared dependencies. **When Jules patches go stale (line numbers shifted by scaffolding commits, file moves, etc.), don't fight the patches — route the work to Ned. Ned works against the current HEAD directly and doesn't suffer from base-commit drift.** Example: darius-star — 9 Jules extraction sessions completed but patches failed after AGY's scaffolding moved files and updated script tags. Ned re-extracted all 7 modules in 2 ticks.
   - AGY/Antigravity: broad research, synthesis, cross-source context gathering, **image interpretation when the orchestrator lacks vision**, **site design/layout/UX direction before Fred builds**, **foundational audits and gap analysis**. For pure research tasks, launch AGY from `/tmp` with NO `--add-dir` to prevent code-building instinct.
   - Codex/local agent: interactive local debugging, deterministic verification, conflict resolution.
   - **AGY-design → Fred-build**: When building brand sites or pages, AGY produces layout descriptions, visual direction, content architecture. Fred implements in Astro. AGY does NOT need to write code — clear design direction is sufficient.
   - **Next Step protocol**: When the user is blocked by a dashboard toggle or short human action, give them ONE baby step while dispatching AGY/Jules on parallel research/code tasks. Never idle while the user does their step.
   - **🔑 AGY delegation rule (from AGY itself): Delegate goals, not tasks. Let AGY run the full local loop (edit → build → verify) before anything hits staging. Never micromanage with "change line 15's color" — give the desired outcome and let AGY own the implementation.** See `agy-delegate-goals-not-tasks` skill for the full pattern.
   - **🔑 Jules delegation rule (from Jules itself, Jun 2026): Always work through a repo. Frame as GOAL with CONTEXT, PARAMETERS, OUTPUT. Include "DO NOT wait for approval." Chain sessions with prior session IDs. Send "Facts already known" JSON. Jules is an Action Engine, not a Search Engine — give it concrete targets, not open-ended exploration.** See `jules-cli-operating-playbook` skill for the full pattern.

2. Make the delegated worker identity explicit.
   - State the worker's identity, role, and persona in the prompt.
   - Identity answers "who are you in this task?"
   - Role answers "what lane owns this?"
   - Persona answers "how should you operate?"
   - This improves output quality and reduces generic or evasive responses.

3. Use a machine-readable control plane when orchestration has real state.
   - If the task involves routing, escalation, timeouts, spawn rules, or multiple active lanes, keep that policy in a manifest or equivalent structured artifact.
   - Keep the manifest, schema, workflow docs, and operator handbook in lockstep.
   - When the rules are still implicit, compile them into a canonical docs set first (playbook, state machine, research-lane contract) before adding more research or behavior.
   - Enforce the contract in CI so governance failures are caught mechanically, not by memory.
   - See `references/orchestration-governance-control-plane.md`, `references/orchestration-docs-control-plane.md`, and `references/local-agent-status.md` for the durable pattern.

4. Verify local-agent status from the runtime control plane before assuming a worker is unavailable.
   - Prefer `hermes profile list` / profile metadata for Hermes-managed local workers.
   - Use the scheduler/session/process view to distinguish "stopped", "running", and "no background session exists".
   - Treat profile status as the first source of truth for agent availability; do not infer availability from a missing terminal session alone.

5. Keep each subtask self-contained.
   - Include repo path, relevant files, constraints, and expected deliverables.
   - Tell the worker what not to touch.
   - Avoid vague prompts like "look into it."

6. Use parallelism only for truly independent work.
   - Do not assign multiple workers to the same file set unless the goal is comparison.
   - Prefer one lane per independent artifact or question.

7. Treat worker "done" claims as provisional until verified.
   - If a worker says a file was written, a PR was opened, or a report was produced, verify it with an independent system check, another agent, or a direct artifact lookup before telling the user it succeeded.
   - Do not let a worker audit its own claimed result.
   - If the evidence is missing or incomplete, rerun, narrow, or reassign the task instead of accepting the claim.

8. **AGY audit → Ned build pipeline (proven Jun 2026):** For greenfield module builds, AGY researches the codebase (read-only) and produces a structured spec document with exact function signatures, dependency graphs, and global variable contracts. Ned then implements from that spec. This is faster than having Ned discover the contracts himself, and more reliable than having Jules build against a moving codebase. The audit doc serves as the source of truth that both agents align on. Example: darius-star — AGY produced `docs/foundational-structure-audit.md` (260 lines, all 5 missing modules fully specified with exact signatures). Ned read that audit and built `save_system.js` (279 lines, all 9 CampaignSave methods) in one 5-minute cron tick.

### AGY Post-Implementation Audit Pipeline (NEW Jun 2026)
After Ned completes a large body of work (10+ commits across multiple modules), use this pipeline to verify quality:

1. **AGY audits Ned's work** — Give AGY a goal: "Audit all of Ned's recent commits. Read the git log, cross-reference against prior bug reports, verify fixes in the actual code. Produce structured reports." AGY runs headless (`--print` mode, 600s timeout). Output: bug verification matrix, module health scores, game state assessment, prioritized action list.
2. **Fred creates a Linear issue** — One issue labeled `agent:ned` pointing Ned at all the reports: "Ned: Read AGY audit reports → execute priority action list." Description includes key findings and a pointer to the most actionable report.
3. **Ned reads and executes** — Ned picks up the issue FIFO, reads the reports, and works through the priority actions in order, committing and commenting as he goes.
3a. **Verify before executing (CRITICAL — Jun 2026):** Before applying any fixes from the audit report, verify each Priority 1 & 2 item against the current master code. A prior Ned cron session may have already applied the fixes and closed the follow-up Linear issues — but left the parent audit issue un-cleaned. The signal: child issues (GRO-1185/1186/1187) are Done while the parent persists in Backlog with stale labels. **Verification pattern:** for each fix in the priority action list, grep the relevant source file and confirm the fix pattern exists. If all Priority 1 & 2 items are already fixed: post a verification comment, move the issue to In Progress with `agent:fred`, and move on. Do NOT re-apply or re-commit already-fixed code. Only execute the remaining unfixed items (usually P3/P4 — asset generation, polish). Example: GRO-1184 (Jun 11) — AGY produced 4 reports + 3 follow-up issues (all Done). Ned verified all 7 Priority 1 & 2 fixes were already in master via grep checks on `ui.js` (LevelManager progression), `sprites.js` (boss path + race condition), `parallax.js` (setKey suffix strip), `combat.js` (ScrapDrop + window bindings), `enemies.js` (window bindings). No code changes needed — verification + label/state cleanup only.

This pipeline catches issues that commit-message scanning misses — AGY actually reads the code and finds root causes (e.g., a function that clears boss state but never advances level progression).

8. Finish with a coherent synthesis.
   - Consolidate worker outputs into one clear answer.
   - Report remaining risks, exact files changed, and next concrete step.

## How delegate_task actually works (what Michael sees)

**While subagents run, the parent agent is BLOCKED.** The parent gets zero intermediate output — no tool logs, no thinking traces. It's a black box until all subagents finish or timeout (10 min each). If the user sends a message mid-delegation, subagents are **cancelled** and the parent responds to the user instead.

This means:

- **✅ Use delegation for:** large parallel independent workstreams (audit site A + index media B simultaneously), tasks with heavy tool-call volume that would flood the parent's context, research synthesis that benefits from isolated reasoning.
- **❌ Use direct execution for:** critical-path fixes (database crash, auth error), tasks where you need to course-correct mid-flight, anything requiring user interaction, or single-command operations where delegation overhead exceeds the work.
- **Expect 2-6 minutes of silence** when delegation is in flight. The parent is watching a progress bar, not doing nothing.
- **Subagents run in isolated contexts:** they have their own terminal sessions, no memory of the parent's conversation, and cannot call `clarify`, `memory`, or `delegate_task` themselves. Pass ALL relevant context (paths, constraints, error messages) in the `context` field.

## Suggested workflow

1. Identify the workstreams.
2. Assign each workstream to the best lane.
3. Provide each worker with the minimal sufficient context.
4. Wait for results or bounded completion.
5. Verify any side effects.
6. Merge findings into one final response.

## Pitfalls

- Doing all the work yourself instead of delegating.
- Overloading one worker with unrelated tasks.
- **AGY audit → Ned pipeline: label accumulation (CRITICAL — Jun 2026):** When the AGY→Ned→Fred pipeline runs across multiple cron ticks, labels can accumulate on the parent audit issue. GRO-1184 was found with `agent:ned` + `agent:fred` + `agent:done` simultaneously — all three labels stacked because agents added their label without querying and removing the prior one. The `labelIds` mutation is a SET operation (replaces the full array), so each label swap MUST query current labels first, build the new array by splicing out old + splicing in new, then update. Never assume the issue has only one agent label. When Ned picks up an AGY audit issue, always query current labels — if `agent:done` is present alongside `agent:ned`, the issue was prematurely stamped. Remove `agent:done` and `agent:ned`, keep `agent:fred`, and move to In Progress for Fred's final review. Do NOT close directly — AGY audit issues require Fred's sign-off on the verification.
- **Dispatcher dedup poison (CRITICAL):** The dispatcher script (`agent_dispatcher.py`) must call `mark_issue_dispatched()` AFTER a successful launch, not before. Marking before launch means any failure (no-op signal, connection error, missing launcher) permanently blocks the issue for the TTL window (60 min). When ALL new issues show `🔄 Already dispatched ... skipping (dedup)`, the dedup DB is poisoned. Fix: clear `/home/ubuntu/.hermes/profiles/orchestrator/state/event-router/router.db` (`DELETE FROM processed_events`), verify the dispatcher script has dedup AFTER launch, and re-fire the dispatcher. Root cause fix was applied June 11, 2026.
- Spawning overlapping workers against the same files.
- Turning a bounded review into an open-ended loop.
- Adding more research or implementation before the control-plane docs are readable and linked from the repo anchors.
- **Massive combined prompts**: when the user has multiple corrections or tasks, do NOT bundle them into one giant prompt. Break them into surgical, single-focus injections — one module, one fix, one concept per delegation. This user explicitly prefers separate, highly precise prompts over monolithic dumps. Loading a worker's context window with 3 unrelated fixes at once confuses their active focus and risks partial execution.
- **Subagent timeout ≠ task impossible**: when a subagent times out (600s), it's often an approach problem, not a capability problem. Large datasets (e.g. 698GB media, 9,592 files) choke on exhaustive processing. Self-implement a simpler heuristic version — folder tagging instead of EXIF parsing, inventory JSON instead of filesystem walk. See `references/batch-delegation-patterns.md` for the timeout recovery pattern.
- **Asking the user before exhausting tool-based approaches (CRITICAL):** The user expects you to try EVERY available automated approach before asking them to do something. If the API is broken, try the CLI. If the CLI is broken, try AGY's browser. If AGY can't log in, try an alternative API endpoint. Only ask the user to click something in a dashboard after you've exhausted all automated paths AND can explain exactly what failed and why. When you do ask, be surgical ("click this specific button") not vague ("check the dashboard"). The user's time is the most expensive resource — burn your own compute first.
- **AGY invocation mode selection (CORRECTED Jun 8, 2026):** There are TWO modes and picking the wrong one wastes sessions:
  - **`--print` mode**: For headless research, bounded tasks, and one-shot work. Works WITHOUT PTY. Use direct foreground invocation: `agy --print "goal" --dangerously-skip-permissions --print-timeout 10m --add-dir /path 2>/dev/null > output.log`. This is the DEFAULT for all delegated AGY research. Verified working in `references/agy-headless-invocation-research.md`.
  - **`--prompt-interactive` mode**: For interactive TUI sessions where commands are TYPED into the interface. Passing `/goal` as a CLI argument does NOT work — the TUI ignores it and waits for interactive input. Only use when you genuinely need a live follow-up session.
  - **Common failure**: Using `--prompt-interactive` with a `/goal` CLI argument → AGY sits at the TUI prompt doing nothing for 300s. Both AGY sessions in this session failed because of this.
  - **Never** use `terminal(background=true)` for AGY — causes SIGTERM 143. Always foreground with `--print` and generous timeout (300s).
  - **AGY in delegate_task — hangs on pipe_read (NEW Jun 2026):** AGY's `--print` mode blocks on stdin when run through `delegate_task` subagents. The subagent's process model doesn't provide stdin the way AGY expects, causing it to hang in `S+ pipe_read` state indefinitely (zero output, never reaches auth phase). **Workaround:** run AGY directly via `terminal()` in foreground mode from the parent agent. If the task exceeds 600s foreground limit, implement it directly rather than fighting AGY's subprocess incompatibility. Two sessions (PID 898332, 899770) hung for 195s+ each before this was identified.
  - **Signal agents skip dispatcher dedup (NEW Jun 2026):** The dispatcher's dedup mechanism is only meaningful for process-spawning agents (AGY, Jules, Codex) where double-launching creates duplicate processes. Signal-mode agents (Fred, Ned, Kai, Autobot) self-manage via their own cron jobs — re-signaling is harmless (nudge files just overwrite). The dispatcher was updated to skip dedup entirely for signal agents. See `references/dispatcher-dedup-recovery.md` for the full dedup architecture and recovery procedure.
- **Prefer existing research over re-running AGY:** The research directory often already contains completed work from prior sessions. Before dispatching AGY to "research 50 businesses" or "draft a proposal template," check `~/work/research/ai-consulting/` and `~/work/ai-consulting/` for existing output files. In one session, AGY spent 16+ minutes on a pilot proposal template that had already been generated the day before.

- **Research subagents time out on large undirected tasks — give smaller chunks (CRITICAL):** When delegating research tasks to subagents (e.g., "research 50 Idaho businesses"), the subagent often times out at 600s with 28+ API calls and zero output. The web scraping approach is too broad. Michael's guidance: "give AGY smaller chunks. Break up the research into multiple terminals and chunks." Instead of "research 50 businesses," delegate 3 subagents each researching 15-17 businesses in specific industries. For the Hawaii outreach list, the subagent succeeded (457s, 23 API calls) because it used the `ddgs` Python library (DuckDuckGo Search) for targeted queries rather than broad scraping. **Pattern:** When a research subagent times out: (a) check if output files were partially produced, (b) break the task into smaller industry-specific chunks, (c) prefer the `ddgs` library or targeted API queries over broad web scraping.

## Good handoff prompt pattern

"Review / implement / research X in repo Y. Ignore unrelated files. Focus on Z. If you find a concrete issue, make the smallest local edit needed. Return a concise verdict with exact file paths and any fix applied."

## Verification checklist

- Did each worker get a distinct, bounded task?
- Did the prompt name the repo and relevant files?
- Did I choose the correct worker lane?
- Did I verify any claimed file changes?
- Did I synthesize the results without duplicating the worker's work?

## Supporting references

- `references/batch-delegation-patterns.md` — proven patterns for batching 3 parallel subagents, task categorization, context-passing discipline, timeout recovery, and session throughput cadence.
- `references/linear-task-series-creation.md` — Python subprocess + curl pattern for programmatically creating parent task + subtask series on Linear via GraphQL API. Use instead of `execute_code` which fails on nested JSON escaping.
- `references/signal-provider-architecture.md` — how the dispatcher delivers work signals to agent lanes via swappable backends (file, HTTP, Redis, Telegram). The transport layer under delegation.
- `references/darius-star-multi-agent-pipeline.md` — end-to-end case study: AGY audit → Ned build → Jules tools/docs pipeline, Jules patch staleness recovery, placeholder stub pattern, OAuth exhaustion fix, cron delivery routing.
- `references/agy-asset-generation-pipeline.md` — AGY sprite/portrait/background generation via parallel delegate_task subagents. Chunk size limits (3-4 images per session), verification pattern, pitfalls.
- `references/agy-research-to-revenue-tasks.md` — turning AGY strategic research (CRO audits, backlink strategy, MSP prospecting) into executable Linear issues with expected impact metrics.
