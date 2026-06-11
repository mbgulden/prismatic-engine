---
name: golden-thread
description: >-
  Master project tracking framework. Load at session start to pick up all active
  projects, check Linear/GitHub for stale items, autonomously continue work,
  run daily/weekly golden thread reviews across all ventures, score thread
  health, produce prioritized action plans, and apply project-type-specific
  templates for cadences, stall detection, and success metrics. Absorbed
  golden-thread-review and golden-thread-templates.
---

# Golden Thread — Autonomous Project Continuity

## Trigger
Load this skill automatically at the START of every session, after any context compaction, or whenever you notice you're waiting for user direction. Also load when the user asks about project status, "what are we working on," or "keep working."

## The Golden Thread Concept
Every project has a "next_action" — a concrete, executable first step. If a project has no next_action, it's stalled. The golden thread is the linked list of next_actions across ALL projects that lets you immediately resume work without context-switching overhead.

## Daily Pipeline (Full Workflow)

For the full end-to-end pipeline — select stalled project → research via sub-agents → synthesize strategy → create tasks → execute → report — see `references/daily-pipeline-workflow.md`. Proven June 8, 2026 on HD Engine Core.

## Step 1: Load the Registry
Read `/home/ubuntu/work/project-registry.json`. This is the single source of truth.

**Registry schema note:** The top-level keys are metadata (`_schema`, `_last_updated`, `_session_summary`, `_last_sync`, `_linear_team`, etc.). Projects are split across TWO top-level containers:

- **`data['ventures']`** — Revenue-generating businesses (hd-engine-core, sentinel-itad, ai-consulting, active-oahu-mirror, etc.). Access via `data['ventures'][<key>]`.
- **`data['standalone_projects']`** — Infrastructure, ops, open-source, and tooling projects (agentic-swarm-ops, sovereign-sentinel, openhumandesignmcp, next-step-bot, google-ai-toolkit). Access via `data['standalone_projects'][<key>]`.

**Pitfall — wrong container:** `agentic-swarm-ops`, `sovereign-sentinel`, `next-step-bot`, `openhumandesignmcp`, and `google-ai-toolkit` are NOT in `data['ventures']` — they're in `data['standalone_projects']`. A direct `data['ventures']['agentic-swarm-ops']` raises KeyError. Always check both containers, or use a safe access pattern: `data['ventures'].get('agentic-swarm-ops') or data.get('standalone_projects', {}).get('agentic-swarm-ops')`. The `completed` array lives directly inside each project entry (no `_completed` prefix — both `ventures` and `standalone_projects` use `completed`).

### Step 2: Check External State
- **Linear**: Query `https://api.linear.app/graphql` (API key in env: `LINEAR_API_KEY`) for any issues with `agent:fred`, `agent:agy`, `agent:jules`, `agent:kai`, or `agent:codex` labels that need action
- **GitHub**: Run `gh issue list` and `gh pr list` across all project repos for stale items
- **Cron jobs**: Run `cronjob(action='list')` to verify scheduled workers are healthy. Check dispatcher (e2f1a3b4c5d6) for agent routing health.
- **AGY watchdog**: Run `python3 /home/ubuntu/work/agentic-swarm-ops/ops/agy_watchdog.py` to check for stuck AGY processes

## Step 2.5: Live Infrastructure Discovery (before concluding work is needed)

**CRITICAL — before you decide a project needs X built, check if X is already running.** A service may have been deployed by a prior session but never documented. The Linear issue may say "Build Stripe Checkout" while the payment server has been running for 4 days. This is the most common form of wasted session time.

Run this 4-point check on every project before generating tasks:

1. **Systemd services**: `systemctl list-units --type=service --state=running | grep -E 'hde|hd-|payment|report|api|mcp'`
2. **Port listeners**: `ss -tlnp | grep -E '800[0-9]|808[0-9]|809[0-9]|300[0-9]|500[0-9]'`
3. **Health checks**: `curl -s http://localhost:<port>/ping` or `/api/ping` or `/health` on each active port
4. **Tunnel routing**: `ps aux | grep cloudflared` → check if tunnels are running, then `cat ~/.cloudflared/config.yml` to see what hostnames are routed

**Example — HD Engine Core (Jun 7, 2026):** GRO-291 said "Create Stripe Checkout." The payment server (`hde-payment.service`) had been running on port 8002 since June 3. The tunnel was routing `reports.humandesignengine.com` but not the API or payment hostnames. The fix was NOT building a payment server — it was adding tunnel ingress rules + real API keys.

**When you find a running service:** Document it in the Linear issue comments with port, service name, and what's needed to complete the pipeline. Don't rebuild. The gap is usually configuration (env vars, tunnel routes, DNS), not code.

See `references/live-infrastructure-discovery.md` for the full checklist with curl commands and common fix patterns.

## Step 2.6: Verify Product Works Before Planning

**Before creating Linear issues or executing autonomous work on a project, verify the CURRENT product actually works.** A plan built on a broken foundation creates waste.

For each active project:
1. **Web apps**: Hit the live URL (`curl -sI https://domain.com`), check for 200. Verify all JS dependencies in `<script>` tags exist on disk.
2. **APIs**: Curl the health endpoint. Verify responses match expected schema.
3. **Games**: Confirm the core loop functions — start → play → progress → complete. Check for missing files, 404s, runtime errors.
4. **Bot/services**: `systemctl status`, check process is running, verify port listener.

**If the product doesn't work:** Fix runtime bugs BEFORE creating new Linear issues or planning architecture. The user's direction to "get back to X" means "make X work" — not "plan X's future."

**Real example (Jun 2026)**: Darius Star had 40 agent-assigned Linear issues spanning 4 phases of immersive audio and architecture refactor. But the game ended after biome 1 boss — no progression to level 2. 4 of 8 JS modules were missing from disk. Creating the issue plan was the wrong first action; diagnosing and fixing the broken gameplay loop was the right one.

## Step 3: Identify Stalled Projects
A project is stalled if:
- Its `next_action` is empty or unchanged for >48 hours
- It has hooks with `autonomous_work` items that haven't been touched
- Linear issues are all in "backlog" with no in-progress items
- **CRITICAL — No project description**: A project with issues but NO description/vision is directionless. The description IS the north star — without it, issues accumulate but don't converge. Every project MUST have a description answering: "What does success look like? What's the very next concrete step?" Fix: write a vision into the project description via `projectUpdate`. See `references/project-vision-template.md`.
- **CRITICAL — Zero issues**: The project exists in Linear but has ZERO non-completed issues. This means the plan was never fully scoped — someone created a project container but never broke down the complete journey into issues. An empty project is a stalled project. Fix: create a complete issue set that traces the full user journey from nothing to free value to email capture to paid conversion to subscription to enterprise. Every project needs enough issues to reach a customer paying.
- **ZERO-ISSUE DETECTION**: After querying Linear projects, explicitly count issues per project. Flag any project with state "started" but 0 non-completed issues — these are the empty shells. They are your highest-priority fix target because they represent plans that stopped at the project name.
- **PROJECT VISION RULE**: Every project must have a clear vision statement in its Linear description field. Michael's directive: "There is ALWAYS an issue that needs to be resolved. there is never 'nothing to do' for any project EVER." The description should answer: what does success look like? what's the north star KPI? what's the very next concrete step? Keep descriptions under 500 chars for GraphQL reliability. If a project has 0 issues or no description, fix it immediately.

## Step 3.5: Prior-Session Completion Verification (before working any stalled In Progress issue OR Todo `agent:fred` issue)

An issue may already be DONE even if the Linear card was never moved. This applies to two categories:

- **In Progress issues stalled 48h+**: Code may be merged, endpoints live, content published by a prior session that forgot to close the card.
- **Todo `agent:fred` issues**: Content/docs/files may have been generated by a different agent or cron run, and the issue was simply never moved to Done. The work exists on disk but the Linear label still says Todo.

Before executing ANY work on these issues:
1. **Check if the output already exists on disk** — `search_files` or `ls -la` for the expected deliverable path (e.g., `docs/character-voice-profiles.md`, `site/tours/kayak.html`). This is step 1 because it's the fastest signal.
2. **If file exists, verify completeness** — does it match the issue description's requirements? For content tasks: check section count, character/dimension coverage, presence of appendices. A 1,162-line doc with all 8 characters profiled is done; a 50-line stub is not.
3. **Read Linear comments** for completion signals like "substantially complete," "PR merged," "deployed."
4. **Check git branches/PRs** — `git branch -a`, `gh pr list --state merged|closed -R <repo>`. Look for the issue number in branch names.
5. **Run the test suite** if applicable.
6. **Probe live endpoints** if applicable.

If verification confirms the work is done: move the issue to Done, swap `agent:fred` → `agent:done` label, post a short verification comment, and report findings. Do NOT re-do completed work.

