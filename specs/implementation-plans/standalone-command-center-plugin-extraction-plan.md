# Standalone Command Center & Plugin Extraction Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Convert the current Hermes-bound dashboard/plugin ideas into a standalone Prismatic Engine command center with separately versioned plugin workspaces, while making AGY CLI + Jules CLI (jules.google.com) orchestration the first seamless workflow lane.

**Architecture:** Prismatic Engine owns state, contracts, event streams, provider/harness registries, and workflow records. The command center is a standalone UI/API shell that consumes engine APIs. Plugins are independent repos/workspaces that register frontend/backend/action/event capabilities through manifests. Hermes/OpenClaw can embed or proxy the command center, but they do not own it.

**Tech Stack:** Python/FastAPI or stdlib-compatible API layer where possible, React/TypeScript frontend shell, existing Prismatic plugin manifest design, pytest for engine contracts, Playwright/visual tests for UI where available.

---

## Phase 0: Inventory and classification

### Task 1: Inventory existing Hermes plugins

**Objective:** Produce a machine-readable inventory of current Hermes plugin snapshots and their intended Prismatic target names.

**Files:**
- Create: `reports/plugin-extraction-inventory.json`
- Create: `reports/plugin-extraction-inventory.md`
- Reference: `docs/standalone-command-center-and-plugin-workspaces.md`

**Steps:**
1. Scan `plugins/hermes-plugin-*` and any Hermes dashboard plugin directories.
2. For each plugin, record:
   - current path
   - manifest path
   - frontend build presence
   - backend API presence
   - Hermes-specific imports or paths
   - Prismatic target name
   - extraction priority
3. Verify every plugin in the architecture doc appears in the inventory.

**Verification:**
```bash
python3 - <<'PY'
import json
items=json.load(open('reports/plugin-extraction-inventory.json'))
assert any(i['target_id']=='prismatic-plugin-activity-stream' for i in items)
assert any(i['target_id']=='prismatic-plugin-agent-control-deck' for i in items)
PY
```

---

### Task 2: Inventory Antigravity-Orchestration-Hub concepts

**Objective:** Convert legacy hub concepts into Prismatic feature candidates without coupling to the old codebase.

**Files:**
- Create: `reports/antigravity-hub-concept-map.md`

**Include:**
- explicit yield API
- decision log handoff vector
- real-time stream
- DAG workflow view
- AGY/Jules split-brain loop
- suggested tasks
- scheduled maintenance
- standardized tests/outcome scoring
- skill evolution proposals

**Verification:**
```bash
grep -q 'decision log' reports/antigravity-hub-concept-map.md
grep -q 'Jules CLI (jules.google.com)' reports/antigravity-hub-concept-map.md
```

---

## Phase 1: Engine contracts before UI

### Task 3: Define Prismatic event schema

**Objective:** Add a stable event schema that all plugins can consume.

**Files:**
- Create: `schemas/events/prismatic-event.schema.json`
- Create: `docs/events.md`
- Test: `tests/test_event_schema.py`

**Minimum event types:**
- `task.created`
- `run.started`
- `provider.output`
- `lock.acquired`
- `lock.released`
- `handoff.yielded`
- `review.started`
- `review.completed`
- `test.started`
- `test.failed`
- `test.passed`
- `skill.proposal.created`
- `human.intervention.requested`

**Verification:**
```bash
python3 -m pytest tests/test_event_schema.py -q
```

---

### Task 4: Define run record / decision-log contract

**Objective:** Standardize what AGY, Jules, Claude, Codex, and harnesses hand to each other.

**Files:**
- Create: `schemas/run-record.schema.json`
- Create: `docs/run-records.md`
- Test: `tests/test_run_record_schema.py`

**Run record contains:**
- `run_id`
- `task_id`
- `provider_id`
- `workspace`
- `branch`
- `decision_log`
- `artifacts`
- `tests`
- `handoffs`
- `outcome_score`

**Verification:**
```bash
python3 -m pytest tests/test_run_record_schema.py -q
```

---

### Task 5: Implement explicit workflow yield endpoint/CLI

**Objective:** Replace brittle file/token watching with a first-class yield contract.

**Files:**
- Create: `prismatic/workflows.py`
- Modify: `pyproject.toml`
- Test: `tests/test_workflow_yield.py`

**Target UX:**
```bash
prismatic workflow yield --run RUN_ID --status success --artifact decision_log.md
```

**Behavior:**
- validates run exists
- records handoff event
- links artifact
- emits `handoff.yielded`

---

### Task 6: Add event stream API

**Objective:** Give standalone plugins a live event source.

**Files:**
- Create: `prismatic/events.py`
- Create: `prismatic/api/events.py` if API layer exists
- Test: `tests/test_events.py`

**Interfaces:**
- append event
- list recent events
- subscribe via SSE/WebSocket later
- file/SQLite fallback for bare-metal installs

**Rule:** push-based stream is the goal; polling is only fallback.

---

## Phase 2: Google-first workflow lane

### Task 7: Add AGY CLI provider adapter

