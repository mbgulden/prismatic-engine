# Standalone Command Center & Plugin Workspaces

> **North star:** `install engine → get value → attach capabilities as needed → detach without losing state`.

This document captures Michael's direction on June 18, 2026: the Hermes dashboard plugins we created should be re-evaluated and rebuilt as standalone **Prismatic Engine** command-center plugins, while remaining attachable to Hermes, OpenClaw, AGY-only workflows, local GPU stacks, Google tools, and future harnesses. Each major plugin should be its own GitHub workspace, not a hardwired folder inside the hub.

## Why this matters

The old `Antigravity-Orchestration-Hub` repo was aiming at the right product experience:

- command center / dashboard
- real-time agent activity visibility
- task graph visibility
- workflow history visibility
- handoff / yield loops
- AGY ↔ Jules workflow orchestration
- persona/scope routing for Jules
- skill/methodology routing for AGY
- standardized tests and reliability scoring
- self-optimization from workflow outcomes
- hardware/server-network visibility via SovereignSentinel-style AIOps

The mistake would be to bind that whole dream to one harness, one IDE extension, one local daemon, or one plugin folder. The Prismatic version should keep the dream while changing the substrate:

- **Prismatic Engine core owns state, contracts, events, adapters, and durable history.**
- **Prismatic Command Center is a standalone UI shell backed by engine APIs.**
- **Plugins are separate workspaces/repos that register capabilities into the command center.**
- **Harnesses can embed or link to the command center, but do not own it.**

---

## Product Architecture

```text
Prismatic Engine Core
  ├─ task/run/journal state
  ├─ event bus / telemetry stream
  ├─ provider registry: AGY, Jules CLI, Claude CLI, Codex CLI, local models, GCP
  ├─ harness registry: Hermes, OpenClaw, none
  ├─ scheduler adapters: systemd, cron, Hermes cron, OpenClaw scheduler
  ├─ plugin registry
  ├─ workflow test/evaluation records
  └─ import/export bundle

Prismatic Command Center
  ├─ standalone web app/API service
  ├─ dashboard shell
  ├─ plugin loader
  ├─ real-time event stream
  ├─ workflow graph views
  ├─ task/run/history views
  ├─ capability attach/detach UI
  └─ action surfaces guarded by engine contracts

Plugin Workspaces
  ├─ prismatic-plugin-task-graph
  ├─ prismatic-plugin-activity-stream
  ├─ prismatic-plugin-agent-control-deck
  ├─ prismatic-plugin-workspace-tree
  ├─ prismatic-plugin-lock-dashboard
  ├─ prismatic-plugin-mcp-controller
  ├─ prismatic-plugin-vram-observability
  ├─ prismatic-plugin-sovereign-sentinel
  └─ future add-ons

Optional Harnesses
  ├─ Hermes
  ├─ OpenClaw
  ├─ AGY-only terminal mode
  ├─ Claude/Codex/Jules CLI loops
  └─ future systems
```

The command center should be able to run as:

1. **Standalone:** `prismatic command-center serve`.
2. **Embedded in Hermes:** Hermes dashboard tab points to or proxies the standalone command center.
3. **Embedded in OpenClaw:** same contract, different host.
4. **Headless:** no UI, just engine APIs/events/CLI.

---

## Existing Hermes Plugin Re-Evaluation Matrix

These plugins should be re-evaluated, renamed, and extracted into standalone Prismatic plugin workspaces. The Hermes versions become compatibility shells.

### `hermes-plugin-prismatic-hub`

**Prismatic target:** `prismatic-command-center` or `prismatic-plugin-command-hub`

**Purpose:** The main cockpit: capability status, dispatcher state, task queues, workflow health, attach/detach controls.

**Extraction rule:** This should become the standalone shell, not remain a Hermes plugin.

### `hermes-plugin-orchestrator-command-deck`

**Prismatic target:** `prismatic-plugin-agent-control-deck`

**Purpose:** Start/pause/halt agents, trigger workflows, inspect provider/harness health, dispatch one-off commands.

**Required contracts:** action requests must flow through Prismatic Engine policy checks. No direct arbitrary shell command execution from UI.

### `hermes-plugin-realtime-activity-stream`

**Prismatic target:** `prismatic-plugin-activity-stream`

**Purpose:** Real-time SSE/WebSocket feed of task events, provider output, handoffs, errors, validation gates, and human interventions.