**Real example (Jun 2026)**: GRO-1013 asked for 8-character voice profiles. `docs/character-voice-profiles.md` already existed at 1,162 lines covering all 8 characters × 9 dimensions + 4 appendices. Prior session had completed it — needed only verification and Done transition.

**Batch close script:** When 3+ verified issues need closing, use `scripts/batch_close_issues.py`. Populate the `ISSUES` dict with {identifier: {uuid, commit, summary}}, update the state/label IDs if needed, and run via `terminal()`. The script handles: query current labels → build new label set (remove fred, add done) → move to Done → post structured verification comment → 150ms rate-limit breathing room. Stdlib only (no deps).

## Step 3.6: Pipeline Deliverable Quality Review (for review-labeled issues)

When an issue has a `pipeline:*` label or `agent:fred` label and is in Todo/Backlog with artifact files referenced in its description (e.g., "AGY Review: ... quality check + gap analysis"), do NOT skip directly to execution. The issue is a REVIEW step — it needs a quality assessment of deliverables already on disk.

Follow this process:

1. **Verify artifacts exist on disk** — Check every file path mentioned in the issue's description with `ls -la` or `read_file`. Missing artifacts = someone claimed work that doesn't exist. Flag this in the review.

2. **Assess completeness** against the deliverable review checklist in `references/pipeline-deliverable-review.md`:
   - Contact info completeness for lead-generation deliverables
   - Structural quality (tables, sections, depth)
   - Gaps that block the next pipeline phase
   - Whether the deliverable is ready for execution or needs more work

3. **Post a structured review comment** on the Linear issue with four sections:
   ```
   ## 🔍 Review: [Issue Title]
   
   ### ✅ Strengths
   [Bullet list of what was done well]
   
   ### ❌ Gaps Found
   [Bullet list of what's missing or incomplete — these are the actionable items for the next pipeline step]
   
   ### 📋 Verdict
   [One sentence: foundation ready? needs minor fixes? needs rework?]
   
   ### Next Step
   [Explicit next action — what happens now that the review is done]
   ```

4. **Move the issue to In Progress** (not Done) — this is a pipeline handoff. The review step is complete but the pipeline continues to the execution phase. The next agent picks up from here, addressing the gaps found.

See `references/pipeline-deliverable-review.md` for the full checklist template with per-deliverable-type scanning criteria.

## Step 3.7: Orphan Issue Detection and Assignment

Issues with no `project` assignment are invisible to project-scoped queries (`project(id:...) { issues { ... } }`). They accumulate in Todo/Backlog silently, never get reviewed, and can persist for weeks. **Always run this scan** after Steps 3.5–3.6.

**Detection:**
```
{ team(id: "b6fb2651-...") {
    issues(first: 100, filter: {state: {name: {in: ["Todo", "Backlog"]}}}) {
      nodes { id identifier title description labels { nodes { name } } project { id name } }
    }
} }
```
Filter client-side for `project === null`. These are the orphans.

**Classification by title/description content:**
Map each orphan to a project by scanning its title and description for domain keywords:

| Title contains | Likely project |
|---|---|
| eBay, inventory, fulfillment, listing, order | Sentinel IT Asset Logistics |
| Google Tools, Design Studio, Google Creative | AOT — Media Library & Content Engine |
| Prismatic, bolt-on, signal, nudge, Design Studio | Prismatic Engine |
| Jules, AGY, dispatcher, router, swarm | Agentic Swarm Ops Documentation |
| nav, menu, header, mobile, redesign | Active Oahu Tours — Website Overhaul |
| MCP, OpenHumanDesign, chart, bodygraph | OpenHumanDesignMCP |

**Batch assignment** (via `issueUpdate(input: {projectId: "..."})`):
```python
# Write the script to /tmp/, then execute via terminal (inherit env vars)
# Map each issue number → target project name → target project ID
assignments = {
    749: 'Sentinel IT Asset Logistics',
    742: 'Active Oahu Tours — Media Library & Content Engine',
    # ... etc
}
# Query issue UUIDs by number filter
# For each orphan: gql(f'mutation {{ issueUpdate(id: "{uuid}", input: {{ projectId: "{proj_id}" }}) {{ success }} }}')
```

**Pitfalls:**
- Use the `number` filter (integer, no "GRO-" prefix), NOT `identifier` filter (HTTP 400).
- Always verify the issue is truly an orphan (`project === null`) before assigning — don't reassign issues already in a project.
- Newly assigned issues remain in their current state (Todo/Backlog). The assignment just makes them visible to project-scoped queries.
- 10+ issues in one batch: write a `.py` file and execute via terminal. The `execute_code` sandbox doesn't inherit env vars.
- After assigning, update `project-registry.json` with the count of orphans resolved. This prevents the next session from re-scanning the same set.

## Step 4: Pick Up Work (Priority Order)
1. **Active project with most recent user attention** — continue where you left off
2. **Stalled project with highest revenue potential** — unstuck it (HD Engine API first)
3. **Infrastructure/maintenance** — run health checks, verify backups
4. **Content generation** — SEO pages, docs, code examples (always valuable, never blocked)

## Step 5: Execute Without Waiting
When you find autonomous work in a project's hooks, execute it immediately. Examples:
- "Run programmatic SEO page generator for 64 gates" → generate those pages
- "Add /v1/natal endpoint to mcp_server.py" → write that endpoint
- "Verify k3s node status" → run the health check

## Step 6: Update the Registry and Issue State

After completing any work, update `project-registry.json`:
- Move completed items to a `completed` array
- Update `next_action` to the next logical step
- Update `_last_updated` timestamp

**For issue state, choose based on task type:**

- **Single-step task** (no pipeline label): Move the Linear issue to "Done"
- **Pipeline task** (`pipeline:*` label, or the issue is step N of a multi-agent chain): Move the issue to "In Progress" — your step is done but the pipeline isn't. The next agent in the chain picks it up from there.
- **Escalated nudge task** (picked up from `/tmp/trigger-fred-work`): Save artifacts, comment with deliverables + what's next, move to In Progress, delete the trigger file. See `autonomous-execution-discipline` → Nudge Executor Pipeline Handoff.

## Linear Integration\n\n### Initiative Management\n\nInitiatives group projects under organizational objectives. Full mutation reference: `references/linear-api-usage.md#initiatives`.\n\n**Key mutations:** `initiativeCreate`, `initiativeUpdate`, `initiativeDelete`, `initiativeToProjectCreate` (link project), `initiativeToProjectDelete` (unlink — requires link object ID, not initiative ID).\n\n**Common pitfall:** `projectUpdate` does NOT accept `initiativeId`/`initiativeIds`. Use `initiativeToProjectCreate`. Linking an already-linked project returns \"project nesting conflict\" — delete the existing link first.\n\n**Batch reassignment:** Query projects with `initiatives { nodes { id name } }`, map client-side, reconcile with create/delete mutations, verify final state.\n\n**Query complexity cap:** Nesting `issues` inside `projects` hits the 10,000 limit with 30+ projects. Split: pull project structure first, then batch issue queries.\n\n- **API**:
- **API**: `https://api.linear.app/graphql` with `Authorization: $LINEAR_API_KEY` header (no "Bearer" prefix)
- **Team**: GrowthWebDev (key: GRO, id: b6fb2651-5a1f-4714-9bcd-9eb6e759ffef)
- **Viewer**: Michael Gulden (id: 4a8a76b2-63f2-4706-b501-3ab2f0709866)
- **To create a project**: mutation `projectCreate` with name, description, teamIds
- **To create an issue**: mutation `issueCreate` with title, description, teamId, projectId
- **To query**: query `{ projects { nodes { id name issues { nodes { id identifier title state { name } } } } } }`
- **Batch agent assignment**: When a project has 30+ issues in Backlog with no agent labels, use the batch update pattern in `references/linear-batch-agent-assignment.md` — query all issues, build an assignment map, and update labels + priorities in one pass with 150ms rate-limit breathing room. `sort:[{createdAt:Asc}]` / `sort:[{createdAt:asc}]` / `sort:createdAt` formats ALL fail with `GRAPHQL_VALIDATION_FAILED` as of Jun 2026. The `CreatedAtSort` enum appears to reject all documented values. **Workaround**: Omit `sort` entirely and rely on `first:N` — results come back in default order, which is roughly created-date for small teams. For sorted results, query all and filter/sort client-side.
- **To update state**: mutation `issueUpdate(id: "...", input: { stateId: "..." })` — state IDs: use query to find
- **To move an issue to a different project**: mutation `issueUpdate(id: "...", input: { projectId: "..." })`
- **To update project description**: mutation `projectUpdate(id: "...", input: { description: "..." })` — keep descriptions under 500 chars for GraphQL reliability
- **Initiatives API**:
  - Query: `{ initiatives { nodes { id name description status } } }` and `{ project(id: "...") { initiatives { nodes { id name } } } }`
  - Create initiative: `mutation { initiativeCreate(input: { name: "...", description: "...", color: "#..." }) { initiative { id name } } }`
  - Update initiative: `mutation { initiativeUpdate(id: "...", input: { name: "...", description: "..." }) { initiative { id name } } }`
  - Link project to initiative: `mutation { initiativeToProjectCreate(input: { initiativeId: "...", projectId: "..." }) { success } }`
  - Unlink project from initiative: `mutation { initiativeToProjectDelete(id: "<linkId>") { success } }` — uses the link ID from `project.initiativeToProjects.nodes[].id`, NOT the initiative ID
  - Delete initiative: `mutation { initiativeDelete(id: "...") { success } }`
  - "Project nesting conflict" error means project is already linked to an initiative — delete existing link first, then re-create