**Objective:** Register AGY CLI as an engine provider with modes and health checks.

**Files:**
- Create: `prismatic/providers/agy_cli.py`
- Test: `tests/test_provider_agy_cli.py`

**Capabilities:**
- implementation
- review
- research
- visual/asset handoff where SDK is attached
- health check: command exists + auth/token status if available

**Failure modeling:**
- timeout
- exit 0 but missing artifact
- auth expired
- no transcript/log

---

### Task 7A: Add AGY chat surface for Google AI Ultra attach

**Objective:** Make AGY feel like an immediately chat-capable provider, not only a background task runner. When a user connects AGY / Google AI Ultra, they can open Prismatic Command Center or a Telegram adapter and chat with the AGY CLI instance running on the same Prismatic host.

**Linear:** GRO-1955 — Build AGY chat interface for Google AI Ultra capability attach.

**Files:**
- Create: `prismatic/chat.py`
- Create: `prismatic/providers/agy_chat.py` or extend `prismatic/providers/agy_cli.py` with a separate chat mode
- Create: `prismatic/api/chat.py` if API layer exists
- Create: `docs/providers/agy-chat.md`
- Test: `tests/test_agy_chat_provider.py`

**Target UX:**
```bash
prismatic providers attach agy
prismatic chat agy --workspace /path/to/workspace
```

Command Center target:
```text
Capability Map → Google / AGY attached → Open Chat → talk to AGY
```

Telegram adapter target:
```text
Telegram message → Prismatic chat gateway → AGY CLI session → Prismatic transcript/run record → Telegram reply
```

**Acceptance:**
- `prismatic providers attach agy` verifies AGY CLI availability, Google auth, usable model/profile, and writable session/log paths.
- A user can start a chat with AGY without creating a Linear issue or workflow task first.
- Chat sessions are engine-owned records with transcript, selected workspace, model/profile, artifacts, and timestamps.
- Command Center exposes new chat, continue chat, workspace/add-dir selection, model/profile selection, timeout/budget display, and artifact links.
- Telegram/Hermes/Slack are adapters only; detaching them leaves the chat/run history intact.
- Failure modes are human-readable in `prismatic doctor`: missing AGY, expired Google auth, invalid model, no writable session directory, headless AGY stall.
- Interactive chat and background dispatch share provider configuration but have separate safety defaults.

**Product note:** This closes the specific gap Michael hit when using only Google AI Ultra / Antigravity IDE: AGY power existed underneath the interface, but there was no clean chat-like surface. The Prismatic promise is: attach Google/AGY → immediately talk to AGY.

---

### Task 8: Add Jules CLI provider adapter

**Objective:** Treat Jules CLI (jules.google.com) as a first-class review/implementation provider with remote GitHub state awareness.

**Files:**
- Create: `prismatic/providers/jules_cli.py`
- Test: `tests/test_provider_jules_cli.py`

**Key rule:** Jules sees remote GitHub state, not local uncommitted files.

**Adapter should support:**
- `doctor`
- `start_session`
- `list_sessions`
- `pull_session`
- `link_pr_or_review`

**Prompt payload includes:**
- branch
- remote URL
- task scope
- persona
- decision log
- diff summary
- required artifacts
- verification expectations

---

### Task 9: Implement AGY → Jules → AGY workflow template

**Objective:** Make the first seamless Google loop concrete.

**Files:**
- Create: `prismatic/workflow_templates/google_agy_jules_loop.yaml`
- Create: `docs/workflows/google-agy-jules-loop.md`
- Test: `tests/test_google_agy_jules_template.py`

**Flow:**
1. AGY implements.
2. AGY writes decision log and test evidence.
3. Engine checkpoints branch and pushes.
4. Jules CLI reviews with persona/scope.
5. Engine pulls/links Jules output.
6. AGY applies fixes if needed.
7. Engine runs standardized tests.
8. Engine records outcome and skill proposals.

---

### Task 10: Add Jules persona/scope registry

**Objective:** Ensure Jules uses the right persona for the right job.

**Files:**
- Create: `config/jules-personas.yaml`
- Create: `docs/providers/jules-personas.md`
- Test: `tests/test_jules_personas.py`

**Personas:**
- reviewer-only
- security-reviewer
- dependency-maintainer
- ui-visual-reviewer
- docs-reviewer
- test-gap-reviewer
- refactor-implementer
- release-validator

Each persona defines allowed mode, scope, artifacts, and verification.

---

## Phase 3: Standalone command center shell

### Task 11: Create command center service skeleton

**Objective:** Start a standalone web/API shell independent of Hermes.

**Files:**
- Create: `prismatic/command_center/server.py`
- Create: `prismatic/command_center/static/` or frontend app root
- Modify: `pyproject.toml`
- Test: `tests/test_command_center_server.py`

**Target UX:**
```bash
prismatic command-center serve --host 127.0.0.1 --port 9130
```

**Endpoints:**
- `/health`
- `/api/capabilities`
- `/api/events`
- `/api/tasks`
- `/api/runs`
- `/api/plugins`

