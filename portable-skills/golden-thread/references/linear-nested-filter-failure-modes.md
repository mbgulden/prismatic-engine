# Linear Nested Filter Failure Modes — Worked Example (Jun 9, 2026)

## Context
Querying the Darius Star project on the GrowthWebDev team. Goal: find all Todo/In Progress issues within a specific project.

## Attempts (in order)

### 1. Project query with nested state filter — **HTTP 200, found project but issues query 500**
```graphql
query($pid: String!) {
  project(id: $pid) {
    issues(first: 50, filter: { state: { name: { in: ["Todo","In Progress"] } } }) {
      nodes { id identifier title state { name } }
    }
  }
}
```
**Result:** Project found (200), but the issues sub-query returned HTTP 500 with `urllib.error.HTTPError: HTTP Error 500: Internal Server Error`.

### 2. Team query with state filter — **HTTP 500**
```graphql
query($tid: String!) {
  team(id: $tid) {
    issues(first: 50, filter: { state: { name: { in: ["Todo","In Progress"] } } }) {
      nodes { id identifier title state { name } project { id name } }
    }
  }
}
```
**Result:** HTTP 500.

### 3. Team query with first:200, NO state filter — **HTTP 500**
```graphql
query($tid: String!) {
  team(id: $tid) {
    issues(first: 200) {
      nodes { id identifier title state { name } project { id name } }
    }
  }
}
```
**Result:** HTTP 500. Even without a state filter, `first: 200` on the team query failed.

### 4. Viewer query (canary) — **HTTP 200 ✅**
```graphql
{ viewer { id name } }
```
**Result:** `{"data":{"viewer":{"id":"4a8a76b2-...","name":"Michael Gulden"}}}`. API key valid.

### 5. Team query with first:30 — **HTTP 200 ✅**
```graphql
{ team(id: "b6fb2651-...") { issues(first: 30) { nodes { id identifier title state { name } project { id name } } } } }
```
**Result:** 200. But returned only 30 issues, and none were Darius Star (they were deeper in the list).

### 6. Project query with first:100, NO state filter — **HTTP 200 ✅** (WINNER)
```graphql
{ project(id: "aa3f825d-...") { issues(first: 100) { nodes { id identifier title state { name } labels { nodes { name } } } } } }
```
**Result:** 200. Returned 95 total issues. Client-side filtered to 4 active.

## Root Cause Analysis
- **Query complexity matters**: `first: 200` on the team endpoint appears to hit a complexity cap that returns 500. `first: 30` works, `first: 200` doesn't.
- **The `state: { name: { in: [...] } }` filter is toxic**: Whether used on project or team queries, it has two failure modes: empty nodes (silent) or HTTP 500 (noisy). Both appear inconsistently.
- **The project-scoped query is the safest**: `project(id:...){ issues(first: N) }` with no filter + client-side filtering is the most reliable pattern across all sessions.

## Recommended Fallback Chain
1. Try `project(id:...){ issues(first: 100) }` → filter client-side
2. If 500, try `first: 30` on same project query
3. If still 500, try `team(id:...){ issues(first: 30) }` → filter client-side by project ID
4. If ALL fail, verify with `{ viewer { id } }` — if viewer returns 200, API is up and the issue is query structure
5. Use the Linear API directly to rule out Python-specific issues — send an authenticated POST to the GraphQL endpoint with the query.

## Client-Side Filter Pattern
```python
darius_id = 'aa3f825d-...'
active = [
    i for i in issues 
    if i.get('project') and i['project']['id'] == darius_id 
    and i['state']['name'] in ('Todo', 'In Progress')
]
active.sort(key=lambda x: int(x['identifier'].replace('GRO-', '')))
```
