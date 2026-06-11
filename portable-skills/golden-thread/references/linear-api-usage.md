# Linear GraphQL API — Usage Patterns

## Authentication
- API key in `Authorization` header WITHOUT "Bearer" prefix
- Key: stored in env `LINEAR_API_KEY`
- Endpoint: `https://api.linear.app/graphql`
- Team: GrowthWebDev (key: GRO, id: b6fb2651-5a1f-4714-9bcd-9eb6e759ffef)

## Common Queries

### List all projects
```graphql
{ projects { nodes { id name url state } } }
```

### List issues in a project
```graphql
{ project(id: "PROJECT_ID") { issues { nodes { id identifier title state { name } } } } }
```

### List all issues
```graphql
{ issues { nodes { id identifier title state { name } project { name } } } }
```

## Common Mutations

### Create project
```graphql
mutation {
  projectCreate(input: {
    name: "Project Name",
    description: "Description",
    teamIds: ["TEAM_ID"],
    state: "started"
  }) { project { id name url } }
}
```

### Create issue
```graphql
mutation($title: String!, $desc: String!, $teamId: String!, $projectId: String!) {
  issueCreate(input: {
    title: $title,
    description: $desc,
    teamId: $teamId,
    projectId: $projectId
  }) { issue { id identifier title } }
}
```

## Fetching All Issues Across Projects (Working Query)

To get all issues across all projects in a single request, use this **unfiltered** query:

```graphql
{ projects { nodes { id name issues { nodes { id identifier title state { name } updatedAt } } } } }
```

Filter issues client-side after fetching. Do NOT use inline `filter` on the nested `issues` field — see Gotcha below.

### To include only non-completed issues, filter after fetching:

```python
active = [i for i in issues if i['state']['name'] in ('backlog','in_progress','triage','todo')]
```

## Gotchas
- Bearer prefix causes `INPUT_ERROR` — use raw key
- **`stateId` transient failure**: `issueUpdate(id, input: {stateId: "..."})` may fail with `INPUT_ERROR: "Entity not found in validateAccess: stateId"` on the first attempt even when the stateId is correct (verified via `workflowStates` query). Retry the identical payload — it succeeds on the second call. This is a transient Linear API quirk, not an invalid state ID. Observed 2026-05-31 with state `bbf71b3e-9a05-48ce-9418-df8b9c0b8fec`.
- `teamId` is required for `issueCreate`
- Project `state` options: "backlog", "started", "paused", "completed", "canceled"
- Issue IDs format: GRO-88, GRO-89, etc.
- Project URLs: `https://linear.app/growthwebdev/project/<slug>-<shortid>`
- **Nested `filter` on issues returns empty**: Using `issues(filter: {state: {name: {in: [...]}}})` on the nested `projects.nodes[].issues` field returns empty `nodes` arrays even when issues exist. Workaround: fetch `issues { nodes { ... } }` without filters and filter client-side. This appears to be a Linear API behaviour with nested project-queries — the filter works on top-level `issues` queries but not on the nested relation.
- **`identifier` filter on `issues` / `issueSearch` returns HTTP 400**: Filtering top-level `issues` or `issueSearch` by `identifier` field — whether `{eq: "GRO-109"}`, `{in: ["GRO-109","GRO-110"]}`, or `{eq: "GRO-109"}` — returns HTTP 400 Bad Request. This is distinct from the `number` filter (which works) and the nested-project filter bug (which returns empty). **Workarounds**: (A) Query by project: `project(id: "...") { issues { nodes { ... } } }` and filter client-side. (B) Use the singular `issue(id: "internal-uuid")` query when you already have the internal UUID from a prior query or the registry. (C) For ad-hoc checks, use `number` filter on top-level `issues`: `issues(filter: {number: {eq: 109}}) { nodes { id identifier ... } }` — `number` accepts integer (bare 109, no "GRO-" prefix). **Root cause**: The Linear API's `identifier` field is a composite display field (`GRO-109`) and does not support standard filter operators.

