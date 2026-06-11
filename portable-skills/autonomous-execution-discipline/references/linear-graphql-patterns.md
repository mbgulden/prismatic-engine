# Linear GraphQL API Patterns

Quirks and working patterns discovered through trial and error (June 2026).

## Authentication

**Do NOT use `Bearer` prefix.** Linear rejects it with "remove the Bearer prefix from the Authorization header."

```bash
# WRONG
curl -H "Authorization: Bearer $LINEAR_API_KEY" ...

# RIGHT
curl -H "Authorization: $LINEAR_API_KEY" ...
```

## Querying Issues

### By UUID (simplest — when you have the issue UUID)

```graphql
query { issue(id: "cc1332cd-79d9-482c-997f-eb95c6660b02") {
  id identifier title description
  state { name }
  assignee { name }
  labels { nodes { id name } }
  priority
  team { id }
  url
} }
```

Use this when you already have the issue UUID from a trigger file, previous query, or nudge payload. No team-scoped filtering needed — direct access.

### By identifier (GRO-429) — when you only know the human-readable ID

`issueSearch` is **deprecated**. `identifier` is not a valid field on `IssueFilter`.  
Use team-scoped queries with `number`:

```graphql
# Get team ID first
query { teams { nodes { id name key } } }

# Then query by issue number within that team
query { team(id: "b6fb2651-...") {
  issues(filter: { number: { eq: 429 } }) {
    nodes { id title description state { name } }
  }
} }
```

### Sorted/filtered issue listing (for autonomous workers)

When querying for the oldest pending issue (e.g., Cron Ned pattern):

```graphql
# Use filter + first limit WITHOUT sort — default ordering is sufficient
query { issues(
  filter: {
    labels: { name: { eq: "agent:fred" } },
    state: { name: { eq: "Todo" } }
  },
  first: 1
) { nodes { id identifier title description } } }
```

If ordering IS needed, use `orderBy` (simpler, works as a plain string):
- ✅ `orderBy:createdAt` — simplest, default ascending (oldest first)
- ❌ `sort:createdAt` — "Expected value of type [IssueSortInput!]"

If you must use `sort` (rare), the syntax requires an array of `IssueSortInput` objects, each with a nested `{ order: Ascending }` value:
- ❌ `sort:[{createdAt:asc}]` — "Expected value of type CreatedAtSort"
- ❌ `sort:[{createdAt:Asc}]` — same error (not PascalCase abbreviation)
- ✅ `sort:[{createdAt:{order:Ascending}}]` — PascalCase enum: `Ascending`/`Descending`

**Recommendation:** Prefer `orderBy:createdAt` over `sort`. For `first:1` queries with label/state filters, you can also omit ordering entirely — the default produces the oldest-created issue for most teams, which is correct for FIFO autonomous execution.

### Filter by specific issue numbers (lookup by GRO-NNN)

When you know a set of specific GRO numbers and need to check their state/labels:

```graphql
query {
  issues(filter: { number: { in: [847, 848, 850] } }, first: 5) {
    nodes {
      id identifier title state { name } labels { nodes { name } }
    }
  }
}
```

