# Prismatic Engine

> **One coordinator, full-spectrum autonomy.**  
> Prismatic Engine is the lightweight orchestration layer that connects
> issue trackers → agents → signals → completion.  It's the hub in a
> hub-and-spoke architecture — one brain, many hands.

---

## What Is Prismatic Engine?

Prismatic Engine is a **provider-agnostic task orchestration framework**
that bridges issue trackers (Linear, GitHub, Jira) with agent runtimes
(Hermes, Docker, CLI, remote bots).  It answers one question:

> *"What task should which agent work on right now, and how do I tell them?"*

It is **not** an agent.  It's the **coordinator** — the dispatcher that
reads issues, routes them to the right agent, and tracks completion.

### Key Concepts

| Concept | Description |
|---|---|
| **Coordinator** | Central loop: poll tracker → route to agent → signal → verify |
| **Signal** | Unit of work sent to an agent (file nudge, HTTP POST, Redis pub/sub) |
| **Task Provider** | Bridge to an issue tracker (Linear GraphQL, GitHub API, etc.) |
| **Agent** | Any runtime that can execute work (Hermes, CLI, Docker, Telegram bot) |
| **Pipeline** | Routing rules mapping labels/keywords to agents |
| **Workspace** | Context directory passed to agents for file access |

---

## Quick Install

```bash
# 1. Install the package
pip install prismatic-engine

# 2. Set up your API key (Linear, GitHub, etc.)
export LINEAR_API_KEY="lin_api_xxxxxxxxxxxxxxxx"
export LINEAR_TEAM_ID="GRO"

# 3. Initialize default config
prismatic-engine init

# 4. Start the coordinator
prismatic-engine serve
```

### From Source

```bash
git clone https://github.com/mbgulden/prismatic-engine.git
cd prismatic-engine
pip install -e .
prismatic-engine init
prismatic-engine serve
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      PRISMATIC ENGINE                            │
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
│                                          └──────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### Hub-and-Spoke

```
                     ┌──────────────────┐
                     │  Issue Tracker   │
                     │  (Linear.app)    │
                     └────────┬─────────┘
                              │
                     ┌────────▼─────────┐
                     │  PRISMATIC ENGINE │
                     │  (Coordinator)   │
                     └───┬───┬───┬──────┘
                    ┌────┤   │   ├────┐
                    │    │   │   │    │
              ┌─────▼┐ ┌▼───▼┐ ┌▼───▼┐
              │Fred  │ │Kai  │ │AGY  │ ...
              │Local │ │HTTP │ │CLI  │
              └──────┘ └─────┘ └─────┘
```

The coordinator polls the issue tracker for new work, routes each issue
to the correct agent via its configured signal provider, and tracks
completion.  Agents don't talk to each other — they only receive signals
from the hub.

---

## Provider-Agnostic Signal System

Signals are transport-agnostic.  The same `SignalPayload` travels across
any backend:

| Provider | Transport | Use Case |
|---|---|---|
| **File** | Filesystem nudge files | Local agents, dev, single-machine |
| **HTTP** | POST webhooks | Remote agents, Docker, cloud |
| **Redis** | Pub/sub channels | Multi-machine swarms, low-latency |
| **Telegram** | Bot API | Notifications, human-in-the-loop |
| **Linear Comment** | GraphQL mutation | Audit trail, status updates |

Add a new provider by subclassing `SignalProvider` — the coordinator
doesn't care which one is active.

---

## Bolt-On Marketplace

Prismatic Engine is designed for a **bolt-on marketplace** of providers
and agents.  Anyone can write:

- **A TaskProvider** — bridge your issue tracker (GitHub, Jira, ClickUp)
- **A SignalProvider** — add a new transport (SQS, Kafka, WebSocket)
- **An Agent** — wrap any runtime (Python script, shell command, Lambda)

Drop a plugin into the `prismatic/providers/` or `prismatic/agents/`
directory, register it in the factory, and it's live.

```python
# Example: registering a custom GitHub provider
from prismatic.providers.tasks import AGENT_TYPES
from my_plugin import GitHubTaskProvider

