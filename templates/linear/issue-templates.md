# Linear Issue Templates — Prismatic Engine

Pre-filled issue descriptions for common Prismatic Engine task types.
Copy the relevant template when creating a new issue.

---

## Template: New Agent Bootstrap

**Title:** `[BOOTSTRAP] Create agent:<name> — <role description>`

**Labels:** `agent:ned`, `type:infra`

**Project:** Prismatic Engine

**Description:**

```markdown
## New Agent Bootstrap: `agent:<name>`

### Identity
- **Name:** [Agent name]
- **Role:** [Primary function]
- **Personality:** [2-3 adjectives]
- **Model:** [deepseek-v4-pro / gpt-5.5 / other]

### Lane Configuration
- **Write access:** `["<dir>/"]`
- **Read-only:** `["<other-dir>/"]`

### Checklist
- [ ] Create SOUL.md from template (`prismatic/templates/profiles/<agent>/SOUL.md`)
- [ ] Register lane in `PRISMATIC_ENGINE.yaml`
- [ ] Create Linear label `agent:<name>` (color: `#HEX`)
- [ ] Configure cron job (5-min interval, deepseek-v4-pro, deliver: local)
- [ ] Register in dispatcher (`agent_dispatcher.py` — 4 changes)
- [ ] Create skill file (`SKILLS/agent-<name>/SKILL.md`)
- [ ] Test: create issue → verify dispatch → verify execution

### Deliverables
1. `prismatic/agents/<name>/SOUL.md`
2. `PRISMATIC_ENGINE.yaml` lane entry
3. Linear label `agent:<name>`
4. Cron job configuration
5. `agent_dispatcher.py` entries
6. `SKILLS/agent-<name>/SKILL.md`
```

---

## Template: New Plugin Development

**Title:** `[PLUGIN] hermes-plugin-<name> — <description>`

**Labels:** `agent:ned`, `type:feature`

**Project:** Prismatic Engine

**Description:**

```markdown
## New Plugin: `hermes-plugin-<name>`

### Widget Description
[What the dashboard widget shows / what problem it solves]

### Plugin Type
- [ ] Status table (read-only data display)
- [ ] Control panel (interactive buttons/forms)
- [ ] Real-time stream (live event feed via SSE)
- [ ] Visualization (charts, graphs, diagrams)

### Checklist
- [ ] Create plugin directory: `plugins/hermes-plugin-<name>/`
- [ ] Write `manifest.json` with correct metadata
- [ ] Build `dashboard/index.html` (widget HTML)
- [ ] Build `dashboard/index.js` (widget logic — vanilla JS)
- [ ] Add `dashboard/style.css` (scoped styles)
- [ ] Build output to `dashboard/dist/` via `scripts/build-plugin.py`
- [ ] Optional: Write `plugin_api.py` (Python backend if needed)
- [ ] Test: `hermes dashboard reload` → verify widget renders
- [ ] Commit and push

### Deliverables
1. `plugins/hermes-plugin-<name>/manifest.json`
2. `plugins/hermes-plugin-<name>/dashboard/index.html`
3. `plugins/hermes-plugin-<name>/dashboard/index.js`
4. `plugins/hermes-plugin-<name>/dashboard/dist/` (built output)
5. `plugins/hermes-plugin-<name>/README.md`
```

---

## Template: Skill Migration

**Title:** `[MIGRATE] Port <skill-name> from external source → portable skill`

**Labels:** `agent:ned`, `type:docs`

**Project:** Prismatic Engine

**Description:**

```markdown
## Skill Migration: `<skill-name>`

### Source
- **Origin:** [Hermes profile skill / external repo / AGY research]
- **Source path:** [path to original SKILL.md]

### Target
- **Destination:** `portable-skills/<skill-name>/SKILL.md`
- **Or:** `SKILLS/<skill-name>/SKILL.md`

