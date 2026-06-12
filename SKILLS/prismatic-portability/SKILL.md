---
name: prismatic-portability
description: >-
  Run the Prismatic Engine in Standalone Mode — offline, air-gapped, or
  resource-constrained environments. Covers local SQLite task queue, subprocess
  and Docker signal providers, CLI initialization wizard, and the 5-step quick
  start for local-only operation without Linear, Hermes, or GitHub.
---

# Prismatic Portability & Standalone Mode

## Trigger
Load this skill when deploying the Prismatic Engine in offline, air-gapped, or
resource-constrained environments where cloud dependencies (Linear, Hermes,
GitHub) are unavailable.

## Overview

Standalone Mode decouples the Prismatic Engine from external cloud dependencies,
enabling local-only operation. This is essential for:

- **Air-gapped environments** with no internet access
- **Local development** without API key setup
- **CI/CD pipelines** running in isolated containers
- **Demonstrations and testing** with zero infrastructure

## Decoupling Architecture

| Dependency | Standard Mode | Standalone Mode |
|---|---|---|
| **Task Intake** | Linear GraphQL API | Local SQLite Task Queue (`prismatic_tasks.db`) |
| **Agent Signaling** | Hermes messaging bus | Subprocess execution or Docker API |
| **Workspace / Git** | GitHub remote | Local Git repository (local-only branches) |
| **API Credentials** | Hermes environment keys | Local `.env` and `~/.config/prismatic/` |

## 5-Step Quick Start

```bash
# 1. Install
git clone https://github.com/mbgulden/prismatic-engine
cd prismatic-engine && ./install.sh

# 2. Initialize
prismatic-engine init --standalone
# Creates: ~/.config/prismatic/prismatic_tasks.db
#          ~/.config/prismatic/config.yaml
#          ~/.config/prismatic/agents/

# 3. Add a task
prismatic-engine task add \
  --role "researcher" \
  --description "Audit SEO for activeoahutours.com" \
  --priority 2

# 4. Run the engine
prismatic-engine run --mode standalone
# Picks up queued tasks, spawns agents, reports results

# 5. Check results
prismatic-engine task list --status completed
prismatic-engine task show <task-id>
```

## Local Task Queue (`prismatic_tasks.db`)

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    priority INTEGER DEFAULT 3,  -- 1=Critical, 2=High, 3=Medium, 4=Backlog
    role TEXT NOT NULL,           -- e.g., 'agent:ned', 'agent:agy'
    status TEXT DEFAULT 'QUEUED', -- QUEUED, RUNNING, COMPLETED, FAILED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    output_summary TEXT,
    error_message TEXT
);
```

### Task CRUD

```bash
# Add a task
prismatic-engine task add --role "agent:ned" --title "Fix nav routing" --priority 1

# Add a task via stdin (pipeline-friendly)
echo '{"role":"agent:agy","description":"Research competitors"}' | \
  prismatic-engine task add --stdin

# List tasks
prismatic-engine task list --status queued
prismatic-engine task list --status completed --limit 10

# Show task details
prismatic-engine task show <task-id>

# Run a single task (one-shot mode — no queue polling)
echo '{"role":"researcher","description":"Review spelling"}' | \
  prismatic-engine run --oneshot
```

## Signal Providers

### 1. Subprocess Adapter (Default)

Runs agents directly on the host OS. Instructions piped via environment
variables; output captured from stdout/stderr.

```bash
# Agent receives:
#   PRISMATIC_TASK_ID=<uuid>
#   PRISMATIC_TASK_ROLE=agent:ned
#   PRISMATIC_TASK_DESCRIPTION="Fix nav routing"
#   PRISMATIC_WORKSPACE=/home/user/project
#   PRISMATIC_LANE=src/

# Agent writes output to stdout (captured by engine)
# Agent exits with code 0 (success) or non-zero (failure)
```

### 2. Docker Adapter

Uses local Docker socket to spin up ephemeral agent containers. Local worktrees
mounted as volumes. Containers cleaned up on completion.

```bash
# Docker adapter config
# ~/.config/prismatic/config.yaml
signal_provider: docker
docker:
  socket: /var/run/docker.sock
  agent_image: prismatic-agent:latest
  mount_workspace: true
  cleanup: on_completion  # or 'never' for debugging
```

## Agent Configuration

```yaml
# ~/.config/prismatic/agents/ned.yaml
name: "Ned"
role: "agent:ned"
description: "Primary executor — code, fixes, builds"
model: "deepseek-v4-pro"
provider: "deepseek"
lane:
  write: ["src/", "infra/", "deploy/"]
  read_only: ["content/", "docs/"]
workspace: "/home/user/project"
```

## Environment Variables

```bash
# Required
PRISMATIC_MODE=standalone
PRISMATIC_CONFIG_DIR=~/.config/prismatic

# Optional overrides
PRISMATIC_DB_PATH=~/.config/prismatic/prismatic_tasks.db
PRISMATIC_AGENTS_DIR=~/.config/prismatic/agents/
PRISMATIC_LOG_LEVEL=info  # debug, info, warn, error
```

## Offline Skill and Template Loading

In Standalone Mode, skills and templates load from local directories:

```
~/.config/prismatic/
├── config.yaml
├── prismatic_tasks.db
├── agents/
│   ├── ned.yaml
│   ├── agy.yaml
│   ├── jules.yaml
│   ├── kai.yaml
│   └── fred.yaml
├── skills/
│   ├── prismatic-7-step-loop/
│   │   └── SKILL.md
│   ├── alchemy-quality-gates/
│   │   └── SKILL.md
│   └── lane-governance/
│       └── SKILL.md
└── templates/
    ├── linear/
    └── cron/
```

## Feature Comparison

| Feature | Standard Mode | Standalone Mode |
|---|---|---|
| Task intake | Linear API | SQLite + stdin |
| Agent signaling | Hermes bus | Subprocess/Docker |
| Git hosting | GitHub | Local repository |
| Skill loading | Hermes skill system | Local filesystem |
| Multi-user | Yes (via Linear) | Single-user (local DB) |
| Provenance | Linear comments + Git | Local SQLite + Git |
| Cron jobs | Hermes cron | System crontab / systemd timers |

## Pitfalls

- ❌ **Assuming Linear API is available:** In Standalone Mode, Linear polling
  is disabled. All task queries go to SQLite.
- ❌ **Mixing modes:** Don't point Standalone Mode at a production Linear
  project. It's designed for local-only operation.
- ❌ **Docker adapter without Docker:** Verify `docker info` succeeds before
  using the Docker signal provider.
- ❌ **Forgetting to init:** `prismatic-engine init --standalone` must run
  before any tasks can be added. The DB and config directories must exist.

See also: `lane-governance` skill, `prismatic-7-step-loop` skill.