**Legacy hub inspiration:** `Report-2_Realtime_Swarm_Visualization.md` recommended push-based streaming rather than 2s polling. Keep that.

### `hermes-plugin-swarm-manager`

**Prismatic target:** `prismatic-plugin-swarm-manager`

**Purpose:** View active agents, sessions, claims, workspace context, and execution lanes.

**Extraction rule:** It should read from engine run/session/task records. Harness-native sessions are imported/linked as external session references, not treated as the only truth.

### `hermes-plugin-workspace-tree-navigator`

**Prismatic target:** `prismatic-plugin-workspace-tree`

**Purpose:** Browse configured engine workspaces, preview safe files, download artifacts, inspect repo status.

**Required contract:** same workspace allowlist and blocklist as `prismatic-publish`; never arbitrary filesystem browsing.

### `hermes-plugin-lock-dashboard`

**Prismatic target:** `prismatic-plugin-lock-dashboard`

**Purpose:** View file locks, owners, TTL, stale locks, lane conflicts, and pre-push governance state.

**Required contract:** consumes engine lock registry, not Hermes profile state.

### `hermes-plugin-vram-observability`

**Prismatic target:** `prismatic-plugin-compute-observability`

**Purpose:** GPU status, local model endpoints, VRAM usage, model routing health, failover state.

**Extension point:** should integrate with local GPU provider registry and SovereignSentinel hardware telemetry when attached.

### `hermes-plugin-mcp-controller`

**Prismatic target:** `prismatic-plugin-tool-server-controller`

**Purpose:** MCP/tool server inventory, health, logs, restart/test actions.

**Rule:** MCP is one tool-server kind. Do not make the plugin MCP-only if the engine later supports other tool-server protocols.

### `hermes-inbox` / prompt intake concepts

**Prismatic target:** `prismatic-plugin-inbox`

**Purpose:** Intake screenshots, logs, prompts, files, and turn them into engine artifacts/tasks/journal events.

**Rule:** saved intake becomes engine state/artifacts, not harness-local uploads.

### `kanban`

**Prismatic target:** `prismatic-plugin-kanban` or task-board view inside command center.

**Purpose:** Human-friendly board over engine tasks/runs. If Hermes Kanban remains, it syncs through adapter/import/export.

---

## Antigravity-Orchestration-Hub Concepts to Preserve

The old hub was not just a dashboard; it encoded workflow discipline. Preserve the concepts, not the coupling.

### 1. Explicit yield / handoff API

Legacy pattern:

```bash
curl -X POST http://127.0.0.1:5001/api/swarm/yield -d '{"status":"success"}'
```

Prismatic target:

```bash
prismatic workflow yield --run <run_id> --status success --artifact decision_log.md
# or
POST /api/workflows/{run_id}/yield
```

Why: filesystem/token watching is brittle. Explicit handoff events are durable and inspectable.

### 2. Decision log as handoff vector

Legacy hub idea: before handing off to Jules, AGY/Antigravity writes `.agents/artifacts/decision_log.md`; Jules gets both code diff and reasoning context.

Prismatic target:

```text
run-record/
  decision_log.md
  diff.patch
  tests.json
  artifacts.json
  handoff.json
```

Jules CLI (jules.google.com) should review not just code, but the decision path, scope, constraints, and verification evidence.

### 3. Real-time push stream

Legacy report recommended WebSocket/Socket.IO over polling.

Prismatic target:

- `/api/events/sse`
- `/api/events/ws`
- event schema: `task.created`, `run.started`, `provider.output`, `lock.acquired`, `handoff.yielded`, `review.completed`, `test.failed`, `skill.updated`, `human.intervention`.

### 4. Workflow graph / DAG view

Legacy hub imagined ReactFlow DAGs for decomposition and animated handoff edges.

Prismatic target:

- every workflow run has a DAG
- nodes: tasks, agents, providers, reviews, tests, publish steps
- edges: handoffs, dependencies, blockers, retries
- UI animates active edges from event stream

### 5. AGY/Jules split-brain orchestration

Legacy hub pattern:

- AGY/local Antigravity does local implementation and context gathering.
- Jules operates against pushed GitHub state.
- Git is the hard state handoff.
- Jules must get a prompt containing scope, branch, diff, decision log, tests, and persona.

Prismatic target:

```text
AGY run → commit/checkpoint → push branch → Jules CLI session → pull/review results → AGY fix loop → validation → merge gate
```