- **CRITICAL RULE**: One Linear project per venture. Never cram multiple products/businesses into one project. Each venture gets its own project, its own issues, and its own golden thread template. The project-registry.json maps ventures to Linear project IDs.

### Initiative Management (Linear API)

Initiatives group projects by company objective. Projects can belong to multiple initiatives.

**Query initiatives:**
```graphql
{ initiatives { nodes { id name description color status } } }
```

**Query a project's initiative links (get connection IDs):**
```graphql
{ project(id: "...") { name initiativeToProjects { nodes { id initiative { id name } } } } }
```
The `id` returned here is the **connection ID** (not the initiative ID) — you need this to delete the link.

**Create initiative:**
```graphql
mutation { initiativeCreate(input: { name: "Name", description: "..." }) { initiative { id name } } }
```

**Update initiative:**
```graphql
mutation { initiativeUpdate(id: "...", input: { name: "New Name" }) { initiative { id name } } }
```

**Delete initiative:**
```graphql
mutation { initiativeDelete(id: "...") { success } }
```

**Link project to initiative (`initiativeToProjectCreate` — NOT `initiativeToProject`):**
```graphql
mutation { initiativeToProjectCreate(input: { initiativeId: "...", projectId: "..." }) { success } }
```

**Unlink project from initiative (`initiativeToProjectDelete` — requires connection ID, NOT initiative ID):**
```graphql
mutation { initiativeToProjectDelete(id: "<connection-id-from-query-above>") { success } }
```

**Reassign a project to a different initiative:**
1. Query the project's `initiativeToProjects` to get the connection ID
2. Delete the old link: `initiativeToProjectDelete(id: "<connection-id>")`
3. Create the new link: `initiativeToProjectCreate(input: { initiativeId: "<new-id>", projectId: "<project-id>" })`
4. If step 3 fails with "project nesting conflict", the project is already linked to another initiative — repeat steps 1-2

**Pitfall — wrong mutation name:** `initiativeToProject` does NOT exist. The correct mutations are `initiativeToProjectCreate`, `initiativeToProjectDelete`, `initiativeToProjectUpdate`. The error message helpfully lists valid alternatives.

### Quick Issue Creation (Bulk)

