# Batch AGY Dispatch Result Routing

**When to use:** Fred orchestrator sessions where 10+ AGY dispatch result files (`/tmp/agy-dispatch-GRO-*-result.md`) have accumulated and need routing to Done, comments, or worker agents.

**Proven:** Jun 13, 2026 — 26 dispatch results routed in one 15-min cron session.

## The 4-Phase Pattern

### Phase 1: Surface Scan (30 seconds)

```bash
# List dispatch files by recency
ls -lt /tmp/agy-dispatch-*-result.md | head -20

# Sample verdict from each file
for f in /tmp/agy-dispatch-GRO-*-result.md; do
  id=$(echo "$f" | grep -oP 'GRO-\d+')
  verdict=$(tail -20 "$f" | grep -i -E 'verdict|approved|needs.fix|DONE|COMPLETE|FAILED|status' | tail -3)
  size=$(wc -c < "$f")
  echo "=== $id ($size bytes) ==="
  echo "$verdict"
done
```

Categorize into:
- **Done-ready**: Research/review complete, dispatch result IS the deliverable. Move to Done.
- **Comments-only**: Results exist but issue needs human approval or further agent work. Post comment, keep state.
- **Route-back**: AGY found failures. Re-label to original worker (agent:ned, agent:kai, etc.).

### Phase 2: Batch Identify (1 query)

Query ALL related issues in ONE team-level call using the `number` filter with `in`:

```graphql
{ team(id: "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef") {
    issues(first: 100, filter: { number: { in: [735,741,749,872,...] } }) {
      nodes { id identifier title state { id name } labels { nodes { id name } } project { id name } }
    }
  }
}
```

Extract state IDs inline from the same response:
```graphql
{ team(id: "...") { states { nodes { id name type } } } }
```

This yields two maps: `uuid_by_identifier` and `current_state_by_identifier`. Use these to decide which issues need Done transitions vs. comments-only.

### Phase 3: Route (shell-level curl, NOT Python gql())

**⚠️ CRITICAL: Use `curl` directly, not Python's `gql()` helper.** The stdlib `gql()` function (from the quick-issue-creation template) sends `{'query': query_string}` without a `variables` key. `commentCreate` with `$body: String!` requires a separate `variables` object in the JSON payload — without it, Linear returns HTTP 400. Shell `curl` with inline mutations avoids this entirely.

Working pattern:
```bash
#!/bin/bash
KEY="$LINEAR_API_KEY"
DONE="bbf71b3e-9a05-48ce-9418-df8b9c0b8fec"
API="https://api.linear.app/graphql"

gql() {
  curl -s -X POST "$API" -H "Authorization: $KEY" -H "Content-Type: application/json" -d "$1" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if not d.get('errors') else 'ERR:'+str(d['errors'][0]['message'][:80]))"
}

# For each Done-ready issue:
gql '{"query":"mutation { c: commentCreate(input: { issueId: \"UUID\", body: \"Short summary.\" }) { success } }"}'
sleep 0.2
gql '{"query":"mutation { issueUpdate(id: \"UUID\", input: { stateId: \"'$DONE'\" }) { success } }"}'

# For comments-only (keep state):
gql '{"query":"mutation { c: commentCreate(input: { issueId: \"UUID\", body: \"AGY research complete. Report saved.\" }) { success } }"}'
```

**Key details:**
- 150ms `sleep` between comment + state transition avoids rate limit
- Inline the state ID as a shell variable (`'$DONE'`)
- Keep comment bodies under 200 chars — no markdown tables, no backticks. Shell escaping eats them.
- For longer comments, write to a temp file first and use `jq` to build the JSON payload

### Phase 4: Archive & Clean

```bash
# Archive dispatch results with date stamp
mkdir -p /tmp/agy-dispatch-archive/$(date +%Y-%m-%d)
mv /tmp/agy-dispatch-GRO-*-result.md /tmp/agy-dispatch-archive/$(date +%Y-%m-%d)/

# Verify no orphan trigger files remain
ls /tmp/trigger-fred-work /tmp/prismatic/nudge-fred 2>/dev/null && echo "WARNING: trigger files still exist"
```

## Category Decision Tree

| Dispatch Result Signal | Category | Action |
|---|---|---|
| "ALL VERIFICATIONS COMPLETED" or "APPROVED" | Done-ready | Comment + → Done |
| "NEEDS FIXES" or "FAILED" with specific issues | Route-back | Comment findings + re-label to worker |
| Human approval required (`requires:human-approval` label) | Comments-only | Post comment, keep state |
| Research/review complete, deliverable on disk | Done-ready | Comment + → Done |
| Sprite/asset tasks with `requires:human-approval` | Comments-only | Comment, keep state — human gates generation |

## Pitfalls

- ❌ **Python `gql()` function with variables**: The helper from the quick-issue-creation template sends `{'query': query}` — no `variables` key. Mutations with `$body` / `$id` parameters get HTTP 400. Use `curl` directly.
- ❌ **Long comments with markdown**: Shell escaping eats backticks, quotes, and tables. Keep under 200 chars for inline curl. Use `@file` pattern for longer bodies.
- ❌ **Forgetting `sleep` between operations**: 150ms minimum between comment + state transition on the same issue. Linear rate-limits burst mutations.
- ❌ **Missing state ID**: Always query team states in the same session. State IDs are team-specific and can change.
