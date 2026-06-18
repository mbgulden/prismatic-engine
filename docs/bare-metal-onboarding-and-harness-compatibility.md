# Bare-Metal Onboarding & Harness Compatibility Architecture

> **Goal:** A user can start on a bare computer because the website looks cool, use Prismatic Engine immediately, and later attach or detach Hermes, OpenClaw, AGY, Jules CLI (jules.google.com), Claude CLI, Codex CLI, local GPUs, Google Cloud Platform, or future systems without losing the engine’s continuity layer.

## Core Answer

Prismatic Engine should be an **independent kernel with attachable capability adapters**.

It is not an agent harness. It should not try to become Hermes, OpenClaw, AGY, Claude Code, Codex CLI, or Google Cloud. It should provide:

1. **State:** journals, run records, task state, source inventories, locks, lanes, provider registry, config.
2. **Contracts:** task schema, artifact schema, agent capability schema, scheduler schema, journal schema, provider schema.
3. **CLIs:** commands that any human, shell, LLM, or harness can invoke.
4. **Adapters:** thin integrations that let existing harnesses and provider CLIs plug in.
5. **Migration tools:** import/export/attach/detach paths so plumbing can move without replatforming the kernel.

Harnesses provide ergonomics: chat surfaces, cron engines, profiles, memory, tool calling, dashboards, OAuth helpers, and notification routing. The engine provides the durable spine.

---

## Bare Computer Lifecycle

### Stage 0 — Curious user, no harness

The user has a computer with Python and git. They want the engine to do something useful in minutes.

Desired happy path:

```bash
curl -fsSL https://prismaticengine.com/install.sh | bash
prismatic doctor
prismatic init
prismatic journal init
prismatic journal snapshot --force
prismatic status
```

Expected result:

- `$PRISMATIC_HOME` exists.
- `~/.prismatic/config.yaml` exists.
- `~/Prismatic/journals/` or configured journal root exists.
- `prismatic journal snapshot` writes an inbox entry and event index.
- `prismatic status` shows which optional capabilities are missing, without failing the core install.

The engine should be useful even if every capability is missing:

- No Hermes? Fine: no chat gateway or Hermes cron.
- No AGY? Fine: no AGY research/assets, but journal and local task tracking work.
- No Linear? Fine: local task provider works.
- No GPU? Fine: local GPU provider shows unavailable.
- No cloud credentials? Fine: GCP adapter shows unavailable.

### Stage 1 — Add one provider CLI

Example: user installs AGY but no harness.

```bash
prismatic providers scan
prismatic providers attach agy
prismatic agents add agy --type cli --command "agy --prompt-interactive --add-dir {workspace}"
prismatic task create "Audit this repo" --agent agy --workspace .
prismatic dispatch once
```

Expected result:

- Engine records AGY as a capability.
- Engine can dispatch a task through a shell command adapter.
- Journals and run records capture what happened.
- No harness is required.

### Stage 2 — Add scheduler plumbing

Without Hermes/OpenClaw, use system cron/systemd timers:

```bash
prismatic scheduler install systemd
prismatic scheduler enable journal-snapshot --every "1h"
prismatic scheduler enable continuity-audit --monthly
```

If Hermes is later attached, those jobs can be migrated:

```bash
prismatic attach hermes --migrate-scheduler systemd
```

### Stage 3 — Add an agent harness

Example: user installs Hermes after a month of independent journal use.

```bash
hermes setup
prismatic attach hermes
prismatic attach hermes --install-shims --sync-skills --register-crons
prismatic migrate scheduler systemd hermes
prismatic verify harness hermes
```

Expected result:

- Existing journals stay under the engine’s configured journal root.
- Hermes gets shims such as `hermes-publish -> prismatic-publish` and `monthly_journal_continuity_audit.py -> prismatic-journal monthly`.
- Hermes cron can schedule engine commands, but does not own the journal logic.
- Hermes chat can invoke `prismatic-*` commands.
- If Hermes is removed later, the engine still runs.

### Stage 4 — Add local GPUs

```bash
prismatic compute scan
prismatic compute attach local-gpu --runtime vllm --endpoint http://localhost:8000/v1
prismatic providers attach local-openai --base-url http://localhost:8000/v1 --model local-model
prismatic doctor compute
```

Expected result:

- Engine knows a GPU-backed OpenAI-compatible endpoint exists.
- Provider routing can include local models for cheap/offline tasks.
- If the endpoint disappears, routes degrade gracefully.

### Stage 5 — Add cloud platform

Example: GCP / Vertex / Lyria / storage / Cloud Run.

```bash
prismatic cloud attach gcp
prismatic cloud doctor gcp
prismatic providers attach vertex --project <project-id> --region us-central1
```

Expected result:

- GCP credentials are checked and stored outside the engine repo.
- Cloud capabilities become optional routes, not required dependencies.

---

## What Users Lose When Detaching a Harness

The detach story must be explicit. A user should know what is kernel state versus harness convenience.

### Detach Hermes

Keep:

- Prismatic config.
- Journals and event indexes.
- Source inventories and continuity reports.
- Locks and lane policy.
- Provider registry and CLI adapters.
- Local/systemd scheduler if installed.
- Artifact publisher if running as engine service.

Lose unless replaced:

- Telegram/Slack/Discord gateway chat.
- Hermes profile memory and session search.
- Hermes cron scheduler.
- Hermes tool calling, delegation, and skill loading ergonomics.
- Hermes dashboard plugins.
- Hermes OAuth credential helpers.

Required replacement if Hermes is removed:

- Scheduler: systemd/cron/OpenClaw scheduler.
- Chat surface: none, terminal, OpenClaw, Discord bot, web UI, etc.
- Memory/session search: engine journal + optional external memory provider.
- Notifications: engine notifier adapter or another harness.

### Detach OpenClaw

Keep the same kernel state. Lose OpenClaw-specific session UX, worker model, and scheduler/gateway features. Any work it did through `prismatic-*` commands remains in engine state.

### Detach AGY

Keep journals, tasks, and run records. Lose AGY-specific research, vision, asset generation, Google Antigravity sub-agent orchestration, and Flow-credit-backed capabilities. Routes using AGY become unavailable and should fall back or pause.

### Detach Claude CLI / Codex CLI / Jules CLI (jules.google.com)

Keep task state and artifacts. Lose that provider’s implementation/review lane until another provider is attached. The engine should mark provider routes as unavailable, not delete tasks.

### Detach local GPU

Keep all state. Lose local/offline inference and GPU telemetry. Provider routing should degrade to cloud/API providers if configured.

---

## Harness Attachment Contract

Every harness adapter should implement the same narrow contract.

### Required adapter capabilities

```yaml
harness:
  id: hermes
  kind: chat-agent-harness
  version_command: hermes --version
  config_root: ~/.hermes
  supports:
    chat_gateway: true
    scheduler: true
    profiles: true
    skills: true
    memory: true
    tool_calling: true
    dashboard: true
  attach:
    install_shims: true
    sync_skills: true
    register_scheduler_jobs: true
    expose_prismatic_commands: true
  detach:
    export_sessions: true
    migrate_scheduler: true
    preserve_journals: true
```

### Hard rules

- Harness adapters must not own business logic.
- Harness shims must `exec prismatic-*` commands.
- Harness cron jobs should call engine CLIs.
- Harness memory may enrich the experience, but journal continuity must remain engine-owned.
- Harness-specific paths and tokens must not appear in engine core except as compatibility fallbacks at the boundary.

---

## Provider / Capability Registry

The engine needs a first-class registry of optional capabilities. This is separate from harnesses.

```yaml
capabilities:
  providers:
    agy:
      type: cli-agent
      command: agy
      modes: [implementation, review, research, vision, assets]
      auth: google-oauth
      healthcheck: agy --version
      requires_pty: true
    claude:
      type: cli-agent
      command: claude
      modes: [implementation, review]
      auth: anthropic
      healthcheck: claude --version
    codex:
      type: cli-agent
      command: codex
      modes: [implementation, review]
      auth: openai-oauth
      healthcheck: codex --version
    jules-cli:
      display_name: Jules CLI (jules.google.com)
      type: cli-agent
      command: jules
      modes: [review, validation]
    local-openai:
      type: openai-compatible
      base_url: http://localhost:8000/v1
      modes: [completion, review]
  compute:
    local-gpu:
      type: nvidia
      healthcheck: nvidia-smi
  cloud:
    gcp:
      healthcheck: gcloud auth list
```

The key is that `providers` are not harnesses. Hermes can call Claude. OpenClaw can call Claude. The engine can call Claude directly. Claude is a capability, not a harness.

---

## Install / Config UX Target

The install should be progressive, not one giant setup wizard.

### Principle

Install core now. Configure capabilities only when needed.

### Proposed command set

```bash
# Core
prismatic init
prismatic doctor
prismatic status
prismatic config edit

# Journal continuity
prismatic journal init
prismatic journal snapshot
prismatic journal audit --period 2026-06

# Capabilities
prismatic providers scan
prismatic providers attach <provider>
prismatic providers doctor <provider>
prismatic providers detach <provider>

# Harnesses
prismatic harness scan
prismatic harness attach hermes
prismatic harness attach openclaw
prismatic harness detach hermes --migrate-scheduler systemd
prismatic harness doctor hermes

# Schedulers
prismatic scheduler install systemd
prismatic scheduler list
prismatic scheduler enable journal-snapshot --every 1h
prismatic scheduler migrate hermes systemd

# Cloud/compute
prismatic compute scan
prismatic cloud attach gcp
prismatic cloud doctor gcp

# Migration
prismatic import hermes --sessions --skills --cron
prismatic export bundle --include journals,tasks,config
```

### First-run wizard shape

```text
Welcome to Prismatic Engine.

Core installed. Optional capabilities detected:
- Python: ok
- git: ok
- AGY: found, not attached
- Claude CLI: missing
- Codex CLI: missing
- Hermes: found, not attached
- NVIDIA GPU: found
- Linear token: missing
- GCP: missing

What do you want to enable now?
[1] Journal continuity only
[2] Attach detected AGY
[3] Attach Hermes
[4] Configure local GPU endpoint
[5] Skip; I will configure later
```

