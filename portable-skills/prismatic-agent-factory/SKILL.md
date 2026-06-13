---
name: prismatic-agent-factory
description: > 
  Reusable factory for adding new personas and sub-agents to the Prismatic Engine.
  Covers the full lifecycle: persona selection from Antigravity Hub → agent skill creation → 
  Linear label → dispatcher routing → cron job → Kai notification wiring.
  Use this whenever Michael wants to expand the agent fleet.
category: agent-orchestration
---

# Prismatic Agent Factory

Standardized process for adding a new persona-based sub-agent to the fleet. Every step is required — skip none.

## Prerequisites
- The Antigravity-Orchestration-Hub repo cloned at `/tmp/Antigravity-Orchestration-Hub`
- The 72 personas cataloged in `src/engine/personas/*.ts`
- Linear API key available in environment
- Hermes cron system operational

## Factory Workflow (7 Steps)

### Step 1 — Select Persona
Pick a persona from the Hub. The 5 persona files are:
```
/tmp/Antigravity-Orchestration-Hub/src/engine/personas/
├── EngineeringPersonas.ts      (1-24)
├── Modeling3DPersonas.ts       (25-36)
├── AnimationVFXPersonas.ts     (37-48)
├── AudioCreativePersonas.ts    (49-60)
└── InfrastructurePersonas.ts   (61-72)
```

Extract persona details:
```bash
grep -A 30 "id: 'PERSONA-ID'" /tmp/Antigravity-Orchestration-Hub/src/engine/personas/*.ts
```

Map the persona to a Hermes agent name (kebab-case): `PERSONA-ID` → `agent-name`

### Step 2 — Create Linear Label
Labels follow the pattern `agent:<name>`. For Kai sub-agents: `agent:kai-<role>`.

```bash
export LINEAR_API_KEY=$LINEAR_API_KEY
LABEL_NAME="agent:kai-rolename"
curl -s -X POST "https://api.linear.app/graphql" \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { issueLabelCreate(input: { name: \\\"$LABEL_NAME\\\", teamId: \\\"b6fb2651-5a1f-4714-9bcd-9eb6e759ffef\\\" }) { issueLabel { id name } } }\"}"
```

### Step 3 — Create Agent Skill
Create the SKILL.md at `~/.hermes/profiles/orchestrator/skills/agent-orchestration/<agent-name>/SKILL.md`.

Template:
```markdown
---
name: <agent-name>
description: "<role summary>. Cron-only worker. Picks up agent:<label> tasks, executes, self-reviews, hands to AGY."
category: agent-orchestration
model: deepseek-v4-flash
provider: deepseek
---

# <Agent Display Name>

You are <Agent Name>, the <role> for Active Oahu Tours. Your persona is in `references/persona-definition.md` — the **<Persona Display Name>** (Persona #N) from the Antigravity-Orchestration-Hub. Follow those hard restrictions.

## Your Domain
- **Capability 1** — description
- **Capability 2** — description
- **Capability 3** — description

## Workflow (EVERY execution)
### Step 0 — Pre-verification
Check git log + grep for existing work. If pre-completed, skip to Step 4.

### Step 1 — Execute
Pull oldest `<label>` issue from Linear (Todo or In Progress). Post "executing" comment.
Rules specific to this domain.

### Step 2 — Self-Review
Verify output. Post "self-review: [PASS/NEEDS_FIX]".

### Step 3 — Handoff to AGY
Post final comment. Swap label: `<label>` → `agent:agy`.

### Step 4 — Cleanup
Pre-completed → `agent:done`. Executed → `agent:agy`.

## Pitfalls
- Domain-specific pitfalls
```

Also create `references/persona-definition.md` with the full persona from the Hub TypeScript file.

### Step 4 — Register in Dispatcher
Add to `agent_dispatcher.py` AGENT_CONFIG:
```python
"agent:<label>": {
    "executable": None,
    "mode": "signal",
    "timeout": 0,
    "next_label": "agent:agy",  # → AGY peer review
},
```

Add to SIGNAL_AGENTS set:
```python
SIGNAL_AGENTS = {..., "agent:<label>"}
```

If it's a Kai sub-agent, the dispatcher's existing KAI_SUB_LABELS callback will handle notifying Kai.

### Step 5 — Create Cron Job
```python
cronjob(
    action="create",
    name="<Agent Name> — agent:<label> worker (every 5min)",
    schedule="every 5m",
    deliver="local",
    model={"model": "deepseek-v4-flash", "provider": "deepseek"},
    skills=["<agent-name>", "autonomous-execution-discipline", "aot-agent-coordination"],
    enabled_toolsets=["terminal","file","search","web","skills","session_search"],
    prompt="""You are <Agent Name>, the <role> for Active Oahu Tours.
Load the <agent-name> skill and follow its workflow.

Pick up the oldest Linear issue labeled `agent:<label>` (Todo or In Progress).
0. Pre-verify
1. Execute
2. Self-review
3. Handoff to AGY → swap to agent:agy
4. Cleanup

Use deepseek-v4-flash. Post results to Linear comments."""
)
```