### 6. Suggested tasks / proactive triage

Legacy idea: fetch Jules suggested tasks and approve those compatible with project rules.

Prismatic target:

- `prismatic suggestions scan --provider jules`
- suggestions become engine tasks in `proposed` state
- policy engine scores scope/risk/value
- human or rule promotes to Todo

### 7. Scheduled maintenance

Legacy idea: codified `.jules/schedules.yml` for dependency healing and security scans.

Prismatic target:

- scheduler adapter owns cadence
- task provider records maintenance tasks
- Jules/AGY/Claude/Codex are interchangeable lanes depending on capability and cost

### 8. Standardized tests and self-optimization

Michael's direction: track and optimize workflow loops based on standardized tests; produce better results over time; dynamically build AGY skills.

Prismatic target:

```text
workflow template
  → run
  → standardized tests
  → reviewer verdict
  → outcome score
  → failure taxonomy
  → routing/prompt/skill update proposal
  → human/AGY review
  → portable skill patch
```

The engine should not silently rewrite skills. It should create skill update proposals with evidence, then apply them through the skill lifecycle.

---

## Google Tools / AGY / Jules Seamless Loop Requirements

The first-class Google lane should work before adding the rest of the world.

### Required providers/capabilities

- `agy-cli`: local Google Antigravity CLI task runner.
- `agy-chat`: interactive chat surface backed by the local/server AGY CLI instance.
- `jules-cli`: Jules CLI (jules.google.com) cloud review/implementation loop.
- `google-antigravity-sdk`: media/asset/research layer where applicable.
- `gcloud` / GCP / Vertex: optional cloud platform adapter.
- GitHub API: Programmatic connection via the GitHub API (verified via `prismatic doctor`) is a required first-class capability for the AGY + Jules workflow. It ensures that the autonomous implementation and review loops can create branches, open pull requests, retrieve diffs, fetch check statuses, and post review comments/decisions programmatically.

### AGY chat capability after Google attach

Michael's explicit product gap: when he only had Google AI Ultra / Antigravity IDE, the advanced Google tooling clearly used the AGY engine underneath, but there was no clean **chat-like interface** for talking to AGY as a connected agent. Prismatic must not repeat that gap.

When a user connects AGY / Google AI Ultra, they should be able to immediately start chatting with AGY through either:

- the standalone Prismatic Command Center, or
- a chat adapter such as Telegram that routes to the AGY instance on the same server/computer running Prismatic Engine.

Target flow:

```text
User message
  → Prismatic chat gateway / command center
  → provider session: agy
  → AGY CLI on the Prismatic host
  → streamed/recorded response
  → Prismatic chat/run/session history
  → reply back to UI or Telegram
```

This is distinct from background task dispatch. The expected first-run experience is: attach Google/AGY → open chat → talk to AGY. Session state, artifacts, selected workspace, model/profile, and transcript history belong to the engine. Telegram, Hermes, Slack, or another harness are adapters only.

### AGY TUI parity and multi-instance phone control

The chat surface should preserve the useful capabilities of the AGY TUI rather than becoming a thin prompt box. The implementation may be a re-styled/remote-safe TUI surface, but the product requirement is capability parity where practical:

- create, name, pause/resume, and stop multiple AGY instances/sessions
- route each instance to a workspace, branch, model/profile, and task scope
- inspect live output, transcript, tool/activity stream, logs, artifacts, and pending approvals
- send follow-up messages to a specific AGY session from phone or web
- promote a chat into a durable task/run, or attach a task/run to an existing chat
- hand off between autonomous mode and interactive steering without losing context
- expose safe controls from mobile: continue, summarize, checkpoint, push, ask reviewer, stop, archive

The differentiated product is not only “chat with AGY.” It is **chat with and control many AGY instances from your phone while they work autonomously from your task management system of choice**.

For a detailed strategy and implementation roadmap on this phone-first control surface, see the [phone-first-command-surface.md](phone-first-command-surface.md) strategy document.

### Task management integration boundary

Linear is the current task manager, but the engine should not hardcode Linear as the spine. Task management belongs behind adapter/plugin contracts:

- Linear adapter: current default because it is working and cheap.
- GitHub Issues / Projects adapter: likely common open-source path.
- Jira / Asana / Trello / Notion / ClickUp adapters: future integrations if demand appears.
- AI-focused task managers: future provider/plugin category.