---

### Task 12: Add plugin registry API

**Objective:** Load independent plugin manifests and expose them to the command center.

**Files:**
- Create: `prismatic/plugins/registry.py` or extend existing module
- Create: `schemas/plugin-manifest.schema.json`
- Test: `tests/test_plugin_registry.py`

**Manifest:** `PRISMATIC_PLUGIN.yaml`

**Rules:**
- plugin workspace path is explicit
- no arbitrary code execution during scan
- backend actions require declared permissions
- frontend bundle served only from declared dist path

---

### Task 13: Create Hermes embed adapter

**Objective:** Let Hermes show the standalone command center without owning it.

**Files:**
- Create: `docs/harnesses/hermes-command-center-embed.md`
- Create: `templates/hermes-command-center-plugin/manifest.json`

**Behavior:**
- Hermes dashboard tab proxies or links to `prismatic command-center serve`.
- No business logic inside Hermes plugin.
- If Hermes is detached, command center still runs.

---

## Phase 4: Plugin workspace extraction

### Task 14: Extract Activity Stream plugin workspace

**Objective:** First plugin extraction canary.

**Repo target:** `prismatic-plugin-activity-stream`

**Files in new workspace:**
- `PRISMATIC_PLUGIN.yaml`
- `README.md`
- frontend source
- tests

**Acceptance:** consumes engine events and renders live stream from standalone command center.

---

### Task 15: Extract Agent Control Deck plugin workspace

**Objective:** Convert orchestrator command deck into policy-gated engine actions.

**Repo target:** `prismatic-plugin-agent-control-deck`

**Acceptance:** can trigger safe dry-run actions and request guarded actions through engine policy.

---

### Task 16: Extract Lock Dashboard plugin workspace

**Objective:** Convert lock dashboard to engine-owned lock registry.

**Repo target:** `prismatic-plugin-lock-dashboard`

**Acceptance:** displays current locks, owner, TTL, stale status, lane conflicts.

---

### Task 17: Extract Workspace Tree plugin workspace

**Objective:** Convert workspace browser to engine artifact/workspace allowlist rules.

**Repo target:** `prismatic-plugin-workspace-tree`

**Acceptance:** cannot browse outside configured workspaces; respects blocklist.

---

### Task 18: Extract Compute Observability plugin workspace

**Objective:** Convert VRAM observability to provider/compute health plugin.

**Repo target:** `prismatic-plugin-compute-observability`

**Acceptance:** shows GPU, local endpoints, local model route health, failover state.

---

### Task 19: Extract Tool Server Controller plugin workspace

**Objective:** Generalize MCP controller into tool-server controller.

**Repo target:** `prismatic-plugin-tool-server-controller`

**Acceptance:** MCP is one backend kind; plugin model can support future tool protocols.

---

### Task 20: Create SovereignSentinel plugin workspace

**Objective:** Attach server/network visualization as a bolt-on, not core.

**Repo target:** `prismatic-plugin-sovereign-sentinel` or add Prismatic manifest to `SovereignSentinel`.

**Capabilities:**
- consume `/api/live`
- consume `/api/intents`
- consume `/api/agent/tools`
- register safe hardware actions with policy gates
- surface topology and infra events in command center

**Safety:** physical actions require policy, cooldown, token check, and human confirmation thresholds.

---

## Phase 5: Standardized tests and self-optimization

### Task 21: Add workflow outcome schema

**Objective:** Record whether a workflow actually produced good results.

**Files:**
- Create: `schemas/workflow-outcome.schema.json`
- Create: `docs/workflow-evaluation.md`
- Test: `tests/test_workflow_outcome_schema.py`

**Fields:**
- task type
- provider route
- persona
- test results
- review verdict
- retries
- elapsed time
- cost/credits
- human intervention
- outcome score
- failure taxonomy

---

### Task 22: Add skill proposal workflow

**Objective:** Let AGY/Jules learn from outcomes without silently editing skills.

**Files:**
- Create: `prismatic/skill_proposals.py`
- Create: `docs/skill-evolution.md`
- Test: `tests/test_skill_proposals.py`

**Flow:**
1. Workflow outcome identifies repeated failure/success pattern.
2. Engine creates skill proposal with evidence.
3. Reviewer approves/edits/rejects.
4. Skill patch is applied through normal skill lifecycle.

---

## Acceptance Criteria

- Command center can run without Hermes.
- Hermes can embed/proxy command center without owning it.
- Existing Hermes plugins are mapped to standalone Prismatic plugin targets.
- Each major plugin has a separate repo/workspace plan.
- AGY CLI can be attached as both a background task provider and an interactive chat provider.
- Command Center and chat adapters can start AGY chat sessions without requiring a Linear issue first.
- AGY CLI + Jules CLI loop is documented and represented as engine workflow template.
- Jules persona/scope routing is explicit.
- SovereignSentinel is modeled as a bolt-on plugin with safety policy gates.
- Workflow results can feed skill proposals without silent self-modification.
- All user directions from this conversation are captured in docs and skill memory.