- **"Duplicate" state requires `relationCreate` first**: Moving an issue to the `Duplicate` state (`stateId: 8a67aa62-ee98-4d67-a513-64217d8859c3`) fails with `INPUT_ERROR: "missing duplicate relation"` unless a duplicate relation already exists. You MUST create the relation FIRST via:

  ```graphql
  mutation { relationCreate(input: {
    issueId: "SIBLING_ISSUE_ID",
    relatedIssueId: "PARENT_ISSUE_ID",
    type: duplicate
  }) { success relation { id } } }
  ```

  **Pitfall:** `relationCreate` may return HTTP 400 even with inline IDs (same variable-binding quirk as `issueUpdate`/`issueArchive`). If it fails, fall back to the **Canceled** state (`a19484ec-9752-4c31-8110-f5043312e328`) which requires no prerequisites. Observed June 2026 with GRO-752/753/754/756.
  
  **Available relation types** via enum `IssueRelationType`: `blocks`, `duplicate`, `related`, `similar`.

- **`sort` parameter on `issues` query requires object-array format**: Inline `sort:createdAt` or `sort:[createdAt]` both fail with `GRAPHQL_VALIDATION_FAILED`. The `sort` field expects `[IssueSortInput!]` — an array of objects with field + direction. Correct format: `sort:[{createdAt: ASC}]` or `sort:[{createdAt: DESC}]`. Example working query: `issues(filter:{...}, sort:[{createdAt: ASC}], first:1)`. **Simpler alternative — `orderBy:createdAt`** also works: `issues(filter:{...}, orderBy:createdAt, first:1)`. Observed June 2026.

- **`issueSearch` is DEPRECATED (as of Jun 2026)**: Returns `{"errors": [{"message": "deprecated", "code": "INPUT_ERROR"}]}` regardless of query structure or filters. Do NOT use `issueSearch` for any purpose.

  **Replacement for finding issues by identifier: `team.issues(includeArchived: true)`** — Use `team(id: "b6fb2651-...") { issues(first: 200, includeArchived: true) { nodes { id identifier title state { name } } } }` and filter client-side by `identifier`. This finds issues outside the default top-100 results and includes archived ones.

  **Replacement for checking if an issue was deleted/trashed**: Use `issue(id: "known-uuid") { ... trashed }` if you have the UUID, or the `team.issues(includeArchived: true)` approach above and check the `trashed` field on returned nodes — `true` means in trash, `false/null` means archived. Unlike `issueSearch`, `team.issues()` with `includeArchived: true` reliably includes deleted and trashed issues.

  **Two-step pattern to find any issue by identifier:**
  1. `team(id: "...") { issues(first: 200, includeArchived: true) { nodes { id identifier title state { name } } } }` → filter client-side on `identifier` to find UUID
  2. Once you have the UUID, use `issue(id: "2de960d1-...") { ... }` for detailed queries and mutations

