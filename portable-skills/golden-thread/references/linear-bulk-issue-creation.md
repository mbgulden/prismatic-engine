# Linear Bulk Issue Creation Pattern

Pattern for creating 20-70+ Linear issues across multiple projects efficiently.

## When to Use
- Empty Linear project (0 issues) needs full scope planning
- User says "plan out every project all the way to the end"
- Any time you need 10+ issues across multiple projects

## The Pattern

### Step 1: Query Linear for Project IDs
```python
python3 - <<'PY'
import os, json, urllib.request
key = os.environ['LINEAR_API_KEY']
# GraphQL query for all projects with IDs
query = """query { projects { nodes { id name } } }"""
# ...
PY
```

### Step 2: Batch Issue Creation via Parallel Subagents

Critical: `execute_code` does NOT inherit `LINEAR_API_KEY` from the shell. Use `terminal` with `python3 - <<'PY'` heredocs instead.

Use `delegate_task` with **3 parallel subagents** (max concurrency), each handling 2-3 projects:
- Role: `leaf`
- Toolsets: `["terminal"]`
- Context: Include the full Linear GraphQL mutation pattern, team ID (`b6fb2651-5a1f-4714-9bcd-9eb6e759ffef`), and all issue titles/descriptions

### Step 3: Issue Creation Mutation

No stateId needed for backlog (default). Each issue:
```python
mutation {
  issueCreate(input: {
    title: "Build Interactive Bodygraph Engine",
    description: "Full scope: SVG rendering, responsive design, gate coloring, channel highlights...",
    teamId: "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef",
    projectId: "c7289b52-4da6-4a25-91ae-860bc6126817"
  }) {
    issue { id identifier title }
  }
}
```

### Step 4: Move Legacy Issues

If issues in a generic bucket need reassignment:
```python
mutation($id: String!, $projectId: String!) {
  issueUpdate(id: $id, input: {projectId: $projectId}) {
    issue { id identifier project { name } }
  }
}
```

### Step 5: Update Registry

After creation, update `project-registry.json` with `linear_issue_ids` arrays and fresh `next_action` values.

## Parallel Delegation Template

```
delegate_task with tasks=[{
  "goal": "Create Linear issues for [Project A], [Project B], [Project C]",
  "context": "LINEAR_API_KEY in shell env. Use python3 heredoc. Team: b6fb...",
  "toolsets": ["terminal"]
}, {...}, {...}]
```

## Pitfalls

- ❌ Using `execute_code` for Linear API — env vars not inherited
- ❌ Using `web_search` — tool may not exist; use `curl` via terminal instead for GitHub/Reddit research
- ❌ Setting `stateId` without querying workflow states first — just omit for default backlog
- ❌ Forgetting to update project-registry.json after creating issues
- ❌ The `&` character in issue descriptions can trigger false-positive backgrounding detection in `terminal()` — the system sees `&` and rejects the command as \"foreground command uses & backgrounding.\" Fix: use `python3 - <<'PYSCRIPT'` (different heredoc delimiter, no quotes inside) and avoid `&` in descriptions. Or use `write_file` to a temp script, then `terminal(\"python3 script.py\")`.