When creating many issues across projects, use this pattern via `terminal` (NOT `execute_code` — sandboxed Python doesn't inherit env vars).

**Preferred method**: Write a .py file first with `write_file`, then execute with `python3 /tmp/script.py`. This avoids shell escaping issues with em-dashes, smart quotes, and markdown in task descriptions. See `references/linear-bulk-task-creation.md` for the template.

**Inline fallback** (simple titles only):

```bash
python3 - <<'PY'
import os, json, urllib.request
key = os.environ['LINEAR_API_KEY']
team_id = 'b6fb2651-5a1f-4714-9bcd-9eb6e759ffef'

def gql(query, variables=None):
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def create_issue(title, description, project_id):
    return gql("""
    mutation($t: String!, $d: String!, $team: String!, $proj: String!) {
      issueCreate(input: {title: $t, description: $d, teamId: $team, projectId: $proj}) {
        issue { id identifier title }
      }
    }
    """, {"t": title, "d": description, "team": team_id, "proj": project_id})

# Create issues in a loop
for title, desc in [("Title 1", "Description 1"), ...]:
    result = create_issue(title, desc, project_id)
    issue = result['data']['issueCreate']['issue']
    print(f"  ✅ {issue['identifier']}: {issue['title']}")
PY
```

For large batches (20+ issues across multiple projects), use `delegate_task` with `toolsets: ['terminal']` to create issues in parallel — 3 subagents, each handling 3 projects. Pass LINEAR_API_KEY via the shell environment (subagents DO inherit it through terminal).

## Linear Cycles & Issue Limits

**Cycles are time-boxed sprints** — enable via Team Settings → Cycles in the Linear dashboard. Our team uses 4 cycles: Foundation (24hr), Production (7-day), Growth (30-day), Empire (no end). Create issues with `cycleId` field in `issueCreate` mutation.

**Free plan issue cap:** When hitting `USAGE_LIMIT_EXCEEDED`:
1. Archive all Done issues (`state: {id: {eq: "DONE_ID"}}` → `issueArchive`) — frees slots without deleting work
2. If Done count is 0 (common — Backlog items accumulate, Done items get auto-archived), archive oldest Backlog items sorted by `createdAt` ascending. Target 15-20 to free meaningful capacity.
3. When archiving Backlog items: prefer stale content-generation tasks, old research tasks, and items blocked on unavailable resources. Never archive active In Progress items, Golden Thread audits, or project scaffolding issues.
4. After archiving, verify freed slots by attempting issue creation. If still hitting limit, archive another 10.
5. June 2026 example: hit cap at ~50 non-done issues. Had 0 Done issues to archive → archived 15 oldest Backlog (GRO-362–422 range, stale content/research tasks). Freed slots → created 15 new bootcamp issues.
4. June 2026: 0 Done, archived 15 oldest Backlog (GRO-362 through GRO-422), freed 15 slots for bootcamp issues (GRO-498 through GRO-512)
- **CRITICAL RULE**: One Linear project per venture. Never cram multiple products/businesses into one project. Each venture gets its own project, its own issues, and its own golden thread template. The project-registry.json maps ventures to Linear project IDs.

## Email Digest
- **Contact**: Michael@growthwebdev.com
- **Daily digest cron**: `cron:054c987cca7f` — runs at 9am, loads golden-thread skills, executes one autonomous task, sends structured digest
- **Digest format**: Work completed, stalled projects needing input, project pulse (all ventures), top 3 next actions
- **Alert triggers**: Project stalled >48h, new Linear issue created, deployment completed, revenue milestone, errors needing attention

## GitHub Integration
- **Authenticated as**: mbgulden (gh CLI)
- **To check PRs**: `gh pr list -R mbgulden/<repo>`
- **To check issues**: `gh issue list -R mbgulden/<repo>`
- **To create PR**: standard git flow; use gh CLI

## Step 7: Verify Complete Scope (The "All the Way to the End" Check)

Michael's directive: plans must lead ALL the way to the end — not just "build an endpoint" or "create a project container." The end scope is: a customer has paid and received value. For each project, verify:

1. **Does the project have issues in Linear?** If zero issues → create a complete set immediately (see Linear Bulk Creation pattern in `references/linear-bulk-issue-creation.md`).
2. **Do the issues span the full user journey?** Every project needs issues covering: initial interaction → free value delivery → email capture → paid conversion → subscription → enterprise expansion. Not just the first technical step.
3. **Is there a clear revenue unlock?** At least one issue should be about turning on payments (Stripe, checkout, billing). If no payment issue exists, the project can't make money.
4. **Are there "lots of free value" items AND "rewarding paid value" items?** Both must be present. Free brings users in; paid converts them.

### Empty Project Resolution Pattern
When you find 0 issues in a Linear project:
1. Map the complete user journey for that venture type
2. Create issues covering every stage (10-15 issues per project is healthy)
3. Use parallel delegate_task subagents (3 at a time, terminal toolset, `python3 - <<'PY'` to call Linear GraphQL)
4. Update project-registry.json with all new issue IDs
5. Set next_action to the first issue in the sequence

## Autonomous Work Patterns
These are always safe to execute without user approval:
- **SEO content generation**: gate pages, channel pages, profile pages, transit blogs. Load Google Drive strategy docs first, then follow the AI SEO/AEO generation workflow in `references/ai-seo-aeo-content-generation.md` — parallel research via delegate_task, AEO Quick Answer blocks, FAQPage schema, statistical density tables.
- **Documentation**: API docs, code examples, README improvements, tutorials
- **Testing**: run pytest, verify chart accuracy against reference data. For API services with key management, run the 9-test gauntlet (register, status, auth, noauth, 401, revoke, revoked-status, bad-tier, pro-tier) — see `references/api-key-gauntlet.md`.
- **Health checks**: GPU status, disk usage, k3s nodes, cron job health
- **Code quality**: lint fixes, dependency updates (non-breaking), security patches. For FastAPI services, use runtime DB availability checks (async `SELECT now()`) rather than module-level `_DEV_MODE` flags — `create_async_engine()` succeeds even when PostgreSQL is unreachable. See `references/fastapi-db-degradation-pattern.md`.
- **Research**: competitor monitoring, pricing changes, new market entrants. When analyzing competitors, load `references/seo-competitor-analysis-workflow.md` for the Ubersuggest-powered analysis flow. For connecting/reconnecting the Ubersuggest MCP server (OAuth with PKCE), see `references/ubersuggest-mcp-connection.md`. **CRITICAL**: Never make competitive claims without verifying with real data. For the nightly research queue batching pattern (parallel subagents, verify → update queue), see `references/research-queue-processing.md`.
- **AGY fallback**: When AGY web research times out (common with `--print` + search), pivot to direct execution from existing artifacts. See `references/agy-research-fallback-pattern.md` for the full workflow.
- **Full-stack asset audit**: For game/creative projects, cross-reference design docs against disk files to find missing visual, audio, environmental, and polish gaps. Create Linear issues and queue generation. See `references/full-stack-asset-audit.md` for the 5-phase methodology.
- **Content-first batch generation**: For tasks that generate large batches of artifacts (voice files, SEO pages, sprites) from source content, follow the 4-phase pattern: verify existing content → create master content document → generate artifacts (mock-first if API unavailable) → verify & commit. See `references/content-first-generation-pattern.md`.

## Pitfalls
- Don't wait for the user to ask you to do something you already know how to do
- **Plan multi-phase changes before executing**: For changes spanning 5+ pages or files across multiple page types, write the plan first (Linear issues + a strategy doc) before generating content. The user's directive: "make a schema plan and task series before you just throw it into the whole site." Single-step autonomous tasks (one endpoint, one fix) still execute immediately — this is about complex cross-cutting changes.
- Build, don't burn
- Don't let projects sit in "backlog" on Linear — move at least one to "in progress"
- Don't assume the user remembers context from last session — but YOU should
- If a project has zero next_actions, that's a bug — add one immediately
- The registry is the authority — if it's out of date, update it before anything else
- **Hermes sandbox path isolation (CRITICAL):** Hermes profiles use a sandboxed home directory (`~/.hermes/profiles/<name>/home/`), distinct from the real filesystem at `/home/ubuntu/`. The `~/work/` inside the sandbox is a SEPARATE directory — not the real `/home/ubuntu/work/`. This means `project-registry.json`, project repos, and other files may appear missing when they actually exist. **This affects BOTH `terminal()` with `~` paths AND `execute_code` sandbox code** — `os.path.expanduser("~")` in `execute_code` resolves to the sandbox home, not `/home/ubuntu`. Fix: symlink `~/work` → `/home/ubuntu/work` (one-time: `ln -s /home/ubuntu/work ~/work`). Verify with `readlink -f ~/work`. If files still appear missing after symlink, use absolute paths: `ls /home/ubuntu/work/` directly. Terminal commands using `~/work` go to the sandbox path — always verify with an absolute path if something is missing.
- **execute_code sandbox has NO environment variables:** The Python sandbox used by `execute_code` is isolated — `os.environ['LINEAR_API_KEY']`, `os.environ['CLOUDFLARE_API_KEY']`, etc. will all raise `KeyError`. This is NOT a missing-credentials problem — the sandbox simply doesn't inherit the orchestrator's env. **Workaround for Linear:** use `terminal()` directly with shell interpolation: `terminal('echo $LINEAR_API_KEY', timeout=5)['output'].strip()`. Then pass the value to `execute_code` via string interpolation. **Workaround for curl in execute_code:** write the script to a file with `write_file`, then execute via `terminal('python3 /tmp/script.py')` — the terminal DOES have env vars. **Pattern:** `execute_code` for pure logic/processing (data transforms, filtering, math), `terminal()` for anything needing env vars or API calls.
- **One Linear project per venture**: Never cram multiple products into one project. Each venture gets its own Linear project with its own issues and template type.
- **Venture priority is defined in the registry**: Currently AI Implementation Consulting is primary. Always check the registry before picking up work.
- **Non-HD ventures exist**: The registry tracks 14+ ventures. Active Oahu Tours, Sentinel ITAD, Asset Forge 3D, and AI Consulting are tracked alongside the 10 HD Engine projects.
- **AGENTS.md AND ISSUE DESCRIPTIONS are ASPIRATIONAL — verify claims against disk (CRITICAL):** AGENTS.md files, project docs, AND Linear issue descriptions (especially those written by other agents) often describe what SHOULD exist, not what DOES. File paths in an issue body may have been assumed by the issue creator rather than verified on disk. **Always cross-check before executing:** `search_files` for the expected paths, `ls` the directories, diff assumed vs actual. **Two real cases:** (1) darius-star (Jun 2026): AGENTS.md claimed 8 modules were \"already extracted\" — only 3 of 8 `.js` files existed on disk. (2) active-oahu (Jun 2026): GRO-1204 referenced `site/tours/kualoa-ranch-kayak.html` but `site/tours/` didn't exist — tour pages lived under `site/activities/`, `site/oahu-kayaking-and-beach-adventures/`, and top-level directories. Adapt mapping to reality rather than executing blindly against wrong paths. **The pattern:** (1) search_files for expected paths, (2) if paths don't exist, find actual files, (3) remap based on what's real, (4) only THEN dispatch work. See also Step 2.5 (Live Infrastructure Discovery) for the same pattern applied to services.\n- **Check what exists before generating**: Before executing any content-generation autonomous work (SEO pages, docs, reports), use search_files to verify what has already been produced. The cron runs daily — prior sessions may have completed the task. Generating duplicates wastes tokens and clutters the repo. This also applies to `global_hooks.idle_work_queue` entries — the queue may be stale. "Generate 121 SEO pages" was already done (154 existed). Verify queue items against reality before executing.
- **Execution agents picking `agent:fred` issues — filter by agent type (Jun 2026):** When an execution agent (Ned, Jules, Codex) has no agent-specific issues and falls back to `agent:fred`, it MUST skip issues whose title starts with "AGY —". These are AGY research/strategy tasks, not coding/execution tasks. Picking one wastes a cycle on verification-only closure (the deliverables were already produced by AGY). **Fix:** filter `agent:fred` results client-side by title prefix; pick the oldest non-AGY issue. If all `agent:fred` issues are AGY tasks, silently exit. **Real example:** Ned's cron run (Jun 11) found 4 `agent:fred` issues — all 4 were AGY tasks. GRO-1178 was already complete (8 files on disk from AGY's prior run). The cycle was a verification-then-close, not execution.
- **Ned cron batch-close pattern (Jun 2026):** When falling back to `agent:fred` and finding issues whose fixes are ALREADY committed to master but never closed in Linear: (1) verify each fix via source code inspection (use `delegate_task` subagents for parallel verification), (2) batch-close using `scripts/batch_close_issues.py`. This turns a "nothing to do" cycle into a productive board cleanup. 8 darius-star bugs (GRO-1173 through GRO-1187) were verified and closed in one Ned cron run using this pattern — all fixes already committed, just needed Linear state/label update.
- **read_file corruption pitfall**: read_file returns LINE_NUM|CONTENT format, easily corrupting files if written back. See references/programmatic-seo-generation.md for the fix command.
- **`execute_code` sandbox `read_file` kills JSON parsing (NEW Jun 2026):** The `execute_code` sandbox's `from hermes_tools import read_file` also returns LINE_NUM|CONTENT format. `json.loads(content)` fails with `JSONDecodeError: Extra data` because the line-number prefix (`1|`) is not valid JSON. **Fix:** NEVER use `execute_code` for JSON file mutations. Use `terminal()` with Python's built-in `open()`: `python3 << 'PYEOF'\nwith open('/path/to/file.json', 'r') as f:\n    data = json.load(f)\n# ... mutate ...\nwith open('/path/to/file.json', 'w') as f:\n    json.dump(data, f, indent=2, ensure_ascii=False)\nPYEOF`. Always validate after writing: `python3 -c "import json; json.load(open('/path/to/file.json'))"`. This applies to ALL JSON file operations in `execute_code` — not just the registry.
- **Research before building foundational components**: Before building complex foundational components (bodygraph rendering engines, auth systems, payment flows, chart computation), spend 5-10 minutes researching existing open-source solutions on GitHub. Use `curl` via terminal to query the GitHub API (no `web_search` tool — use `curl -s "https://api.github.com/search/repositories?q=..."`). Check stars, licenses, last update, and file structure. Do NOT build from scratch when a well-structured open-source foundation exists. See `references/bodygraph-rendering-research.md` for an example of the expected research depth — multiple repos compared, code examined, recommendation with rationale.
- **Golden Thread Evaluation Project**: When Michael asks for ongoing plan evaluation, create a dedicated Linear project with one audit issue per venture. Each audit asks: scope gaps, research depth, competitive alignment, missing bridges from current state to revenue, and whether the blueprint is a real end-to-end plan or a token stub.
- **Linear nested-filter is unreliable — two failure modes (Jun 2026):** Using `issues(filter: {state: {name: {in: [...]}}})` can fail two ways: (1) **Empty nodes** — the query succeeds but returns zero results even when issues exist. (2) **HTTP 500** — the query is rejected entirely with an Internal Server Error. Both failures occur for the same filter and have the same fix: **use `project(id: \"...\") { issues(first: N) }` without any state filter, then filter client-side in Python.** The project-scoped query is the most reliable path — it succeeded in every session where nested team/project filters failed. **Escalation path:** If even the project query returns 500, test with a minimal `{ viewer { id } }` query to verify the API key is still valid, then reduce `first` (try 30, then 10). The viewer query is the canary — if it returns 200, the API is up and the issue is query complexity. See `references/linear-api-usage.md` for the working query patterns and `references/linear-nested-filter-failure-modes.md` for a concrete worked example with the full fallback chain.
- **Stalled In Progress ≠ work needed**: An issue stalled in "In Progress" for 48h+ may already be DONE — code merged via PR, tests passing, endpoints live — but the Linear card was never moved to Done. Before working on ANY stalled In Progress issue, run this 4-step verification: (1) read all Linear comments for completion signals like "substantially complete" or "PR merged", (2) check git branches/PRs (`git branch -a`, `gh pr list --state merged|closed`), (3) run the associated test suite, (4) probe live endpoints if applicable. Only start work if verification fails. This avoids re-doing completed work and pushing duplicate PRs.
- **Linear stateId mutation can fail transiently**: The `issueUpdate` mutation with `stateId` may return `INPUT_ERROR: "Entity not found in validateAccess: stateId"` on first attempt even with a valid state ID. Retry the identical payload — it succeeds on the second call. This is a transient Linear API quirk, not an invalid state ID.
- **Linear mutations with GraphQL variables — scope matters (CORRECTED Jun 2026)**: The statement "all mutations with GraphQL variables return 500" was over-generalized and caused wasted debugging. The actual failure scope:
  - **Breaks with GraphQL variables**: `issueArchive`, `issueDelete`, `issueUpdate` (when passing `stateId` or `id` as variables). Fix: inline the ID directly: `mutation { issueArchive(id: "abc...") { success } }`.
  - **Works fine with variables**: `issueCreate`, `initiativeCreate`, `initiativeUpdate`, `initiativeToProjectCreate`. These accept variables without issue.
  - **`commentCreate` with variables — works for structured markdown (REFINED Jun 11, 2026)**: `commentCreate` WITH GraphQL variables (the `@file` + `{"variables":{"body":"..."}}` pattern) succeeds for structured markdown comments up to at least 750 chars including headers, code blocks, bold text, bulleted lists, and inline code — all verified working Jun 11 across 3 comments. The failure case is specifically **large markdown tables (~10+ rows)** — a 10-row table triggers HTTP 500 (observed Jun 9). **Rule**: use the `@file` + GraphQL variables pattern for all completion comments. Only simplify/shorten if the comment contains a multi-row table of 8+ rows — in that case, post the table as a follow-up or use a list format instead. The inline-interpolation approach (no variables) still breaks on shell escaping characters (quotes, em-dashes, newlines). See `references/linear-comment-mutation-500.md` for the worked example.
  - **Inline-interpolation failure (separate issue)**: Building a mutation string with f-strings containing long comment bodies (quotes, newlines, em-dashes) triggers HTTP 500 due to shell/syntax escaping. This is NOT the same failure mode as the issue-ID-variables bug.

  **Corrected rule**: Inline IDs for `issueArchive`/`issueDelete`/`issueUpdate` stateId. Use GraphQL variables for string payloads (comment bodies, descriptions). Hybrid example:
  ```python
  def post_comment(issue_id, body):
      return gql(f'''
          mutation($body: String!) {{
            commentCreate(input: {{ issueId: "{issue_id}", body: $body }}) {{
              comment {{ id }}
            }}
          }}
      ''', {"body": body})
  ```