## Initiatives\n\n### Query Initiatives\n```graphql\n{ initiatives { nodes { id name description color status } } }\n```\n\n### Initiative Field Name Gotchas\n| Wrong | Right | Notes |\n|---|---|---|\n| `initiative` (singular on Project) | `initiatives { nodes { ... } }` (plural, nested) | Projects use the plural `initiatives` field |\n| `state` on Initiative | `status` on Initiative | Initiative uses `status`, not `state` — different from Project/Issue |\n| `initiativeId` on `ProjectUpdateInput` | Use `initiativeToProjectCreate` mutation | `initiativeId`/`initiativeIds` are NOT valid fields on ProjectUpdateInput — this is a common mistaken assumption |\n\n### Create Initiative\n```graphql\nmutation {\n  initiativeCreate(input: {\n    name: \"Initiative Name\",\n    description: \"Description\",\n    color: \"#6E6E6E\"\n  }) { initiative { id name } }\n}\n```\n\n### Update Initiative (rename, change description)\n```graphql\nmutation {\n  initiativeUpdate(id: \"INITIATIVE_ID\", input: {\n    name: \"New Name\",\n    description: \"Updated description\"\n  }) { initiative { id name } }\n}\n```\n\n### Delete Initiative\n```graphql\nmutation { initiativeDelete(id: \"INITIATIVE_ID\") { success } }\n```\n\n### Link Project to Initiative (CORRECT mutation)\n\n**DO NOT use `projectUpdate` with `initiativeId` — that field does not exist on `ProjectUpdateInput`.**\n\nThe correct mutation is `initiativeToProjectCreate`:\n\n```graphql\nmutation {\n  initiativeToProjectCreate(input: {\n    initiativeId: \"INITIATIVE_ID\",\n    projectId: \"PROJECT_ID\"\n  }) { success }\n}\n```\n\n### Unlink Project from Initiative\n\nRequires the **link object ID**, NOT the initiative ID or project ID. The link ID comes from querying the project's `initiativeToProjects` field:\n\n```graphql\n# Step 1: Get the link ID\n{ project(id: \"PROJECT_ID\") {\n    name\n    initiativeToProjects { nodes { id initiative { id name } } }\n} }\n\n# Step 2: Delete using the link ID\nmutation { initiativeToProjectDelete(id: \"LINK_ID\") { success } }\n```\n\n### Linking Already-Linked Projects (\"Project Nesting Conflict\")\n\nWhen a project is already linked to an initiative and you try to link it to a different one, the API returns: `\"project nesting conflict\"`. **Resolution:** query the project's `initiativeToProjects` to find the existing link ID, call `initiativeToProjectDelete` to remove it, then call `initiativeToProjectCreate` with the new initiative ID.\n\n### Query All Projects with Their Initiatives\n```graphql\n{ projects { nodes { id name url state progress initiatives { nodes { id name } } } } }\n```\n\n### Full Initiative Reconciliation Pattern (batch reassignment)\n\nWhen reorganizing initiatives across many projects:\n1. Query all projects with `initiatives { nodes { id name } }` — client-side map\n2. For projects needing new initiative: `initiativeToProjectCreate`\n3. For projects moving to different initiative: first query `initiativeToProjects` for link IDs, then `initiativeToProjectDelete`, then `initiativeToProjectCreate`\n4. Verify final state by re-querying projects with initiatives\n\n## Query Complexity Limits (CRITICAL)\n\nLinear enforces a **complexity cap of 10,000**. Nesting `issues { nodes { ... } }` inside `projects { nodes { ... } }` with 30+ projects easily hits this (observed: 13,990 with 31 projects).\n\n**Split pattern — never query all projects + all issues in one request:**\n\n```bash\n# Query 1: Structure only (projects + initiatives + states)\ncurl ... -d '{\"query\": \"{ projects { nodes { id name url state progress initiatives { nodes { id name } } } } }\"}'\n\n# Query 2: Issue counts per project (one project at a time if needed)\ncurl ... -d '{\"query\": \"{ project(id: \\\"ID\\\") { issues { nodes { id identifier title state { name } } } } }\"}'\n\n# Query 3: All issues flat (for filtering)\ncurl ... -d '{\"query\": \"{ issues { nodes { id identifier title state { name type } project { id name } } } }\"}'\n```\n\n**Symptoms of hitting the cap:** HTTP 400 with `\"The query is too complex. Complexity: N. Maximum allowed complexity: 10000.\"`\n\n**Fix:** Split the query. Pull structure first, then batch project-level issue queries. Client-side aggregation.\n\n## Bulk Operations: Posting Comments to Multiple Issues

When posting similar comments to many issues (e.g., interview scripts, status updates), avoid bash heredocs (`python3 <<'PYEOF'`) — nested quoting and escaping causes hard-to-debug `SyntaxError` failures. Instead, write a `.py` file and execute it:

```bash
# Write the script to a temp file
write_file path=/tmp/post_comments.py content="...python script..."

# Execute it
python3 /tmp/post_comments.py
```

The script pattern:
```python
import os, json, urllib.request
key = os.environ['LINEAR_API_KEY']

# Map issue numbers to comment text
comments = {
    142: "Comment text for GRO-142...",
    143: "Comment text for GRO-143...",
}

for num, comment_text in comments.items():
    # Get issue ID by number
    query = f'query {{ issues(filter: {{number: {{eq: {num}}}}}) {{ nodes {{ id }} }} }}'
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps({'query': query}).encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    
    iss_id = data['data']['issues']['nodes'][0]['id']
    
    mutation = {'query': 'mutation { commentCreate(input: {issueId: "' + iss_id + '", body: ' + json.dumps(comment_text) + '}) { success } }'}
    req2 = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps(mutation).encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req2, timeout=30) as r2:
        result = json.loads(r2.read())
    print(f"  GRO-{num}: {'OK' if result['data']['commentCreate']['success'] else 'FAIL'}")