A “Linear clone” is worth noting, but **not as core spine work**. It should be a future optional plugin/workspace only if external task tools become a bottleneck, pricing risk, offline need, or distribution constraint. Until then, Prismatic should define the task/run contract and let existing task software plug in.

Potential future plugin/workspace:

```text
prismatic-plugin-task-manager
  → local-first task board over Prismatic task/run records
  → optional sync adapters to Linear/GitHub/Jira/etc.
  → not required for first useful Prismatic install
```

### Schedule Observatory / Schedule Control Plane

Google AGY and Jules CLI (jules.google.com) have their own `/schedule` or scheduled-work systems. Prismatic should provide one real-time visibility surface for scheduled intelligence across all providers, not just local cron.

The command center should visualize:

- Prismatic-owned schedules: cron, systemd timers, native Prismatic scheduler jobs, Hermes cron when attached
- AGY-owned schedules: AGY `/schedule` entries and scheduled AGY jobs
- Jules-owned schedules: scheduled work that lives on Jules.google.com
- task-manager-owned schedules: recurring Linear/GitHub/Jira/Notion/etc. work where adapters support it

Target UX:

```text
Command Center → Schedules
  → unified calendar / timeline / table
  → provider filter: Prismatic / AGY / Jules / task manager
  → live states: next run, fired, running, completed, failed, changed
  → action: edit local schedule OR ask owning provider to update
  → watch normalized schedule record update in real time
```

Control boundary:

- **Prismatic-owned schedules:** edit directly through Prismatic contracts.
- **AGY-owned schedules:** route change requests through AGY chat/control, e.g. “ask AGY to update this `/schedule`,” then observe the imported schedule state change.
- **Jules-owned schedules:** likely read-only/deep-link first because schedule state lives on Jules.google.com; mutate only if Jules CLI/API exposes a safe path.

The engine owns normalized schedule records and events, not necessarily the remote provider’s source of truth. This preserves visibility without lying about control.

Events to model:

- `schedule.discovered`
- `schedule.created`
- `schedule.updated`
- `schedule.deleted`
- `schedule.fired`
- `schedule.running`
- `schedule.completed`
- `schedule.failed`

### Minimum loop

```text
1. Engine creates task/run.
2. AGY CLI executes implementation in workspace.
3. AGY writes decision log and verification evidence.
4. Engine checkpoints and pushes branch.
5. Engine starts Jules CLI session with branch + decision log + persona + scope.
6. Engine tracks Jules session state.
7. Engine pulls Jules output or links PR/review artifacts.
8. Engine routes fixes back to AGY or another provider.
9. Engine runs standardized tests.
10. Engine records outcome score and skill/framework lessons.
```

### Persona/scope routing

Jules CLI should not be generic. It needs job-specific personas/scopes:

- reviewer-only
- security reviewer
- dependency maintainer
- UI/visual reviewer
- docs reviewer
- test-gap reviewer
- refactor implementer
- release validator

Each persona has:

- allowed files or repo scope
- read/write mode
- expected artifacts
- timeout/budget
- required verification
- merge authority rules

### Failure states to model explicitly

- AGY timeout with no artifact
- AGY exit 0 but missing required files
- Jules cannot see unpushed local state
- Jules session created but no PR/review output
- Jules output conflicts with local branch
- provider auth expired
- test failure after provider claims success
- skill update proposed without evidence

---

## SovereignSentinel as Bolt-On Plugin Workspace

SovereignSentinel should not be part of Prismatic core. It should be a plugin/add-on workspace.

**Target repo:** `prismatic-plugin-sovereign-sentinel` or keep `SovereignSentinel` and add a Prismatic manifest.

**Capabilities from current repo:**

- network snapshots across Proxmox nodes
- L2/L3 topology data
- thermal/GPU/ZFS/SMART telemetry
- `/api/live` telemetry feed
- `/api/intents` pending human/physical infra intents
- `/api/agent/tools` OpenAI-compatible action schemas
- guarded action endpoints: `gpu_purge`, `fan_overdrive`, `zfs_heal`, `kernel_logs`, `k3s_drain`

**Prismatic plugin role:**

- register hardware telemetry stream
- register safe action tools with policy gates
- surface server/network visualization in Command Center
- convert intents into Prismatic tasks
- attach hardware events to workflow routing, e.g. "GPU down → avoid local model route"

