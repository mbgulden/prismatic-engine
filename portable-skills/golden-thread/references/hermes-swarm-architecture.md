# Hermes Swarm Architecture — Complete Strategy & Operating Model

*Captured from GRO-665 (Jun 2026). Reference document describing the full swarm architecture: agents, labels, cron jobs, dispatcher, and workflows.*

---

## The Central Nervous System: Linear

**Linear is the source of truth for ALL work across ALL agents.** Every task, every handoff, every deliverable flows through Linear. Agents never pass work directly to each other — they always go through Linear so the full history is preserved.

### Why Linear, not Slack/Telegram/email?
* **Durable**: Issues persist forever, comments are threaded, history is searchable
* **Structured**: Labels, projects, cycles, priorities — not just chat messages
* **Agent-readable**: GraphQL API lets every agent query, create, and update
* **Human-readable**: Michael can see the full swarm state at a glance
* **GitHub-bridged**: Every code change links back to a Linear issue

---

## Agent Roles & Capability Matrix

| Agent | Role | Strengths | Interface | Label |
|---|---|---|---|---|
| **Fred (Hermes Orchestrator)** | Orchestrator, implementer, deployer | Task routing, code fixes, deployments, strategy | Telegram + terminal | `agent:hermes` |
| **AGY (Antigravity CLI)** | Vision, design, research | Google Drive analysis, UI/UX design, image generation, web research | PTY terminal | `agent:agy` |
| **Jules CLI** | Async repo worker | PR creation, code review, branch management, rebasing | CLI sessions | `agent:jules` |
| **Codex CLI** | Visible code reviewer | Interactive code review in VS Code tabs, conflict resolution | CLI exec | `agent:codex` |
| **Sage** | Becca's assistant | Transit briefings, journal recaps, HD insights | Telegram bot | N/A (persona) |
| **Kai** | Active Oahu assistant | Ella's project management, tour content | Telegram bot | N/A (persona) |
| **Jamie** | Michael's exec function | Next-step reminders, task nudges | Telegram bot | N/A (standalone) |

### Agent Strengths & Weaknesses

**AGY**: Excellent at research, design critique, and visual analysis. Poor at implementation (times out on large code changes). Best used for: planning, review, design, research.

**Jules**: Excellent at async PR work — creating branches, writing code, opening PRs. Limited to 300 sessions/day. Best used for: documentation PRs, schema changes, collector scripts, non-urgent code.

**Codex**: Excellent at visible interactive work the user wants to watch. Requires VS Code terminal. Best used for: complex refactors, debugging sessions, anything needing human oversight.

**Fred (Hermes)**: The orchestrator and closer. Best at: routing decisions, deployment, integration, strategy, fixing what other agents break.

---

## The Label-Based Handoff Protocol

This is the KEY innovation. Agents communicate through Linear labels:

```
Issue created with agent:agy
        ↓
Dispatcher picks up agent:agy → launches AGY
        ↓
AGY completes work → re-labels as agent:hermes
        ↓
Dispatcher picks up agent:hermes → signals Fred
        ↓
Fred implements/deploys/closes → labels agent:done
```

### Label Chain Patterns

**Visual/Design → Implementation**: `agent:agy` → `agent:hermes` → `agent:done`
**Research → Report**: `agent:agy` → `agent:done`
**Direct Implementation**: `agent:hermes` → `agent:done`
**Code Review**: `agent:codex` → `agent:hermes` → `agent:done`
**Async PR Work**: `agent:jules` → `agent:done`