The `in` operator on `number` returns issues matching any of the listed numbers. This is the fastest way to check whether a batch of known issues are Done, Todo, or Backlog — no team-scoping needed, no `identifier` filter (which isn't supported on IssueFilter).

### Compound filters with `and`

For range queries (e.g., issues NUMBER 845–850), Linear rejects duplicate field names in a single filter object:

```graphql
# ❌ Two "number" fields in one object — rejected
filter: { number: { gte: 845 }, number: { lte: 850 } }

# ✅ Use and:[] compound
filter: { and: [ { number: { gte: 845 } }, { number: { lte: 850 } } ] }
```

The `and` compound wraps an array of filter objects that all must match. Also works for combining label + state + number filters in complex queries.

### By state

State IDs are not standard — query the team's workflow:

```graphql
query { team(id: "TEAM_ID") {
  states { nodes { id name type position } }
} }
```

Typical states: Backlog, Todo, In Progress, In Review, Done, Canceled, Duplicate.

### Get team labels (for label mutations)

```graphql
query { team(id: "TEAM_ID") {
  labels { nodes { id name } }
} }
```

Returns UUIDs for all team labels — needed before you can add/remove specific labels.

## Mutations

### Update issue state

```graphql
mutation {
  issueUpdate(id: "ISSUE_UUID", input: { stateId: "STATE_UUID" }) {
    success
  }
}
```

### Create comment on an issue

```graphql
mutation ($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}
```

Variables: `{"issueId": "ISSUE_UUID", "body": "markdown text here"}`. The `comment { id }` in the return lets you verify the comment was created (the UUID is unique per comment, not per issue).

### Swap issue labels (remove old, add new in one call)

Labels are REPLACED entirely via `labelIds` — you cannot add/remove individual labels incrementally. To swap `agent:fred` for `agent:done`:

```python
# 1. Get current labels from issue query (above)
# 2. Build new list: all current labels EXCEPT the one to remove, PLUS the one to add
# 3. Send the complete replacement list

mutation ($issueId: String!, $labelIds: [String!]!) {
  issueUpdate(id: $issueId, input: { labelIds: $labelIds }) {
    success
    issue { id labels { nodes { id name } } }
  }
}
```

Variables: `{"issueId": "UUID", "labelIds": ["new-uuid-1", "new-uuid-2"]}`. The full list replaces ALL existing labels — if you pass only `["agent:done"]`, the issue ends up with ONLY that label and drops all others.

### Create issue with parent

```graphql
mutation CreateIssue($teamId: String!, $title: String!, $description: String!, $parentId: String) {
  issueCreate(input: {
    teamId: $teamId
    title: $title
    description: $description
    parentId: $parentId
  }) {
    success
    issue { id identifier title }
  }
}
```

## Preferred API Call Pattern (Python + requests)

**Use `python3 -c` with `requests` library from terminal()** — cleaner than subprocess+curl:

```python
python3 -c "
import os, json, requests
api_key = os.environ['LINEAR_API_KEY']

# Query
query = '{ issue(id: \"UUID\") { id identifier title state { name } labels { nodes { id name } } team { id } } }'
resp = requests.post('https://api.linear.app/graphql',
    headers={'Authorization': api_key, 'Content-Type': 'application/json'},
    json={'query': query})
data = resp.json()

# Mutation with variables (avoids all shell quoting issues)
mutation = '''
mutation (\$issueId: String!, \$body: String!) {
  commentCreate(input: { issueId: \$issueId, body: \$body }) {
    success
    comment { id }
  }
}
'''
resp = requests.post('https://api.linear.app/graphql',
    headers={'Authorization': api_key, 'Content-Type': 'application/json'},
    json={'query': mutation, 'variables': {'issueId': 'UUID', 'body': 'Comment text'}})
print(json.dumps(resp.json(), indent=2))
"
```

Key advantages over `curl`:
- No shell-escape hell for GraphQL strings with curly braces and quotes
- Variables dict is serialized automatically by `requests`
- Can use Python string formatting for dynamic values
- The `\$issueId` syntax escapes the `$` from shell expansion inside `python3 -c`

## Batch Updates from Shell

Looping with `curl` in a for-loop works fine — no rate limiting observed at this scale (9-10 sequential mutations).

```bash
for ID in uuid1 uuid2 uuid3; do
  curl -s -H "Authorization: $LINEAR_API_KEY" \
    -H "Content-Type: application/json" \
    -X POST https://api.linear.app/graphql \
    -d "{\"query\":\"mutation { issueUpdate(id: \\\"$ID\\\", input: { stateId: \\\"$STATE\\\" }) { success } }\"}"
done
```

## Shell Quoting (CRITICAL)

**Never construct GraphQL queries with inline shell escaping.** JSON strings containing GraphQL mutations with nested curly braces and escaped quotes are nearly impossible to get right from bash. Three options:

### Option A: curl with `@file` (simplest for shell-only environments)

Write the JSON payload to a temp file, then `-d @file` — avoids ALL shell escaping:

```bash
# Write payload to file (using Python for clean JSON generation)
python3 -c "
import json
query = '''query {
  issues(filter: { labels: { name: { eq: \"agent:fred\" } }, state: { name: { eq: \"Todo\" } } }, first: 5) {
    nodes { id identifier title description }
  }
}'''
with open('/tmp/linear_query.json', 'w') as f:
    f.write(json.dumps({'query': query}))
"

# Call with @file — no escaping needed
curl -s -X POST https://api.linear.app/graphql \
  -H 'Content-Type: application/json' \
  -H "Authorization: $LINEAR_API_KEY" \
  -d @/tmp/linear_query.json | python3 -m json.tool
```

This is the simplest approach for cron jobs and shell scripts where you don't want to install `requests` or deal with `python3 -c` escape hell.

### Option B: Python `subprocess` (for scripts)

```python
import json, subprocess

mutation = {
    "query": """mutation {
        issueUpdate(id: "UUID", input: { stateId: "STATE_UUID" }) {
            issue { id identifier state { name } }
        }
    }"""
}

result = subprocess.run([
    "curl", "-s", "-X", "POST", "https://api.linear.app/graphql",
    "-H", f"Authorization: {api_key}",
    "-H", "Content-Type: application/json",
    "-d", json.dumps(mutation)
], capture_output=True, text=True)
```

### Option C: Python `requests` (preferred when available)

```python
import os, json, requests
api_key = os.environ['LINEAR_API_KEY']

resp = requests.post(
    "https://api.linear.app/graphql",
    headers={"Authorization": api_key, "Content-Type": "application/json"},
    json={"query": mutation}
)
```

**⚠️ execute_code sandbox limitation:** Neither `os.environ['LINEAR_API_KEY']` nor `subprocess` will work from inside `execute_code()` — the sandbox does NOT inherit shell environment variables. Always use `terminal()` when making Linear API calls.

## Degraded API Recovery — Mutation-Based State Discovery

When Linear's GraphQL API experiences a database migration issue (e.g., `QueryFailedError: column ss.worker_type does not exist`), ALL read queries may fail while mutations continue to work. This blocks the standard workflow of querying team states for the Done state ID.

**Workaround: Use a mutation's return payload to discover state IDs.** Mutations that return nested issue/team data bypass the broken query path:

```graphql
mutation {
  issueUpdate(id: "ISSUE_UUID", input: {}) {
    success
    issue {
      id
      team {
        id
        name
        states {
          nodes {
            id
            name
            type
          }
        }
      }
    }
  }
}
```

This mutation is a no-op (empty `input: {}`) but returns the full team state list — including the Done state UUID (`type: "completed"`). From there, use a second mutation to move the issue:

```graphql
mutation {
  issueUpdate(id: "ISSUE_UUID", input: { stateId: "<done-state-id>" }) {
    success
  }
}
```

**When to use:** Any time queries fail with `QueryFailedError` but mutations return successfully. This was discovered during GRO-949 (June 2026) when the Linear API had a transient `ss.worker_type` column error across all read paths.

**Pitfall:** The no-op `issueUpdate` still fires a database write even with empty input. Use it sparingly — once per session to cache the state IDs, not per-issue.

## Team State Discovery (for moving issues between states)

When you need to move an issue to a different state (e.g., Todo → Done), you MUST discover the target state's UUID. Two approaches:

### Approach A: Query the issue, then the team (recommended)

```graphql
# Step 1: Get the issue's team ID
query { issue(id: "ISSUE_UUID") { team { id name } state { id name } } }

# Step 2: Query that team's states
query { team(id: "TEAM_ID_FROM_STEP_1") {
  states { nodes { id name type position } }
} }
```

### Approach B: Query ALL workflow states, filter client-side

```graphql
query { workflowStates { nodes { id name type team { id name } } } }
```

Then filter in Python: `[s for s in states if s['team']['id'] == target_team_id]`

### ❌ Pitfall: Can't filter workflowStates by team name

```graphql
# THIS RETURNS ZERO RESULTS — team name filtering doesn't work on workflowStates
query { workflowStates(filter: {team: {name: {eq: "GRO"}}}) { nodes { id name } } }
```

The `workflowStates` query accepts a `filter` but the `team` field filter requires a **team UUID**, not a team name/key. Since you usually don't know the team UUID before querying, Approach A (issue → team.id → team.states) is more reliable.

**Recommendation:** Always use Approach A. Get the issue first (which gives you `team.id` for free), then query that team's states. This is 2 queries but never fails on team name mismatch.

## Common Mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Shell quoting GraphQL | 400 "Expected property name" | Use Python `json.dumps()` + subprocess or `requests.post()` |
| `Bearer` prefix | "Remove the Bearer prefix" | Just the key |
| `issueSearch` | "This endpoint deprecated" | Use `team(id:).issues(filter:)` |
| `identifier` filter | "Field 'identifier' is not defined" | Use `number: { eq: N }` |
| Hardcoded state IDs | "stateId contained an entry that could not be found" | Query team states first |
| Filtering workflowStates by team name | Returns zero results silently | Get team ID from issue first, then query `team(id:).states` |
| Label names (not UUIDs) in labelIds | Mutation succeeds but no labels applied | Label IDs are UUIDs — query `team { labels { nodes { id name } } }` |
| Assignee names (not UUIDs) | "assigneeId contained an entry..." | Query team members for UUIDs |
| `$LINEAR_API_KEY` in execute_code | KeyError or empty | Use `terminal()` — sandbox has no env vars |
| Not escaping `$` in `python3 -c` | Shell expands `$issueId` to empty string | Use `\$issueId` to escape from shell, or put mutation in single-quoted heredoc |