- **Linear issueArchive mutation rejects variables**: Using GraphQL variable substitution in `issueArchive` (`mutation($id: String!) { issueArchive(id: $id) { success } }`) returns HTTP 500 across all issues. Inlining the ID directly in the mutation string works: `mutation { issueArchive(id: \"550e8400-...\") { success } }`. For batch archiving, build the mutation string per-issue rather than using parameterized queries.

- **Linear `labelIds` mutation requires the FULL set, not add/remove**: `issueUpdate(input: { labelIds: [...] })` expects an array of ALL label IDs the issue should have after the mutation — it is a SET operation, not an add/remove operation. Do NOT use `labelIds: { remove: [...], add: [...] }` (HTTP 400). Instead: (1) query current labels with `issue(id: "...") { labels { nodes { id name } } }`, (2) build the new array by splicing in/out the target label IDs, (3) pass the full array. **F-string pitfall**: nested `{{ labels: [\"...\"] }}` braces in f-strings cause JSON rendering errors due to f-string escaping. Use string concatenation or `json.dumps()` to build the label ID array, then template it into the mutation string via `%s` or `.format()`. Confirmed working pattern (Jun 2026):
  ```python
  new_ids = [id for id in current_ids if id != fred_label_id] + [done_label_id]
  payload = 'mutation { issueUpdate(id: "' + issue_uuid + '", input: { labelIds: ' + json.dumps(new_ids) + ' }) { success } }'
  ```

- **Chart tests need `timezonefinder` + correct invocation**: Running `pytest` on the OpenHumanDesignMCP test suite requires `timezonefinder` and the correct working directory. The full invocation:
  ```
  cd /home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server && \
    pip install --break-system-packages timezonefinder && \
    python3 -m pytest tests/ -x --tb=short -q
  ```
  NOTE: tests live in `hd-mcp-server/tests/` (NOT the repo root `tests/`), and this host has `python3` (not `python`). Without `timezonefinder`, 2 timezone tests fail with UTC fallback. With it installed: 28 passed, 0 failures.
- **`agent:done` issues stuck in ANY non-Done state (NEW Jun 2026):** Issues with `agent:done` label can be stuck in Todo, In Progress, OR Backlog — not just Todo, and not just orphans. In one session: 5 `agent:done` issues sat In Progress inside Agentic Swarm Ops (GRO-675, 673, 669, 665, 152) — fully completed but never moved. **Detection:** query team-level: `team(id:...) { issues(filter: {state: {name: {in: ["Todo","In Progress","Backlog"]}}}, first: 200) { nodes { id identifier title state { name } project { name } labels { nodes { name } } } } }` — filter client-side for `agent:done`. Move matches to Done. **Also covers orphans:** `agent:done` issues with no project assignment are invisible to project-scoped queries. The team-level query catches them too. Example: GRO-726–729 sat in Todo with `agent:done` and no project for days.

- **Cron `deliver: local` means invisible output (CRITICAL):** Cron jobs set to `deliver: local` write output to the cron log directory only — Michael never sees them. This is the default and it's wrong for LLM-driven jobs. When auditing cron health: check `deliver` field on every job. LLM-driven jobs (those with a `prompt`) should deliver to `origin` (current chat) or a specific Telegram chat. Script-only jobs (`no_agent: true`) can stay `local` if their output feeds other jobs (journal snapshots, data pipelines). In one session: 12 of 21 jobs delivered to `local`, and 4 were LLM-driven with useful output Michael never saw. After restructuring, 6 LLM jobs were rerouted. Routine audit: `cronjob(action='list')` → scan `deliver` field → reroute any LLM job set to `local`.\n- **Registry JSON trailing commas**: `project-registry.json` is standard JSON (not JSONC). Trailing commas after the last element of an object/array cause parse errors. Always validate with `python3 -c "import json; json.load(open(...))"` after editing.
- **Linear issue comment spam from monitoring crons**: Active issues can accumulate 40+ comments of identical cron monitor output (Jules Session Monitor fires ~30 min, Golden Thread hourly). When reading Linear comments for verification, the useful signal is buried under repeat posts. Strategy: read the first 3-5 comments (issue creation/scope) and the last 3-5 comments (latest status); skip the middle if they're repetitive monitor output. If an issue accumulates 40+ monitor comments, flag it — the monitor should move to a dedicated tracker issue so the working issue stays scoped. GRO-106 is the canonical example: 50 comments, 45+ were identical Jules session check posts; the actual completion signal was in comment #2 from May 31. See `references/linear-monitor-comment-spam.md` for the full case study and remediation options.
- **`issueSearch` is fully deprecated (Jun 2026)**: Returns `INPUT_ERROR: "deprecated"` for ANY query. The previous pitfall about `issueSearch(filter: {identifier: ...})` returning HTTP 400 is superseded — the entire endpoint is dead. **Replacement**: use `team(id: "...") { issues(first: 200, includeArchived: true) { nodes { ... } } }` with client-side filtering to find issues by identifier. Two-step pattern: (1) find the UUID via team-level query, (2) use singular `issue(id: "...")` for details/mutations. See `references/linear-api-usage.md` for the full pattern.