### The Dispatcher (cron: every 15 min)
* Script: `~/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py`
* Polls Linear for issues with `agent:*` labels
* Launches the appropriate agent
* After launch: transitions label to next agent in chain
* Handles failures gracefully (one agent crash doesn't stop others)

---

## The GitHub Layer: Code Truth

**GitHub is the canonical source for ALL code.**

### Repo → Project Mapping
* `hd-platform` → HD Engine Core, HD Reports, HD Growth, etc.
* `active-oahu-tours-mirror` → Active Oahu Tours
* `agentic-swarm-ops` → Swarm Ops Docs, Orchestration Router
* `beyondsaas-site` → Beyond SaaS Brand
* `belief-deprogrammer` → Belief Deprogrammer
* `OpenHumanDesignMCP` → Open source MCP server

### Jules per-Repo Strategy
* Jules lives in EVERY repo for repo-level QC
* Don't trigger Jules on every one-off commit
* Batch commits into meaningful PRs before Jules review
* Use Jules for structural work (docs, schemas, collectors)
* Reserve Jules sessions for repos that are actively changing

---

## Cron Job Architecture (24 jobs)

### LLM-Driven Agents (human-visible output)
| Job | Freq | Purpose | Deliver To |
|---|---|---|---|
| Morning Briefing | 8am MT | Daily status for Michael | origin |
| Golden Thread Digest | 9am MT | Cross-project health scan | origin |
| Golden Thread Sync | 10am MT | Registry/Live sync | origin |
| Nightly Backlog Worker | 4am MT | Clear Linear backlog | origin |
| Nightly Content Engine | 10am MT | beyondsaas.ai content | telegram:8190664947 |
| Consulting Pipeline | 3pm MT | Follow-up reminders | telegram:8190664947 |
| Becca Morning Briefing | 8am MT | Sage → Becca transit | telegram:8570023972 |
| Becca Journal Recap | 11:59pm MT | Daily journal synthesis | telegram:8570023972 |
| Fred Transit Briefing | 1pm MT | Michael's transit | origin |
| Sage Transit Briefing | 1pm MT | Becca's transit | telegram:8570023972 |
| Weekly Journal Rollup | Sun 10am | Weekly summary | telegram:8190664947 |
| Monthly Compliance | 1st, 2pm MT | Business compliance | telegram:8190664947 |
| Monthly Skill Audit | 1st, 3am MT | Consolidate skills | origin |
| AGY Golden Thread | 6am/6pm MT | AGY project reviews | origin |
| Research→Strategy→Exec | 7pm MT | AGY research pipeline | origin |

### Script-Only Agents (infrastructure, silent)
| Job | Freq | Purpose |
|---|---|---|
| Agent Dispatcher | every 15 min | Linear label → agent routing |
| Journal Snapshot | every 60 min | Memory persistence |
| Becca Journal Snapshot | every 60 min | Becca memory persistence |
| Memory Grooming | 12:15am MT | Clean hot memory |
| Nudge Trigger | every 1 min | Watch for /tmp/nudge-fred |
| AOT Broken Links | Mon 8am | Link checker |
| PR Auto-Merger | on PR events | Low-risk PR auto-merge |

---

## The Complete Workflow: End to End

### Example: AGY Reviews Code → Fred Fixes → Done
1. Issue created with label `agent:agy`
2. Dispatcher picks up `agent:agy`, launches AGY with issue context
3. AGY works: reads code, finds edge case, posts plan
4. AGY applies fix: modifies code, verifies, comments
5. AGY re-labels: `agent:agy` → `agent:hermes`
6. Dispatcher picks up `agent:hermes`, signals Fred
7. Fred verifies: checks AGY's fix, runs tests
8. Fred closes: labels `agent:done`, moves to Done

### Example: Jules Creates Documentation PR
1. Issue `agent:jules` created for docs
2. Dispatcher launches Jules
3. Jules creates branch, writes docs, opens PR
4. Jules comments on issue with PR link
5. Jules re-labels `agent:done`

---

## Principles
1. **Linear is the ledger.** If it's not in Linear, it didn't happen.
2. **GitHub is the code.** Every change is a commit, every commit links to an issue.
3. **Labels are handshakes.** Agents don't talk to each other — they pass labels.
4. **One dispatcher, many agents.** The dispatcher is the ONLY thing that launches agents.
5. **Jules is capped.** 300 sessions/day. Don't waste them on noise.
6. **Fred closes.** The orchestrator is the final verifier before `agent:done`.