### Migration Checklist
- [ ] Copy source SKILL.md to target location
- [ ] Verify frontmatter: name, description, trigger conditions
- [ ] Update file paths in references/templates/scripts to be portable
- [ ] Add to `portable-skills/INSTALL.md` index (if in portable-skills/)
- [ ] Strip hermetic references (specific file paths, user names, API keys)
- [ ] Add portability note: "This skill is portable — copy to any Hermes profile"
- [ ] Test: load in a secondary profile, verify trigger fires

### Deliverable
Portable skill file at target path, ready for distribution.
```

---

## Template: Lane Governance Setup

**Title:** `[LANE] Set up lane governance for <repo> — PRISMATIC_ENGINE.yaml + pre-push hooks`

**Labels:** `agent:ned`, `type:infra`

**Project:** Prismatic Engine

**Description:**

```markdown
## Lane Governance Setup: `<repo>`

### Repository
- **Repo:** [repo path or URL]
- **Agents:** [list of agents that work in this repo]

### Checklist
- [ ] Create `PRISMATIC_ENGINE.yaml` with lane definitions
- [ ] Define write/read-only directories per agent
- [ ] Set branch prefix and commit prefix per agent
- [ ] Install pre-push hook from `scripts/pre-push-hook.py`
- [ ] Test: attempt direct push to main → should be BLOCKED
- [ ] Test: attempt lane violation → should be BLOCKED
- [ ] Test: valid push within lane → should PASS
- [ ] Document in `COMMIT_CONVENTION.md`

### Deliverables
1. `PRISMATIC_ENGINE.yaml`
2. `.git/hooks/pre-push` (installed from `scripts/pre-push-hook.py`)
3. `COMMIT_CONVENTION.md` (updated with lane rules)
```

---

## Template: 7-Step Loop Task

**Title:** `[LOOP] <task summary> — DECOMPOSE → DISPATCH → EXECUTE → REVIEW → INTEGRATE`

**Labels:** `agent:fred`, `pipeline:prismatic-loop`

**Project:** Prismatic Engine

**Description:**

```markdown
## 7-Step Loop Task: `<task summary>`

### Megaprompt
[High-level description of what needs to be accomplished]

### Expected Contracts
| Agent | Lane | Task |
|---|---|---|
| agent:ned | `src/`, `infra/` | [implementation task] |
| agent:agy | `content/`, `docs/` | [research/audit task] |
| agent:jules | `src/`, `docs/` | [PR/code task] |

### Quality Gates
- [ ] **Draft gate:** Syntax/lint passes, all files exist
- [ ] **Review gate:** AGY structural review, no blocker issues
- [ ] **Publishing gate:** Fred merge approval, provenance logged

### Loop State Machine
```
DECOMPOSE → DISPATCH → EXECUTE → REVIEW → (PASS) → INTEGRATE
                                        → (FAIL) → FEEDBACK → REFINE → EXECUTE (max 3 retries)
```

### Deliverables
1. Agent contracts (`.antigravity/contracts/<threadId>.json` × N)
2. Executed changes on feature branches
3. Review reports per agent
4. Merged commits on main
5. Updated Linear cards (Done state)
6. Provenance log entry

### Retry Budget
- Max 3 refinement loops per contract
- After exhaustion: escalate to human
```

---

## Usage

To create an issue from a template:
1. Copy the relevant template above
2. Replace `[placeholders]` with actual values
3. Create the issue via Linear API or dashboard

```python
# Bulk creation via Linear API
import os, json, urllib.request
key = os.environ['LINEAR_API_KEY']
team_id = 'b6fb2651-5a1f-4714-9bcd-9eb6e759ffef'
project_id = '<project-uuid>'

def create_issue(title, description):
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps({'query': f'''
          mutation {{
            issueCreate(input: {{
              title: "{title}",
              description: "{description}",
              teamId: "{team_id}",
              projectId: "{project_id}"
            }}) {{
              issue {{ id identifier title }}
            }}
          }}
        '''}).encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())
```