- **Linear identifier filter (`issues`) returns HTTP 400**: Queries like `issues(filter: {identifier: {eq: "GRO-109"}})` return HTTP 400 Bad Request. The `identifier` field does not support `eq`/`in` filters. `issueSearch` is also fully deprecated (see above). **Workaround A**: Query by project ID using `project(id: "...") { issues { nodes { ... } } }` and filter client-side by identifier. **Workaround B**: Use the singular `issue(id: "internal-uuid")` query with the issue's internal UUID (obtained from a team-level `issues(first: 200, includeArchived: true)` query). **Workaround C**: For ad-hoc checks, use `number` filter on top-level `issues`: `issues(filter: {number: {eq: 109}}) { nodes { id identifier ... } }` — `number` accepts integer (bare 109, no "GRO-" prefix). **Root cause**: The `identifier` field is a composite display field and does not support standard filter operators.

- `references/hermes-swarm-architecture.md` — Full agent capability matrix, label-based handoff protocol, and cron job architecture document. Load when orchestrating multi-agent workflows or reviewing swarm health.
- **Registry JSON corruption from multi-byte UTF-8 (emojis)**: Emoji characters like `✅` (`\\u2705`, 3 bytes in UTF-8) in JSON values cause Python's character-position and byte-position to diverge. When debugging JSON parse errors, `text[pos]` and `raw[pos]` can return DIFFERENT characters because `text` counts code points while `raw` counts bytes. Do NOT use mixed text/byte position comparisons when emojis are present — pick one encoding and stick with it. The symptom: every tool (hex dump, text search, `json.loads`) reports the error at a different position, making the bug seem to move.\n- **Hermes sandbox path isolation (CRITICAL):** Hermes profiles use a sandboxed home directory (`~/.hermes/profiles/<name>/home/`) which may contain a STALE copy of `~/work/` from profile creation time. The real filesystem at `/home/ubuntu/work/` is separate. **Symptom:** `project-registry.json` appears missing, only 4 directories visible, but `ls /home/ubuntu/work/` shows 20+. **Fix:** `mv ~/work ~/work.stale.$(date +%Y%m%d) && ln -s /home/ubuntu/work ~/work`. After this, `~/work/` resolves to the real directory. Check this BEFORE rebuilding anything that appears "missing."
- **demjson3 recovery for corrupted registry JSON**: When `json.loads()` rejects `project-registry.json` but manual quote-balance and brace-depth checks show the structure is correct, the file likely has an invisible encoding issue (stray backslash, emoji byte-offset corruption, or control character). Recovery: `pip install --break-system-packages demjson3`, then `data = demjson3.decode(content)` for lenient parsing, then `json.dump(data, f, indent=2, ensure_ascii=False)` to rewrite as clean standard JSON. This produced a valid 19-venture registry in one pass after 15+ failed patch attempts. Install demjson3 BEFORE starting a patch loop on the registry — attempting incremental text fixes on a multi-byte-corrupted file is a time sink.

## Alignment Document Processing
When the user has Google Drive docs containing strategic analysis or deliverables:
1. **Search**: `mcp_gdrive_drive_search` with query (e.g., "Alignment")
2. **Read all**: `mcp_gdrive_drive_read_file` on each — exported as markdown
3. **Assess**: strategic (info-only) vs deliverables (ready to use)
4. **Improve**: Fill placeholders, add concrete details. Delegate to subagents for parallel processing.
5. **Save locally**: Write to `~/work/research/ai-consulting/` (or appropriate domain subdirectory under `~/work/research/`)
6. **Register**: Add to `project-registry.json` under the relevant venture
7. **Footer**: Every deliverable ends with "Next action: [specific step]"
See `references/alignment-doc-workflow.md` for full pattern.

### General Business Intelligence Extraction (Non-Alignment)
For tasks asking you to confirm Drive access, extract business notes, or surface strategic content (not alignment deliverables):
1. **Multi-topic search**: Run `mcp_gdrive_drive_search` with diverse queries ("business notes", "consulting", "Gemini", project names like "Active Oahu", "Micron", "EZShare")
2. **Priority by recency**: Read most recently modified docs first — highest signal-to-noise. A doc updated today tells you what's top-of-mind.
3. **Check dedicated research folders**: Scan for folders (e.g., "Michael's Research with Gemini") via `query` + `mimeType: folder`. Then list their contents to find docs that loose searches miss.
4. **Breadth-first sampling**: Read 6-10 docs at summary depth (3000-5000 chars each) rather than deep-reading 2-3. You're prospecting for strategic signal, not studying architecture.
5. **Compile structured notes**: Save to `~/work/research/<domain>-notes-<YYYYMMDD>.md` with:
   - Source attribution (doc name, date, fileId)
   - Key bullet points per doc
   - "Next actions suggested by these notes" section at the bottom
6. **Register findings**: Update `project-registry.json` or feed into the `research/queue.json` if new strategic direction emerges
See `references/business-intelligence-extraction.md` for full pattern with worked example.

- **Project automation (`tasks.json`)**: For any project needing lint/build/deploy, use a `tasks.json` manifest + `tasks/` directory of Python/Bash scripts. Zero dependencies, works everywhere. See `references/tasks-json-project-automation.md` for the full template with lint.py, build.py, and deploy.sh patterns.

- **Cloudflare Tunnel Setup**
Michael uses Cloudflare for domains and tunnel. Two methods:
- **Token-based** (what Michael uses): Copy token from Zero Trust dashboard → `cloudflared tunnel run --token <TOKEN>`. Hostnames/routing configured in dashboard under Public Hostnames, NOT via `--url` flag.
- **Key gotcha**: Docker needs `--network host` to reach localhost. The `--url` flag is for quick testing only — production routing goes through dashboard.
- **Local service**: Tunnel only forwards. You need services listening on configured ports.
See `references/cloudflare-tunnel-notes.md` for full reference.

- **Website Rebuilds (Astro + Cloudflare Pages)**
Pattern: Research stack compatibility → check integrations → map migration path → document gotchas.
See `references/website-rebuild-astro-cloudflare.md` for the comprehensive template.

- **Tourism Schema Implementation**
When adding schema.org markup to a tour operator, rental, or activity site, use the hierarchy: TravelAgency (homepage) → TouristTrip (tours) + Product (rentals) + FAQPage (Q&A) + ItemList (hubs) + ContactPage. See `references/tourism-schema-implementation.md` for the full type map, injection pattern, and competitor landscape analysis.

## Static Site Migration Audit
Pattern: Pull sitemaps → audit every URL live vs mirror for 404s/redirects/duplicates → quantify traffic loss → fix missing routes → fix orphan pages → inject schema → deploy. See `references/site-migration-url-audit.md` for the complete workflow with detection scripts and fix patterns.

## WordPress → Static Mirror Migration (wget + CF Pages)
Faster alternative (hours not weeks): `wget --mirror` the entire WP site → clean artifacts → compress images → push to GitHub → deploy to CF Pages as static HTML. Produces an exact 1:1 copy with zero visual changes. Use this as a baseline before iterative improvements. Full workflow + 404 audit methodology: `references/static-mirror-migration.md`.

## Static Mirror Page Generation (Template-Based)
Once the mirror baseline exists, generate new pages that match the WordPress theme exactly:
1. Extract three template sections from the mirror homepage: HEAD (lines 1-`</head>`), BODY_TOP (from `<body>` to just before `.entry-content`), and BODY_BOTTOM (after `.entry-content` through `</body>`)
2. Save these as `site/_templates/head.html`, `body_top.html`, `body_bottom.html`
3. For each new page: clone the HEAD → regex-replace `<title>`, `description`, `og:*`, `twitter:*`, `canonical` → inject JSON-LD schema before `</head>` → append BODY_TOP → add unique `<div class="entry-content">...</div>` → append BODY_BOTTOM → close `</body></html>`
4. All CSS/JS paths are relative (`wp-content/themes/...`) — pages at root level resolve correctly; deep pages use `../../wp-content/...` (already handled by the mirror)
5. Deploy: commit to the mirror's git repo (branch `main`), push → CF Pages auto-deploys if connected

See `references/static-mirror-page-generation.md` for the full generation script pattern.
See `references/tourism-schema-injection.md` for batch JSON-LD schema injection across an existing mirror (page-type detection + injection templates).