**Hard safety rule:** physical infrastructure actions require stricter policy than code edits. Rate limits, auth token checks, dry-run modes, and human confirmation thresholds must be engine-level policy, not just UI buttons.

---

## Separate Plugin Workspace Standard

Each major plugin should live in its own separate GitHub workspace/repo, not inside the command center repo.

### Required layout

```text
prismatic-plugin-example/
  PRISMATIC_PLUGIN.yaml
  README.md
  package.json              # if frontend exists
  src/
  backend/                  # optional API bridge
  tests/
  docs/
  skills/                   # optional portable skills contributed by plugin
  examples/
```

### Required manifest

```yaml
schema_version: "1.0"
id: prismatic-plugin-activity-stream
name: Activity Stream
version: 0.1.0
engine_version: ">=0.1.0"
kind: dashboard-plugin
entrypoints:
  frontend: dist/index.js
  backend: prismatic_plugin_activity_stream.server:app
capabilities:
  events:
    consumes: ["*"]
  actions: []
permissions:
  workspaces: read-only
  actions: none
```

### Plugin categories

- dashboard plugin
- provider adapter
- harness adapter
- scheduler adapter
- hardware/telemetry plugin
- skill pack
- workflow template pack

---

## Command Center Views

Minimum standalone dashboard views:

1. **Home / Capability Map** — what is installed, attached, missing, degraded.
2. **Task Board** — local tasks, Linear/GitHub/Jules tasks, status, priority.
3. **Workflow Graph** — DAG of current and historical workflows.
4. **Activity Stream** — real-time events and logs.
5. **AGY Chat** — interactive chat with attached AGY / Google AI Ultra provider, with workspace/model/session controls and transcript history.
6. **Schedules** — unified real-time view of Prismatic cron/systemd jobs, AGY `/schedule`, Jules scheduled work, and provider/task-manager recurring events, with owner-aware controls.
7. **Agent Control Deck** — start/pause/halt/resume/provider actions.
8. **Run History** — run records, artifacts, decision logs, test evidence.
9. **Skill Evolution** — skill proposals, patches, usage, outcome metrics.
10. **Provider Health** — AGY/Jules/Claude/Codex/local models/GCP.
11. **Locks & Lanes** — workspace governance and collision prevention.
12. **Workspace Tree** — safe browsing and artifact publishing.
13. **Compute / GPU** — VRAM, model endpoints, failover, local servers.
14. **SovereignSentinel** — server network visualization add-on when installed.

---

## Documentation Promise

Michael explicitly asked: "please make sure you are documenting all this along the way so we aren’t losing all my directions."

Therefore, every architecture-affecting conversation should produce at least one of:

- doc update
- implementation plan
- skill patch
- issue/task artifact
- schema update

For this conversation, the durable artifacts are:

- `docs/standalone-command-center-and-plugin-workspaces.md`
- `specs/implementation-plans/standalone-command-center-plugin-extraction-plan.md`
- `prismatic-engine-operations` skill patch

---

## North-Star Summary

The standalone command center is the Prismatic form of the old Antigravity Orchestration Hub dream:

- same ambition
- better substrate
- no harness lock-in
- no plugin lock-in
- provider/capability driven
- real-time and historical visibility
- AGY/Jules loops first-class
- local models and hardware plugins attachable
- every plugin independently replaceable
- detach anything without losing state

Keep the sentence:

```text
install engine
get value
attach capabilities as needed
detach without losing state
```

---

## Engine v0.1 Land — Capability-Registry-Driven Foundation

**Status (2026-06-18):** Landed on branch `feature/capability-vcs-github`.

The engine is now a **capability-registry-driven, mode-switch-gated, schedule-aware, doctor-verifiable, GitHub-API-required** autonomous agent engine. v0.1 means: every contract referenced in this doc has a real implementation under `prismatic/`, every test passes (146+ green), and the dispatcher wires the gate stack end-to-end.

### What v0.1 includes (concrete surface)

