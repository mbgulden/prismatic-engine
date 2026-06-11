# Prismatic Engine

> **One coordinator, full-spectrum autonomy.**  
> Prismatic Engine is a portable, multi-agent orchestration engine. Deploy autonomous agent swarms across any infrastructure — local, edge, or cloud. It is the hub in a hub-and-spoke architecture: one brain, many hands.

---

## 📖 Table of Contents
1. [What is Prismatic Engine?](#what-is-prismatic-engine)
2. [Architecture Overview](#architecture-overview)
3. [Repository File Map](#repository-file-map)
4. [Portable Agent Skills (`portable-skills/`)](#portable-agent-skills-portable-skills)
5. [Swarm Dashboard Plugins (`plugins/`)](#swarm-dashboard-plugins-plugins)
6. [Governance, Reports & Research](#governance-reports--research)
7. [Getting Started & Installation](#getting-started--installation)
8. [License](#license)

---

## 💡 What is Prismatic Engine?

Prismatic Engine is a **provider-agnostic task orchestration framework** that bridges issue trackers (Linear, GitHub, Jira) with agent runtimes (Hermes, Docker, CLI, remote bots). It answers one question:

> *"What task should which agent work on right now, and how do I tell them?"*

It is **not** an AI agent itself. It is the **coordinator** — the dispatcher that reads issues, enforces branch and lane boundaries, manages lock safety, routes them to the right agent, and tracks completion.

### Key Concepts

| Concept | Description |
|---|---|
| **Coordinator** | Central loop: poll tracker → route to agent → signal → verify. |
| **Signal** | Unit of work sent to an agent (file nudge, HTTP POST, Redis pub/sub). |
| **Task Provider** | Bridge to an issue tracker (Linear GraphQL, GitHub API, etc.). |
| **Agent** | Any runtime that can execute work (Hermes, CLI, Docker, Telegram bot). |
| **Pipeline** | Routing rules mapping labels/keywords to agents. |
| **Workspace** | Context directory passed to agents for file access. |
| **Lanes** | Directory write/read permissions assigned to specific agent profiles to prevent overlapping edits. |
| **Locks** | Thread-safe file-locking system preventing collision when multiple agents work on the same repository. |

---

## 🏗️ Architecture Overview

The Prismatic Engine separates the core coordination logic from the execution platforms (agents) and monitoring views (dashboards).

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                      PRISMATIC ENGINE (Core)                    │
 │                                                                  │
 │   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
 │   │  Task         │    │  Coordinator  │    │  Signal       │      │
 │   │  Provider     │───▶│  (Router)     │───▶│  Provider     │      │
 │   │  (Linear)     │    │               │    │  (File/HTTP)  │      │
 │   └──────────────┘    └──────────────┘    └──────┬───────┘      │
 │                                                    │              │
 │                                                    ▼              │
 │                                          ┌──────────────────┐    │
 │                                          │   Agent Runtime   │    │
 │                                          │  (Hermes/Docker)  │    │
 │                                          └────────┬─────────┘    │
 └───────────────────────────────────────────────────┼──────────────┘
                                                     │ (Telemetry Events)
                                                     ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                     HERMES SWARM DASHBOARDS                     │
 │                                                                  │
 │  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐ │
 │  │  Prismatic Hub  │   │  Swarm Manager  │   │ GPU/VRAM Monitor│ │
 │  └─────────────────┘   └─────────────────┘   └─────────────────┘ │
 │  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐ │
 │  │ Lock Observability │   │ MCP Server Deck │   │ Activity Stream │ │
 │  └─────────────────┘   └─────────────────┘   └─────────────────┘ │
 └─────────────────────────────────────────────────────────────────┘
```

### 1. Hub-and-Spoke Event Routing
The coordinator polls the issue tracker for new work, resolves metadata (labels, assignees), checks file locks, verifies lane write permissions, and dispatches a signal payload. Agents execute code in their isolated workspaces and report logs back.

### 2. Multi-Agent Git Coordination & Lane Governance
To prevent agents from overwriting each other's work or pushing conflicts:
* **Lanes** are defined in `PRISMATIC_ENGINE.yaml` (e.g., Fred owns `src/`, Kai owns `content/`, Ned owns `scripts/` and `prismatic/`).
* A local git `pre-push` hook validates the pushing branch prefix and ensures only files in the agent's owned lane were modified.
* **Locks** are stored in a centralized lock registry (`~/.antigravity/swarm_locks.json`) with stale lock detection (heartbeat auto-expiration > 5 minutes). Pushes containing locked files are rejected.

---

## 📂 Repository File Map

```
.
├── PRISMATIC_ENGINE.yaml             # Main project config (roles, lanes, locks, staging)
├── SOUL.md                           # Philosophical core, non-negotiables & vision doc
├── index.html                        # Sleek, animated dashboard landing page/demo
├── Dockerfile                        # Container recipe for serving the engine
├── docker-compose.yml                # Multi-container orchestration config
├── install.sh                        # Engine CLI installer and systemd service generator
├── pyproject.toml                    # Build config for the python package
├── LICENSE                           # Affero GPL v3 license
│
├── prismatic/                        # Core Python engine codebase
│   ├── __init__.py                   # Package initialization
│   ├── coordinator.py                # Coordinator orchestrator loop
│   ├── agents/                       # Agent adapter implementations
│   │   ├── __init__.py
│   │   ├── base.py                   # BaseAgent abstract definition
│   │   └── hermes.py                 # Hermes agent signal wrapper
│   └── providers/                    # Transport and tracker bridges
│       ├── __init__.py
│       ├── signals/                  # Signal adapters (File, HTTP, Redis, Telegram)
│       │   ├── base.py
│       │   ├── file.py
│       │   ├── http.py
│       │   └── redis.py
│       └── tasks/                    # Task adapters (Linear, Local)
│           ├── base.py
│           └── linear.py
│
├── portable-skills/                  # Reusable agent skill profiles and disciplines
│   ├── INSTALL.md                    # Setup and installation instructions
│   ├── export.py                     # Skill packaging exporter tool
│   ├── export.sh                     # Bash wrapper for skill exports
│   └── (discipline subdirectories... detailed below)
│
├── plugins/                          # Suite of 8 dashboard monitoring extensions
│   └── (plugin subdirectories... detailed below)
│
├── reports/                          # Audit reports and implementation specs
│   ├── rubric-assessment-2026-06-11.md
│   ├── agy-hermes-discovery-report.md
│   └── agy-core-boundary-validation.md
│
├── research/                         # Coordination landscape & research notes
├── specs/                            # Written architecture specifications
├── test-plans/                       # Quality assurance test plans and scripts
└── scripts/                          # Development and sync helpers
```

---

## 🧠 Portable Agent Skills (`portable-skills/`)

These directories contain modular, reusable rule systems and markdown runbooks injected into agents' system prompts to enforce professional disciplines:

* **[INSTALL.md](file:///home/ubuntu/work/prismatic-engine/portable-skills/INSTALL.md)**: Details how to copy/link these skills into live Hermes agent profile directories.
* **[export.py](file:///home/ubuntu/work/prismatic-engine/portable-skills/export.py) / [export.sh](file:///home/ubuntu/work/prismatic-engine/portable-skills/export.sh)**: Automates bundling, checking, and exporting these directories.
* **`autonomous-execution-discipline/`**: Guidelines for runner agents (like Ned) to independently parse errors, test code, and verify builds without prompting for human approval.
* **`github-pr-workflow/`**: Git review, automated staging tests, PR audits, and conflict resolution protocols.
* **`golden-thread/`**: Step-by-step verification methodology to ensure code does not just compile but solves the root problem.
* **`himalaya/`**: Code cleaniness and design aesthetic standards.
* **`orchestrator-delegation-discipline/`**: Rules for the coordinator agent (Fred) to decompose large tasks and delegate them to specialized roles.
* **`static-site-seo-fix/`**: Procedures for audits, canonical tag fixes, and landing page indexation policies.
* **`systematic-debugging/`**: Troubleshooting processes including logging audits and local reproduction.

---

## 🖥️ Swarm Dashboard Plugins (`plugins/`)

A consolidated collection of 8 React/Webpack-based plugin extensions built for the Hermes Dashboard to visualize swarm operations:

1. **`hermes-plugin-lock-dashboard/`**  
   *Displays live file lock status. Shows which files are currently locked, by which agent, and the remaining heartbeat TTL.*
2. **`hermes-plugin-mcp-controller/`**  
   *Model Context Protocol command panel. Lets you monitor active servers, test tools, and view server error logs.*
3. **`hermes-plugin-orchestrator-command-deck/`**  
   *Swarm control center. Dispatches commands, monitors active agents, and tracks active routing queues.*
4. **`hermes-plugin-prismatic-hub/`**  
   *Main coordination hub page. Visualizes event webhook dispatch, SQLite deduplication tables, and houses the interactive SVG prism refractor.*
5. **`hermes-plugin-realtime-activity-stream/`**  
   *Live SSE activity viewer. Feeds running subprocess logs and status updates from agents in real time.*
6. **`hermes-plugin-swarm-manager/`**  
   *Swarm session inspector. Explores workspace directories, acts as session director, and embeds an interactive shell terminal.*
7. **`hermes-plugin-vram-observability/`**  
   *Hardware telemetry monitor. Connects to `nvidia-smi` endpoints to render live GPU load, memory allocation, and VRAM limits.*
8. **`hermes-plugin-workspace-tree-navigator/`**  
   *Interactive file tree navigation component. Enables directory exploring, file editing, and direct downloads through the dashboard.*

---

## 🛡️ Governance, Reports & Research

The governance and research documents represent the engineering constraints and history behind the Prismatic Engine:

* **[PRISMATIC_ENGINE.yaml](file:///home/ubuntu/work/prismatic-engine/PRISMATIC_ENGINE.yaml)**: Enforces agent profiles (Fred, Kai, AGY, Jules, Ned), their branch name prefixes (e.g. `execution/`, `design/`), and their read/write folder lanes.
* **[SOUL.md](file:///home/ubuntu/work/prismatic-engine/SOUL.md)**: Describes the "manifestation of idea in reality" mantra. A strict guide on avoiding placeholders, completing tasks fully, and building features to be production-ready.
* **`reports/agy-core-boundary-validation.md`**: Architectural audit outlining core dispatch mechanisms vs plugin structures.
* **`reports/rubric-assessment-2026-06-11.md`**: Core evaluation score sheet checking swarm resilience, security, and performance.
* **`specs/prismatic-engine-architecture-v1.md`**: The initial architecture specification covering coordinator loops, git hooks, and lock interfaces.

---

## 🚀 Getting Started & Installation

### 1. Engine CLI Setup
```bash
# Clone the repository
git clone https://github.com/mbgulden/prismatic-engine.git
cd prismatic-engine

# Install in editable mode
pip install -e .

# Initialize the configuration files
prismatic-engine init

# Serve the coordinator
prismatic-engine serve
```

### 2. Lock & Lane Git Validation Hook Setup
Link the pre-push hook to check agent lanes before pushes:
```bash
ln -s ../../scripts/pre-push-hook.py .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

### 3. Running Dashboard Plugins
Each plugin inside `plugins/` features a package configuration and can be built and run inside the Hermes container environment:
```bash
cd plugins/hermes-plugin-prismatic-hub
npm install
npm run build
```
This compiles assets into `dashboard/dist/index.js` which is loaded dynamically by the dashboard runtime.

---

## 📄 License

Prismatic Engine is released under the **AGPLv3 License**.

```
Copyright (C) 2026 Michael Gulden (mbgulden)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.
```

---
*Prismatic Engine — one coordinator, full-spectrum autonomy.*