NOTE: The skills list MUST include `aot-agent-coordination` — without it, the agent lacks AOT brand voice, workspace layout, and nav rules. This was discovered during fleet testing (June 2026) when sub-agents ran without shared context.

### Step 6 — Wire Callbacks (Kai Sub-Agents Only)
If this is a Kai sub-agent (`agent:kai-*`):
1. Add to `KAI_SUB_LABELS` in dispatcher (line ~1525)
2. Add to `SIGNAL_AGENTS` in dispatcher (line ~1393)
3. The dispatcher auto-notifies Kai when issues appear

### Step 7 — Create Test Task
Verify the pipeline works:
```bash
# Create a test issue with the new label
curl -s -X POST "https://api.linear.app/graphql" \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "mutation { issueCreate(input: {
    teamId: \"b6fb2651-5a1f-4714-9bcd-9eb6e759ffef\",
    title: \"[TEST] <simple test task>\",
    description: \"Test task for <Agent Name>.\",
    labelIds: [\"<label-id>\"],
    stateId: \"3d29ebe3-00cf-428b-b52a-bfecb5ae4410\"
  }) { issue { identifier } } }"}'
```

Watch the cron's next cycle — the agent should pick it up within 5 minutes.

## Skills Inventory (What Sub-Agents Get)

Every sub-agent gets these skills loaded by their cron job:
1. **Their domain skill** (e.g., `kai-css-agent`) — the persona + workflow
2. **`autonomous-execution-discipline`** — pre-verification, review loop, cleanup

They do NOT get:
- Kai's interactive gateway skills
- HD/Human Design skills
- Revenue/compliance skills
- Infrastructure skills
- Calendar/booking skills

If a sub-agent needs shared context (AOT brand voice, workspace layout), add the `agy-aot-coordination` skill to their cron's skills list.

## Label ID Reference (GrowthWebDev Team)

| Label | ID |
|-------|-----|
| agent:kai | c4d929be-8d15-4482-b6d7-a5ed85aa2e73 |
| agent:kai-css | f246eb61-5e84-4594-8b31-249a588c5648 |
| agent:kai-content | 4da5ee04-607f-4188-b9e0-18af413ad62f |
| agent:kai-js | 61180755-4f1f-48fb-bedf-ddd02f7cffaf |
| agent:agy | 1b69d9c0-20a8-45b3-a594-771b8cba75a7 |
| agent:ned | 6e0400c9-fc04-4868-86e3-f3156821f413 |
| agent:fred | a43efb77-534a-4e39-8ff3-76f0e42019d1 |
| agent:done | a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b |
| agent:jules | 5bc301fb-e4dc-404c-97fb-290c49ed2528 |

## State ID Reference
| State | ID |
|-------|-----|
| Todo | 3d29ebe3-00cf-428b-b52a-bfecb5ae4410 |
| In Progress | 734901ee-58f0-457c-b9a0-f911c0da13a4 |
| Done | bbf71b3e-9a05-48ce-9418-df8b9c0b8fec |

## Pitfalls
## Pitfalls
- **ALWAYS query label IDs live** before creating a label — don't assume from memory. Use the query in `references/linear-label-ids.md`.
- **SIGNAL_AGENTS** must include the new label or the dispatcher's dedup will skip it. The set is at line ~1393 of agent_dispatcher.py.
- **Auto-transition exclusion** (line ~1482) must include the new label. The dispatcher auto-transitions labels to `next_label` for non-signal agents. If your new agent is signal-mode (cron-only), add it to the exclusion tuple: `("agent:fred", "agent:kai", "agent:ned", "agent:kai-css", ...)`. Forgetting this causes the dispatcher to bounce the issue to `agent:agy` or `agent:fred` before the agent ever executes — symptom: issue has wrong label within seconds of creation.
- **KAI_SUB_LABELS** must be updated for any Kai fleet addition. The dispatcher's AGY→Kai callback (line ~1525) only checks labels in this set.
- **Cron job uses local delivery** — no user notification on success (only errors surface). To verify: check Linear issue state/labels after the cron cycle.
- **Test task state must be Todo** — Backlog won't be picked up by the 5-min cron.
- **Persona reference must include the full system prompt** with HARD RESTRICTIONS block from the Hub TypeScript source.
- **Don't overwrite existing skills** — if the agent name exists, use `skill_manage(action='patch')`.
- **Dispatcher patches require restart** — the cron job reading `agent_dispatcher.py` picks up changes on next cycle. No explicit restart needed for script-mode crons.
- **Linear `--label` flag on `create-issue` does NOT work** via the linear_api.py script. Always use the raw GraphQL mutation with `labelIds` array directly. Labels applied via CLI flag end up empty.