AGENT_TYPES["github"] = GitHubTaskProvider
```

---

## Comparison with Hermes

| Feature | Prismatic Engine | Hermes Agent |
|---|---|---|
| **Role** | Coordinator / Orchestrator | AI agent (does the work) |
| **Scope** | Task routing, signals, tracking | LLM-powered task execution |
| **Dependencies** | stdlib only | LLM API, tool frameworks |
| **Runtime** | Python 3.10+ | Python 3.10+ |
| **Install size** | ~50KB (no AI deps) | Larger (LLM tooling) |
| **When to use** | You need to coordinate multiple agents | You need an agent that can think and act |

**Prismatic Engine is lighter.**  It has zero AI dependencies.  It doesn't
call an LLM, generate text, or interpret natural language.  It reads
structured data from issue trackers and sends structured signals to agents.
Hermes (or any other agent) does the heavy lifting.

Think of Prismatic Engine as the **traffic cop** and Hermes as the **delivery
driver**.  They work best together.

---

## Deployment Paths

### 1. pip Install (simplest)

```bash
pip install prismatic-engine
prismatic-engine init
prismatic-engine serve
```

Runs as a standalone Python process.  Best for single-machine setups
where agents share a filesystem.

### 2. Docker

```dockerfile
FROM python:3.12-slim
RUN pip install prismatic-engine
COPY config/ /etc/prismatic/
CMD ["prismatic-engine", "serve"]
```

```bash
docker run -e LINEAR_API_KEY="..." \
  -v /tmp/prismatic:/tmp/prismatic \
  prismatic-engine:latest
```

Best for multi-container deployments with HTTP signal providers.

### 3. Hermes-Native

When running inside a Hermes session, Prismatic Engine uses the
`HermesAgent` adapter to send nudge files.  No additional services
needed — just import and use:

```python
from prismatic import PrismaticEngine
engine = PrismaticEngine()
engine.run_pipeline("pipeline:hermes")
```

---

## Configuration

Default config directory: `~/.config/prismatic/` or `./config/`

```
config/
├── agents.yaml        # Agent signal configurations
├── workspaces.yaml    # Workspace registry (optional)
└── router.yaml        # Routing rules and pipeline settings
```

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LINEAR_API_KEY` | Yes | Linear personal API key |
| `LINEAR_TEAM_ID` | No | Default team key (e.g. "GRO") |
| `PRISMATIC_NUDGE_DIR` | No | Nudge file directory (default: `/tmp/prismatic`) |
| `PRISMATIC_SECRET` | No | Shared secret for HTTP signal auth |
| `PRISMATIC_WORKSPACE` | No | Override workspace path for agents |

---

## Development

```bash
git clone https://github.com/mbgulden/prismatic-engine.git
cd prismatic-engine
pip install -e ".[dev]"
pytest
```

### Project Structure

```
prismatic/
├── __init__.py
├── coordinator.py       # Main loop: poll → route → signal → verify
├── providers/
│   ├── __init__.py
│   ├── signals/         # File, HTTP, Redis, Telegram
│   │   ├── base.py      # SignalProvider ABC
│   │   ├── file.py      # Local filesystem nudge files
│   │   ├── http.py      # HTTP webhook push
│   │   └── redis.py     # Redis pub/sub
│   └── tasks/
│       ├── base.py      # TaskProvider ABC + Issue dataclass
│       └── linear.py    # Linear GraphQL implementation
└── agents/
    ├── __init__.py
    ├── base.py          # BaseAgent ABC + AgentConfig + AGENT_TYPES registry
    └── hermes.py        # Hermes signal adapter
config/
├── agents.yaml
├── workspaces.yaml
└── router.yaml
```

---

## License

Prismatic Engine is released under the **AGPLv3 License**.

```
Copyright (C) 2026 Michael Gulden (mbgulden)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.
```

---

*Prismatic Engine — one coordinator, full-spectrum autonomy.*
