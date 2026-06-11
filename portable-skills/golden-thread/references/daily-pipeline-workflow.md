# Daily Golden Thread Pipeline — Full Workflow

> Proven June 8, 2026 — HD Engine Core. 5-phase pipeline executed end-to-end in one session.

## The 5-Phase Pipeline

```
SELECT → RESEARCH → SYNTHESIZE → EXECUTE → REPORT
  ↓         ↓            ↓           ↓          ↓
registry  sub-agents   strategy    direct fix  structured
+ Linear  (parallel)   matrix +    or AGY      summary
                      task create              + blockers
```

## Phase 1: SELECT PROJECT

1. Read `/home/ubuntu/work/project-registry.json` for all projects with `next_action`
2. Query Linear for non-done issues across all projects
3. Pick the project with the **oldest next_action** (most stalled)
4. Tiebreaker priority: AI Consulting > HD Engine > Active Oahu > other revenue

## Phase 2: RESEARCH (Sub-agents, NOT AGY)

**CRITICAL:** AGY `--print` with web research consistently times out. Use `delegate_task` with `toolsets: ['web', 'terminal']` instead. Proven 3/3 successful on June 8, 2026.

Launch 3 sub-agents in parallel:
1. **Assumption Challenge** — "Challenge these assumptions with real evidence. Search competitor sites, pricing pages, forums."
2. **Strategy Discovery** — "Research 3-5 competing monetization strategies. What do adjacent markets do?"
3. **Gap Analysis** — "Compare our current artifacts vs what the market needs. What's missing?"

Each sub-agent produces a structured report saved to disk. Combine into one JSON at `~/work/research/agy-outputs/{project}-{date}.json`.

**Pitfall:** Sub-agents may report false positives about bugs. The strategy agent claimed the Stripe payment server had a "form-urlencoded encoding bug" — the actual code used correct `application/json` encoding. Always verify sub-agent bug claims against the source code before acting on them.

## Phase 3: SYNTHESIZE (Fred)

1. Validate all sub-agent claims against evidence (check citations, verify bug claims)
2. Build the strategy comparison matrix (weighted criteria: revenue ×3, speed-to-dollar ×2, code-alignment ×2, moat ×1, risk ×−1)
3. Select the winning strategy (highest weighted score)
4. Generate 3-5 concrete Linear tasks with test rubrics (unit, integration, revenue, assumption checks)
5. Create tasks via Linear GraphQL API (`issueCreate` mutation)
6. Move the top-priority task to Todo state

**Task creation pattern:** Write a Python script to `/tmp/` and execute via `terminal()`. The `execute_code` sandbox lacks env vars (`LINEAR_API_KEY`, etc.). Use `terminal('python3 /tmp/script.py')` instead. Always verify tasks were created with `json.load()` validation.

## Phase 4: EXECUTE

**Decision tree for execution method:**

| Task type | Method | When |
|---|---|---|
| Simple code fix (<50 lines) | **Direct execution** (patch tool, terminal) | Single-file changes, error handling, config fixes |
| Multi-file build | AGY `--print` with local file refs | 3+ files, new features, architecture changes |
| Documentation/report | Write file directly | Research reports, blocker docs, runbooks |
| System verification | terminal() health checks | Service probes, DNS checks, pipeline testing |

**After execution:**
- Post results as a Linear comment on the task
- Move task to In Progress (pipeline handoff) or Done (standalone task)
- Restart any affected services (`sudo systemctl restart <service>`)

## Phase 5: REPORT

Deliver structured summary with:
- Assumption challenges (CONFIRMED/CHALLENGED/FALSE with evidence)
- Strategy comparison matrix
- Winning strategy with rationale
- Tasks created (with identifier links)
- Execution results (pass/fail per rubric check)
- Blockers requiring Michael input (🫵 format with exact actions)

**Blocker format:**
```
🫵 **Waiting on Michael:** {exact action needed}
1. Command: {exact thing to run/configure}
2. Unblocks: {what ships next}
3. Time estimate: {X minutes/hours}
```

## Registry Update

After the pipeline completes, update `project-registry.json`:
- Update `next_action` on the project with new status
- Add `_last_pipeline_run` timestamp
- Update `_last_updated`

Use a Python one-liner via terminal for JSON mutation:
```bash
python3 -c "import json; ...; json.dump(data, open('...', 'w'), indent=2)"
```

## Pre-Flight Infrastructure Discovery

Before any pipeline run, verify what's already deployed (Step 2.5 from golden-thread):
1. `systemctl list-units --type=service --state=running | grep -E 'hde|payment|report|api'`
2. `ss -tlnp | grep -E '800[0-9]|808[0-9]'`
3. `curl -s http://localhost:<port>/ping` on each active port
4. `ps aux | grep cloudflared` for tunnel status

**Also check static assets:** `search_files(target='files', pattern='chart-calculator*.html', path='~/work/')` — a consumer frontend may already exist that wasn't discovered by the service-level checks alone.

## Key Learnings from June 8, 2026 Session

1. **3 parallel sub-agents completed in ~5 min** — all produced comprehensive, structured research
2. **Infrastructure checks found 4 running services** — API (8000), Payment (8002), Reports (8081), Tunnel (cloudflared)
3. **Chart calculator already existed** — `docs/hd-engine/free-tools/chart-calculator.html` with full CSS/JS. Discovery via static file search would have saved time.
4. **Sub-agent false positive** — claimed Stripe encoding bug where code was actually correct. Verify before acting.
5. **Payment server fix was 3 lines** — graceful error handling for missing API keys. Simple enough for direct execution.
6. **Linear task creation via terminal Python script** — 4 tasks created, 2 moved to Todo, 1 executed immediately.