The default should be **journal continuity only**. That means the user gets value immediately without being forced into every integration.

---

## Scenario Walkthroughs

### Scenario A — Independent first, Hermes later

Month 1:

```bash
prismatic init
prismatic journal init
prismatic scheduler install systemd
prismatic scheduler enable journal-snapshot --every 1h
```

Month 2:

```bash
hermes setup
prismatic harness attach hermes --install-shims --sync-skills
prismatic scheduler migrate systemd hermes
```

Result: prior journals remain valid. Hermes becomes the chat/orchestration face. Engine remains source of truth.

### Scenario B — AGY-only, no harness ever

```bash
prismatic init
prismatic providers attach agy
prismatic scheduler install systemd
prismatic task create "Run a monthly continuity audit" --agent agy
prismatic dispatch once
```

Result: works as terminal-native orchestration. Missing: rich chat gateway, persistent conversational memory, Hermes tool ecosystem. Kept: tasks, runs, journals, artifacts, locks, provider state.

### Scenario C — Hermes to OpenClaw migration

```bash
prismatic harness detach hermes --export sessions,cron,skills
prismatic harness attach openclaw --import exported-bundle
prismatic scheduler migrate hermes openclaw
prismatic verify harness openclaw
```

Result: OpenClaw takes over ergonomics. Engine state remains unchanged.

### Scenario D — Full stack gradually

1. Start with journal-only.
2. Attach AGY for research/assets.
3. Attach Codex CLI for implementation.
4. Attach Jules CLI (jules.google.com) for review.
5. Attach local GPU for cheap/offline review.
6. Attach Hermes for Telegram/Slack orchestration.
7. Attach GCP for media/model/cloud jobs.

At no point does the engine require reinstalling or moving the journal state.

---

## Engineering Gaps

### P0 — Make bare-metal install real

- Real `install.sh` or package manager path.
- `prismatic init` creates sane config and directories.
- `prismatic doctor` reports optional capability status.
- README quickstart must match actual commands.

### P0 — First-class journal init

Current journal CLIs exist, but onboarding needs:

- `prismatic journal init`
- default local journal root
- scheduler registration templates
- local-only mode with no Linear/Hermes

### P0 — Harness adapter contract

Implement:

- `prismatic harness scan`
- `prismatic harness attach hermes`
- `prismatic harness detach hermes`
- shim installation
- scheduler import/export
- skills sync boundary

### P1 — Capability registry

Implement:

- `prismatic providers scan`
- `prismatic providers attach/detach/doctor`
- health checks for AGY, Claude CLI, Codex CLI, Jules CLI (jules.google.com), local OpenAI-compatible endpoints, GCP.

### P1 — Scheduler abstraction

Support at least:

- systemd timers
- crontab
- Hermes cron
- OpenClaw scheduler adapter later

### P1 — Migration bundle format

Create a portable tar/zip or directory bundle:

```text
prismatic-export/
  manifest.json
  config.yaml
  journals/
  tasks.db
  run-records.db
  skills/
  scheduler.json
  harnesses/
```

### P2 — UI integration

Expose the same lifecycle through the Prismatic interface:

- capability cards
- attach/detach buttons
- doctor checks
- missing capability prompts exactly when a workflow needs them
- migration wizard

### P2 — Future-proof plugin API

Define adapter APIs that future harnesses can implement without engine changes:

- `HarnessAdapter`
- `ProviderAdapter`
- `SchedulerAdapter`
- `NotifierAdapter`
- `MemoryAdapter`
- `ArtifactAdapter`

---

## Non-Negotiable Product Rules

1. **No forced harness.** Bare engine must remain useful.
2. **No hidden ownership transfer.** Attaching Hermes/OpenClaw cannot silently move engine state into harness-owned folders.
3. **No business logic in harness shims.** Shims execute engine commands.
4. **Graceful degradation.** Missing capabilities show unavailable, not broken.
5. **Progressive setup.** Configure things as the user needs them.
6. **Journal continuity survives migrations.** This is the canary and the proof.
7. **Capability registry beats hardcoded assumptions.** The engine asks “what is available?” before routing.
8. **Export always exists.** If a user wants out, `prismatic export bundle` gives them their state.

---

## Recommended Next Build Sequence

1. `prismatic init` + `prismatic doctor` reality pass.
2. `prismatic journal init` local-only mode.
3. `prismatic harness scan/attach/detach` skeleton with Hermes adapter first.
4. `prismatic providers scan/attach/doctor` for AGY, Claude CLI, Codex CLI, Jules CLI (jules.google.com), local OpenAI endpoint.
5. `prismatic scheduler install/list/enable/migrate` for systemd and Hermes cron.
6. `prismatic export bundle` / `prismatic import bundle`.
7. UI cards for capabilities and migration state.

This sequence keeps the project from accidentally becoming an agent harness while still making it feel like one coherent system to the user.