## Static Site Deployment (Cloudflare Pages)
For any static site (landing pages, docs, SEO content), deploy to Cloudflare Pages — never serve static HTML through a tunnel. Pages is free, globally CDN-distributed, auto-deploys on git push, and stays online when the homelab is down. Dynamic services (API, MCP engine) stay on the tunnel.
See `references/cloudflare-pages-deploy.md` for the full deployment pattern.
See `references/cloudflare-pages-token-requirements.md` for API token permission requirements (the 7003 "Could not route" fix).
See `references/cloudflare-pages-domain-management.md` for adding custom domains to Pages projects via API (Global Key auth, DNS update, domain verification workflow).

## Golden Thread Review (daily / on-demand)

### Purpose
Take a step back from execution and evaluate the entire Linear board for golden thread health, duplicate/stale items, missing pieces that block revenue, and next-session/week priorities. Run at the start of each day or whenever Michael asks "how are we doing?"

### Step 8: Pull Full Linear State
Query all GRO issues with identifier, title, priority, state, labels, project, description.

### Step 9: Group by Golden Thread
Map each issue to its primary thread:

- **Human Design Engine** — HD product, API, reports, SEO, marketing, revenue
- **Active Oahu Tours Website** — site audit and Astro rebuild for activeoahutours.com
- **Active Oahu Community Site** — site rebuild for activeoahu.com
- **Your Hawaii Guide** — affiliate aggregator rebuild for yourhawaiiguide.com
- **Active Oahu Media Library** — photo/video indexing, Google Tools integration, social pipeline, shop
- **Hermes Agent Manager** — dashboard, plugins, swarm infrastructure, PTY, profiles
- **Orchestration Router** — agent routing, dispatch, task intake, handoff contracts
- **Sentinel ITAD** — IT asset logistics, resale, certifications
- **Local GPU/LLM** — model serving, Qwen, Ollama, vLLM, benchmarks
- **AI Consulting** — client SEO, consulting practice
- **Project Honeybadger** — AI agent commerce platform
- **Sovereign Sentinel** — homelab inventory, GPU fleet, observability

Additional ventures tracked in the registry map to these threads as they grow.

### Step 10: Score Each Thread
- 🟢 **Active & healthy** — issues being completed, clear path forward
- 🟡 **Stalled** — no recent completions, needs unblocking
- 🔴 **Cold** — all backlogged, needs user input or cancellation

Scoring heuristics: see `references/thread-health-scoring.md`.

### Step 11: Find Duplicates
Search for issues with similar titles/descriptions. Common duplicate patterns:
- Affiliate program: GRO-94, GRO-98, GRO-108
- PDF reports: GRO-95, GRO-102, GRO-103, GRO-107
- Scrum setup: GRO-1, GRO-2, GRO-3, GRO-4 (onboarding templates)

See `references/duplicate-issue-patterns.md` for known duplicates.

### Step 12: Identify Stale Items
- Issues created > 30 days ago with no activity
- Issues in "Todo" that should be "Backlog" or "Canceled"
- Issues blocked on unavailable resources (Synology, Google Takeout)

### Step 13: Produce Report
Format:

```
# 🔭 Golden Thread Review — [date]

## State of the Swarm
[total issues, by state counts]

## Thread Analysis (one section per thread)
- Status emoji + name
- Done count / Total count
- Key wins this session
- Missing pieces (top 3)
- Duplicates (if any)

## Recommended Actions
### Close (duplicates/stale)
### Immediate (today/tomorrow)
### This Week
### Defer (needs user input)
### Cancel
```

### Step 14: Assign Blocked Items to Michael
After the review, find issues blocked on user input and:
- Reassign them to Michael on Linear
- Add a comment with 🫵 emoji and the **exact action needed** — one sentence, no fluff
- The comment MUST answer: "What do I do, and what does it unblock?"

Format:
```
🫵 **Needs you, Michael.** [One-sentence action.]

| Field | Value |
|-------|-------|
| Command/action | [exact thing to run/decide] |
| Unblocks | [what ships next] |
```

Michael builds momentum by clearing noise. Blocked items with vague comments stay blocked forever — make it impossible to misunderstand what's needed.

### Step 15: Deliver
Post the review to the current chat. Offer to deep-dive any thread. Include a "🫵 Waiting On You" section at the bottom with the top 3-5 blocked items and their exact actions.

### Swarm Throughput Reporting
When the user asks "how much is the swarm accomplishing without you?", produce a compact autonomous throughput report:

```
## 🤖 Autonomous Swarm Throughput

### N Active Cron Jobs
| Job | Freq | Type | Status |

### Swarm Health
| Issue | Action |
```

**What to include:**
- Count of LLM-driven agents (consume tokens) vs script-only agents
- Each job's frequency, last status, and whether it's green/yellow/red
- Fixes applied to broken jobs (duplicates removed, errors re-triggered)
- Verdict: "X of Y agents running green"

This is NOT a deep cron log dump — it's a one-glance dashboard of autonomous activity. Michael uses it to gauge whether the swarm infrastructure is earning its keep between sessions.

### Review Notes
- This is a REVIEW, not execution. Don't start building during the review.
- The most valuable output is the duplicate/stale cleanup.
- When Michael says "keep going", refer back to the "Immediate" action list from the last review.
- Run this at start of day, before diving into execution.
- After executing a batch of items, run a quick update (no need for full re-review unless >24h).
- For content-heavy threads (Active Oahu, Human Design), the **expert-interview-content-production** skill governs how to turn Michael's recorded expertise into web pages, social posts, and schema markup. When a golden thread review identifies content gaps, create interview tasks using that skill's template.
- The **domain-portfolio-strategy** reference doc governs how to handle Michael's multi-domain web properties (split by purpose, consolidate hosting, zero content overlap).

### Review References
- `references/initiative-health-pulse.md` — Compact initiative-level dashboard format (🟢🟡🔴 tables). Use this instead of per-thread narrative when 7+ initiatives exist or after Linear reorganization.
- `references/linear-api-queries.md` — Working GraphQL queries, auth pattern, and `execute_code` sandbox gotchas
- `references/linear-initiatives-restructuring.md` — Full account reorganization pattern: audit → analysis → initiative design → bulk API mutations → cleanup
- `references/duplicate-issue-patterns.md` — Known duplicates, stale templates, and AGY migration issues already closed
- `references/thread-health-scoring.md` — Scoring heuristics for 🟢🟡🔴 thread status and re-analysis output format
- `references/domain-portfolio-strategy.md` — Three-domain clean architecture for Michael's web properties

## Project Templates

Every project in `project-registry.json` maps to one of these templates. Each template defines daily/weekly/monthly hooks, stall detection, success metrics, autonomous work, and complete scope checklists.

At session start, load the template for each active project → execute daily hooks → report on stall detection. If stalled: execute autonomous work immediately. If not stalled: pick the highest-impact weekly or monthly hook. Update the registry's `next_action` field after completing any work.

### Template: API / SaaS Product
**Applies to:** HD Engine Core, HD Dating Products, HD AI Lab

**Daily:** Check API uptime and error rates, review new signups and churn. If 0 new signups in 3 days: investigate marketing/reach. If errors > 1%: fix root cause before adding features.
**Weekly:** Ship one endpoint improvement or docs update. Review competitor pricing. Write one developer blog post or code example.
**Monthly:** Revenue vs projection review. Pricing optimization check. Customer feedback synthesis.
**Stall Detection:** No deploys in 7 days. No new signups in 5 days. Error rate > 5%.
**Success Metric:** MRR (Monthly Recurring Revenue)
**Autonomous Work:** Write API docs and code examples, add monitoring/alerting, optimize rate limiting, build SDK for one new language, run load tests.

### Template: Content / Automation Product
**Applies to:** HD Reports Engine, HD Growth Engine, HD Creator Tools

**Daily:** Check pipeline health (all automations running?). Verify latest content was delivered. If any pipeline failed: fix and replay.
**Weekly:** Add one new content template or variant. Review content performance (opens, clicks, shares). Optimize one automation step.
**Monthly:** Content calendar refresh. New distribution channel evaluation. Revenue per content piece analysis.
**Stall Detection:** Pipeline failures > 24h unresolved. No new content in 7 days. Zero revenue from content in 14 days.
**Success Metric:** Content-attributed revenue per month
**Autonomous Work:** Generate SEO pages (gates, channels, profiles), create new report templates, A/B test pricing/design, add new distribution channel, write content for next week's calendar.

### Template: Marketplace / Listing Product
**Applies to:** RapidAPI listing, Shopify App

**Daily:** Check listing health and reviews. Monitor conversion rate. Respond to any new reviews/questions.
**Weekly:** Optimize one listing element (title, description, screenshots, pricing). Research one new marketplace to list on. Competitive listing analysis.
**Monthly:** Revenue vs listing position correlation. Pricing tier adjustment. New marketplace evaluation.
**Stall Detection:** No reviews in 14 days. Conversion rate declining for 7+ days. Listing position dropping.
**Success Metric:** Monthly active subscribers from marketplace
**Autonomous Work:** A/B test listing copy, update screenshots, add FAQ entries, submit to new marketplaces, gather and showcase testimonials.

