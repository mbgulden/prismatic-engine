# Kai Fleet Architecture — Live Configuration (Jun 13, 2026)

## Fleet Layout

```
Kai (main orchestrator) — PID 786270, up since Jun 11
├── Kai-CSS    — cron ace8ecd3ef53, every 5min, agent:kai-css
├── Kai-Content — cron 2ac45086e335, every 5min, agent:kai-content
└── Kai-JS     — cron 4634d607c484, every 5min, agent:kai-js

Kai Orchestrator — cron b53f6eea750e, every 15min, agent:kai → sub-agent decomposition
Kai Callback Monitor — cron ecc080d17c00, every 2min, detects completions → nudges Kai
Second Witness — cron 2f2da24ba5e3, every 30min, AGY review terminal for Prismatic
```

## Label IDs (GrowthWebDev team)

| Label | ID |
|-------|----|
| agent:kai | `c4d929be-8d15-4482-b6d7-a5ed85aa2e73` |
| agent:kai-css | `f246eb61-5e84-4594-8b31-249a588c5648` |
| agent:kai-content | `4da5ee04-607f-4188-b9e0-18af413ad62f` |
| agent:kai-js | `61180755-4f1f-48fb-bedf-ddd02f7cffaf` |
| agent:agy | `1b69d9c0-20a8-45b3-a594-771b8cba75a7` |
| agent:done | `a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b` |
| agent:fred | `a43efb77-534a-4e39-8ff3-76f0e42019d1` |
| agent:ned | `6e0400c9-fc04-4868-86e3-f3156821f413` |

## State IDs (GrowthWebDev team)

| State | ID |
|-------|----|
| Todo | `3d29ebe3-00cf-428b-b52a-bfecb5ae4410` |
| In Progress | `734901ee-58f0-457c-b9a0-f911c0da13a4` |
| In Review | `6a5050ad-3386-4623-a404-7f2791047cd5` |
| Done | `bbf71b3e-9a05-48ce-9418-df8b9c0b8fec` |
| Backlog | `e5544f55-482e-49ac-b0f7-3dd2e1775dbb` |
| Canceled | `a19484ec-9752-4c31-8110-f5043312e328` |

## Team ID
GrowthWebDev: `b6fb2651-5a1f-4714-9bcd-9eb6e759ffef`

## Profile Locations
- Kai (main): `$PRISMATIC_HOME/.hermes/profiles/kai/config.yaml` — deepseek-v4-flash, delegation enabled, HD MCP
- Kai-CSS: `$PRISMATIC_HOME/.hermes/profiles/kai-css/config.yaml` — deepseek-v4-flash, no delegation
- Kai-Content: `$PRISMATIC_HOME/.hermes/profiles/kai-content/config.yaml` — deepseek-v4-flash, no delegation
- Kai-JS: `$PRISMATIC_HOME/.hermes/profiles/kai-js/config.yaml` — deepseek-v4-flash, has browser toolset

## Skills Loaded by Each Cron

| Cron | Skills |
|------|--------|
| Kai-CSS | kai-css-agent, autonomous-execution-discipline, aot-agent-coordination |
| Kai-Content | kai-content-agent, autonomous-execution-discipline, aot-agent-coordination |
| Kai-JS | kai-js-agent, autonomous-execution-discipline, aot-agent-coordination |
| Kai Orchestrator | kai-orchestrator, autonomous-execution-discipline, aot-agent-coordination |

## Kai Gateway
- Running as: `hermes --profile kai gateway run` (PID 786270, since Jun 11)
- Allowed chats: 8190664947 (Michael), 8424997958 (Ella), -5106332713 (group)
- Model: deepseek-v4-flash with OpenAI Direct fallback
- Delegation: max_concurrent_children=3, orchestrator_enabled=true

## Kai Callback Monitor (Jun 13 addition)

**Cron:** `ecc080d17c00` — runs every 2 min, no_agent (Python script)
**Script:** `~/.hermes/profiles/orchestrator/scripts/kai_callback_monitor.py`

Detects when sub-agents complete work:
1. Scans for `agent:kai-css/content/js` issues in "In Review"
2. Scans for `agent:agy` issues whose parent is `agent:kai`
3. Scans for `agent:kai` parents where all children are Done/In Review
4. Writes nudge: `/tmp/bot-delegation/requests/kai-nudge.json`
5. Bot-delegation-watchdog (`31b8d26e2f31`, every 1min) delivers to Kai

## Shared Agent Context

**Path:** `$PRISMATIC_HOME/work/active-oahu-static/.aot-agent-context.md`

Loaded by all Kai sub-agents on every execution (Step 0 in each skill). Contains: site structure, brand voice, design tokens, active branch state, fleet roster, review pipeline, CF Pages deploy delay. Eliminates cold starts.

## Creating Sub-Tasks (GraphQL)

```graphql
mutation {
  issueCreate(input: {
    teamId: "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef",
    title: "[AOT] <DOMAIN>: <specific action>",
    description: "Parent: <parent-identifier>\n<clear instructions>",
    labelIds: ["<sub-agent-label-id>"],
    stateId: "3d29ebe3-00cf-428b-b52a-bfecb5ae4410",
    parentId: "<parent-issue-id>"
  }) { success issue { identifier } }
}
```

## AGY Output Pitfalls

**tee kills output:** Never pipe AGY through `tee` — it buffers all output. Use direct `> file 2>&1` redirection.

**File-reference hangs:** `--print "Read /tmp/file..." --add-dir /tmp` hangs for large files. Use `--print "$(cat file)"` for inline embedding.

See `antigravity-cli-orchestration` skill → `references/agy-output-pitfalls.md` for full details.
