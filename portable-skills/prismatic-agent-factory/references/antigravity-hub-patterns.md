# Antigravity Orchestration Hub — Extracted Patterns

Source: AGY ingest of mbgulden/Antigravity-Orchestration-Hub, 2026-06-13
Full report: $PRISMATIC_HOME/work/agentic-swarm-ops/docs/antigravity-hub-secrets.md

## 5 Core Patterns

1. **Constrained Planner** — LLM forced to pick from registered persona IDs. Invalid picks remap to CORE-LOGIC-PLANNER as safe fallback. Prevents hallucinated agent roles.

2. **Zero-Trust Sandboxing** — Dynamic contract injection with allowed directories, read-only directories, anti-pattern rules from prior failures, and self-termination contract (agent deletes its own contract file on completion).

3. **Webhook Yield Loop** — Ephemeral HTTP server on random port with one-time bearer token. Agent POSTs when done. No polling. Server closes, archives summary, spawns clean thread.

4. **Scout-Critic Validation** — High-risk tasks split: Scout explores 2-3 paths on isolated branch, writes structured report. Critic validates. Only VALIDATED verdicts merge.

5. **Dual-Track Routing** — UI tasks sequential (UiQueueProcessor), background tasks parallel (fire-and-forget to Headless API / Local AI / GitHub Jules).

## Persona Architecture
- 72 roles across 5 domains (Engineering 24, 3D 12, Animation 12, Audio 12, Infrastructure 12)
- Compile-time cap of 100 entries to prevent registry pollution
- Each persona: systemPrompt with HARD RESTRICTIONS, allowedDirectories, readOnlyDirectories, preferredHead, maxActions

## Lock Protocol
- Mutex file: `.antigravity/swarm_locks.json`
- swarm.js CLI: lock/unlock/status
- Stale lock cleanup on agent termination