### Template: Consumer App
**Applies to:** HD Consumer App, HD Dating App

**Daily:** Check DAU (Daily Active Users) and new signups. Monitor crash rate and load times. Review app store ratings.
**Weekly:** Ship one UX improvement or bug fix. Review user feedback and feature requests. Post one social media update.
**Monthly:** Churn analysis. Feature prioritization review. Growth experiment results.
**Stall Detection:** DAU declining for 7+ days. Crash rate > 1%. No deploys in 14 days.
**Success Metric:** DAU and Premium Conversion Rate
**Autonomous Work:** Fix UI bugs, add onboarding tooltips, optimize load times, write release notes, create demo videos.
**Complete Scope Checklist (the plan must reach a paying customer):**
- [ ] Rendering engine (visual output — bodygraph, dashboard, whatever the core view is)
- [ ] User authentication (signup, login, password reset)
- [ ] User profile (save data, manage settings)
- [ ] Free value delivery (the thing users get before paying)
- [ ] Email capture/conversion path (free → email → upsell)
- [ ] Paid tier feature differentiation (what changes at each price point)
- [ ] Payment integration (Stripe checkout, webhooks, billing)
- [ ] Mobile responsiveness (the app must work on phones)
- [ ] Share/social features (viral growth mechanism)
- [ ] Premium tier upgrade flow

### Template: Enterprise / B2B Product
**Applies to:** HD Enterprise, HD Coach Platform, HD Education

**Daily:** Review inbound leads. Check enterprise customer health (usage, support tickets). Follow up on any outstanding proposals.
**Weekly:** Nurture 5 leads (email, case study, demo offer). Improve one piece of sales collateral. Check on 1 existing customer.
**Monthly:** Pipeline review: leads → opportunities → closed. Case study publication. Pricing/packaging review.
**Stall Detection:** No new leads in 14 days. No proposals sent in 21 days. Existing customer churn.
**Success Metric:** Qualified Pipeline Value and Closed MRR
**Autonomous Work:** Build case study templates, research target accounts, draft outreach emails, improve demo environment, create ROI calculator.

### Template: AI Consulting / Services Business 🚀 PRIMARY
**Applies to:** AI Implementation Consulting, any consulting/services venture where you sell expertise, not software.

**Daily:** Check for new leads or responses to outreach. Review any active pilot projects (on track? client happy?). Follow up on 1 outstanding proposal or warm lead.
**Weekly:** Send 3-5 outreach messages (email, LinkedIn, referrals). Improve one piece of sales collateral (proposal template, case study, ROI calculator). Check in with 1 existing client for feedback/expansion opportunities.
**Monthly:** Pipeline review: leads → proposals → closed → retainer conversion. Pricing review: are pilots converting to retainers? adjust if not. Network: attend 1 local event, reconnect with 2 dormant contacts.
**Stall Detection:** No outreach sent in 7 days. No new leads in 14 days. Active pilots with no client communication in 5 days. Proposals sitting un-sent for >3 days after drafting.
**Success Metric:** Monthly Recurring Revenue from Retainers + Pilot Revenue
**Autonomous Work:** Scrape business directories for new targets, draft outreach emails for new leads, polish proposal and MOU templates, research target industries for bottleneck patterns, generate ROI case studies from past work, update LinkedIn profile and content, liquidation/asset sales (servers, hardware) — treat as cash-flow injections.

### Template: Coaching / Service Product
**Applies to:** ShePlantedATree HD Coaching

**Daily:** Check booking calendar. Follow up on any incomplete bookings. Share one HD insight on social media.
**Weekly:** Send 3 follow-up emails to past clients. Create one piece of content (blog, video, social post). Review upcoming sessions and prep materials.
**Monthly:** Revenue vs capacity analysis. Client satisfaction review. Service offering refresh.
**Stall Detection:** No bookings in 14 days. Calendar empty for next 2 weeks. No content published in 21 days.
**Success Metric:** Monthly bookings and client retention rate
**Autonomous Work:** Draft social media content, create client resource PDFs, research partnership opportunities, update service descriptions, gather and format testimonials.

### Template: Open Source Project
**Applies to:** OpenHumanDesignMCP

**Daily:** Check GitHub: new issues, PRs, stars. Respond to any community questions. Run test suite.
**Weekly:** Merge or close stale PRs. Write one docs improvement. Tag a new release if warranted.
**Monthly:** Community growth metrics. Roadmap review. Dependency updates.
**Stall Detection:** No commits in 14 days. Open issues > 30 days without response. Test suite failing for > 24h.
**Success Metric:** GitHub stars and contributor count
**Autonomous Work:** Improve documentation, add test coverage, respond to issues, update dependencies, write changelog entries.

### Template: Infrastructure / DevOps
**Applies to:** Sovereign Sentinel Homelab, Agentic Swarm Ops

**Daily:** Health checks: GPU, disk, k3s nodes, cron jobs. Alert if anything > 80% utilization. Verify backups ran.
**Weekly:** Review logs for anomalies. Apply non-breaking security patches. Capacity planning check.
**Monthly:** Full backup verification. Cost optimization review. Architecture review.
**Stall Detection:** Health check failures > 24h unresolved. Disk > 90%. Backups failing.
**Success Metric:** Uptime and successful backup count
**Autonomous Work:** Run health check scripts, verify backup integrity, clean up old logs/docker images, update documentation, test disaster recovery.
**Pitfall — Orphan Process Port Binding:** When a systemd service is `inactive (dead)` but restart fails with `OSError: [Errno 98] Address already in use`, an orphan process is holding the port. The old service process died but left a child Python process still listening. Detect: `ss -tlnp | grep <PORT>` to find the PID. Fix: `sudo kill <PID>` then `sudo systemctl restart <service>`. Verify: `systemctl status <service>` shows `active (running)`. This is common after failed restarts, OOM kills, or SIGTERM timeouts — the init process dies but its forked children don't. For the full HD Platform service architecture and creation pattern (venv paths, PYTHONPATH, relative import fixes, health checks), see `references/hd-platform-service-management.md`.
**Pitfall — Port Monitoring Mismatch:** When a health check flags a port as DOWN for many consecutive checks, verify the actual port in the service config file (`/etc/systemd/system/<service>.service` → `Environment=PORT=...`) before assuming the service is broken. Monitoring metadata (registry `port_8001_status`) may reference the wrong port. Always cross-check with `ss -tlnp` and the service unit file.

### Template: Static Site / Landing Page
**Applies to:** Human Design Engine marketing site, Active Oahu Tours rebuild, any project whose primary deliverable is a static website.

**Deployment:** Cloudflare Pages (free, global CDN, auto-deploy on git push). NOT homelab HTTP server behind tunnel (unreliable, ties uptime to server). Tunnel is for dynamic services only: API endpoints, MCP engine, databases.
**Daily:** Check site is live: `curl -sI https://domain.com` → 200 OK. Verify no Worker auto-config corruption: `curl -s domain.com | head -1` should show raw HTML, not line-numbered output.
**Weekly:** Add one new page or content section. Check Google Search Console for indexing status. Review analytics for traffic patterns.
**Monthly:** SEO audit: are pages ranking? which keywords? Competitor site review: what are they doing that you're not? Content refresh: update stale pages.
**Stall Detection:** Site down > 1 hour. No new content in 14 days. Zero organic traffic growth month-over-month.
**Success Metric:** Monthly organic search traffic and conversion rate
**Autonomous Work:** Generate new SEO content pages, update existing pages with fresh data/stats, improve page load speed, add structured data (JSON-LD schema), create blog posts from research docs, submit sitemap to Google Search Console. For sitemap generation from a docs directory, see `references/sitemap-generation.md`.
**Deployment Pattern:** Push to GitHub → Cloudflare Pages auto-deploys. Output directory: `docs/` (no build command for static HTML). Framework Preset: **None**. Custom Domain: Add in Pages → Custom Domains tab.
**Pitfall — Worker Auto-Config:** Cloudflare may auto-create a Worker that corrupts HTML with line numbers. If the site looks unstyled despite valid CSS, check `curl domain.com | od -c | head -1` for digit-pipe prefixes. Fix: delete the Worker or remove its route binding. Pages serves clean HTML.

### Templates Usage
1. Every project in `project-registry.json` has a `template` field pointing to one of these types
2. At session start, load the template → execute daily hooks → report on stall detection
3. If stalled: execute autonomous work immediately
4. If not stalled: pick the highest-impact weekly or monthly hook
5. Update the registry's `next_action` field after completing any work
6. **Completeness check**: When auditing a project, verify it has issues covering every stage in its template's complete scope checklist. A project with 0 issues or issues that only cover stage 1 (build the thing) is an incomplete plan. Fill the gaps immediately using the Linear bulk creation pattern.