| Component | Path | Status |
|---|---|---|
| Capability registry | `prismatic/capabilities/registry.py` | ✅ landed |
| `vcs.github` capability | `prismatic/capabilities/vcs_github.py` | ✅ landed |
| `vcs.github` provider | `prismatic/providers/github.py` | ✅ landed |
| `agy` provider (Ollama as first sibling) | `prismatic/providers/ollama.py` | ✅ landed |
| Mode switch (interactive/collaborative/autonomous) | `prismatic/mode_switch.py` | ✅ landed |
| Schedule observatory (records + owner-aware mutation) | `prismatic/schedules.py` | ✅ landed |
| Gateway endpoints (`/schedules/*`) | `prismatic/gateway/server.py` | ✅ landed |
| Event bus types | `prismatic/gateway/ipc_bridge.py` | ✅ landed |
| `prismatic-engine doctor` | `prismatic/cli/doctor.py` (extracted in GRO-1971) | ✅ landed |
| GitHub-credentials gate in AGY/Jules launch | `prismatic/dispatcher.py` | ✅ landed |
| Stall-tracker-aware escalation | `prismatic/dispatcher.py` | ✅ landed |

### v0.1 contracts (the spine)

```
Provider (e.g., github, agy, ollama) — token discovery, API surface, stdlib only
   ↓
Capability (e.g., vcs.github, agy) — wraps provider with idempotency + event emission
   ↓
Registry — first-class slot for each capability, check_status() is a contract
   ↓
Gateway — read endpoints, owner-aware mutation, IPC bridge events
   ↓
ModeSwitch — gates every state transition (interactive/collaborative/autonomous)
```

### v0.1 tests

- `tests/test_capability_registry.py` — registry contract
- `tests/test_vcs_github_capability.py` — GitHub capability wrapper (idempotency, events)
- `tests/test_github_provider.py` — provider token discovery, auth, repo access
- `tests/test_mode_switch.py` — `is_major_transition`, pending approvals
- `tests/test_dispatcher_mode_switch.py` — dispatcher integrates the mode switch
- `tests/test_dispatcher_activation.py` — GRO-1957 GitHub-credentials gate
- `tests/test_schedule_records.py` — schedule records, gateway endpoints
- `tests/test_router_selection.py` — capability-aware task routing
- `tests/test_ollama_provider.py` — sibling provider example
- `tests/test_doctor_command.py` — `prismatic-engine doctor`
- `tests/test_journal.py` — journal continuity kernel (Phase 1 from earlier work)

Result: `pytest tests/` → 146 passed, 0 failed.

### Provider-neutral naming posture (live contract)

- The engine code uses **provider-specific class names** (`GitHubProvider`,
  `GitHubCapability`) while only one provider exists per capability slot.
- The **doc-level and registration-level** names are **provider-neutral**
  (`vcs.github`, `vcs_provider`, `code_host_provider`).
- The transition from provider-specific to provider-neutral class names is
  deferred until a second adapter is actively being built. Adding a GitLab
  or Bitbucket provider is the trigger to introduce a shared abstract
  base. Until then, premature abstraction would create debt.

### Open follow-ups (post-v0.1)

- **GRO-1955** — Build AGY chat interface for Google AI Ultra capability attach
  (additive: `chat.agy` capability + read-only gateway endpoints; **landed in GRO-1969** with empty list contract in v0.1; mutation paths deferred to a separate GRO-1955 follow-up)
- **GRO-1956** — Build Schedule Observatory for Prismatic, AGY, and Jules scheduled work
  (additive: real `agy` / `jules` schedule adapters with explicit fallback paths; **landed in GRO-1970** with `metadata.adapter = "live" | "fallback-mock"` for honest data freshness)
- **GRO-1957** — Add GitHub API connection layer for AGY + Jules CLI workflow
  (transformative: extract `cmd_doctor` into `prismatic/cli/doctor.py`; **landed in GRO-1971**)
- **GRO-1968** — AGY review of the v0.1 land (audit gate; AGY runs `agy-research-strategist`)
- **GRO-1972** — Codify Linear API rate-limit optimization into Prismatic Engine
  (`LinearBudget` token-bucket + `LinearMetrics` observability + dispatcher integration; research complete, plan at `specs/implementation-plans/linear-rate-limit-codification.md`)

### Branch & PR state

- Branch: `feature/capability-vcs-github`
- Open PR: <https://github.com/mbgulden/prismatic-engine/pull/22> (still open; commits
  `7ef0dbf` → `ee9a00b` → `7b2d2f8` → `61f2190` → `05e0a8d`)
- Latest commit: `05e0a8d [Fred] Fix label alias in get_issues_with_label + harden test_comment_based_approval`
- All 5 commits pushed; pre-push hook green; in-lane ownership verified.

