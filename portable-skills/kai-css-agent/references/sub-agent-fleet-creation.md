# Sub-Agent Fleet Creation Pattern

How to create a fleet of specialized cron-only workers from existing Linear labels. Used to build the Kai-CSS / Kai-Content / Kai-JS fleet (2026-06-13).

## When to Use

When an orchestrator agent (like Kai) is the only execution node and is bottlenecked — spin up 2-5 specialized cron-only workers, each with a narrow domain, their own Linear label, and a Deepseek-v4-flash model.

## Pattern

### 1. Labels Must Already Exist
Query Linear for existing labels:
```bash
export LINEAR_API_KEY=...
TEAM_ID="b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"
curl -s -X POST "https://api.linear.app/graphql" \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"query { issueLabels(first: 50, filter: {team: {id: {eq: \\\"$TEAM_ID\\\"}}}) { nodes { id name } } }\"}"
```

If labels don't exist, create them via the Linear UI (not programmatic — label creation via API is unreliable).

### 2. Create One Skill Per Agent
Each skill defines:
- **Domain** — exactly what files/types of work this agent handles
- **Tech stack** — what tools, frameworks, conventions apply
- **Workflow** — Step 0 (pre-verify), Step 1 (execute), Step 2 (self-review), Step 3 (handoff to AGY), Step 4 (cleanup)
- **Pitfalls** — domain-specific gotchas (e.g., "never touch nav" for CSS agent)
- **Collaboration** — how the orchestrator creates tasks for this agent

Template: see `kai-css-agent`, `kai-content-agent`, `kai-js-agent` skills.

### 3. Create One Cron Job Per Agent
```bash
cronjob action=create \
  deliver=local \
  schedule="every 5m" \
  model='{"model":"deepseek-v4-flash","provider":"deepseek"}' \
  skills='["<skill-name>","autonomous-execution-discipline"]' \
  name="<Agent Name> — agent:<label> worker (every 5min)" \
  prompt="You are <Agent Name>, the <domain> specialist for Active Oahu Tours. Load the <skill-name> skill and follow its workflow exactly.

Pick up the oldest Linear issue labeled \`agent:<label>\` (state: Todo or In Progress). Follow the 4-step workflow:
0. Pre-verify — check git log and existing work
1. Execute — <domain-specific rules>
2. Self-review — <domain-specific checks>
3. Handoff to AGY — swap label to agent:agy for peer review
4. Cleanup — if pre-completed, move to Done with agent:done

The project repo is at <repo-path>. Use deepseek-v4-flash. Post all results to Linear comments."
```

### 4. Add Labels to Dispatcher Routing Table
In `agent_dispatcher.py`, add to `AGENT_CONFIG`:
```python
"agent:<label>": {
    "executable": None,
    "mode": "signal",
    "timeout": 0,
    "next_label": "agent:agy",  # <Agent> → AGY review
},
```

### 5. Double-Check Loop (built into workflow)
```
Orchestrator creates task (agent:<label>)
    ↓
Sub-agent picks up → executes → self-reviews
    ↓
Sub-agent swaps to agent:agy
    ↓
AGY peer-reviews → swaps to agent:<orchestrator-label>
    ↓
Orchestrator approves → agent:done
```

## Fleet Created (2026-06-13)

| Agent | Cron ID | Label | Skill |
|-------|---------|-------|-------|
| Kai-CSS | `ace8ecd3ef53` | `agent:kai-css` | `kai-css-agent` |
| Kai-Content | `2ac45086e335` | `agent:kai-content` | `kai-content-agent` |
| Kai-JS | `4634d607c484` | `agent:kai-js` | `kai-js-agent` |

## Pitfalls
- **Cron-only, NOT gateway** — these are leaf workers with no user interaction. Don't create gateway profiles.
- **deepseek-v4-flash** — cheaper and fast enough for focused domain work. Don't use pro for sub-agents unless tasks require deep reasoning.
- **Always use `autonomous-execution-discipline`** as the second skill — prevents the "what should I do next" stall pattern.
- **Test with a real task** — create one issue with the sub-agent's label to verify the full pipeline before scaling.
- **Labels must exist before cron creation** — cron jobs fire immediately and will error if the label query returns nothing.
