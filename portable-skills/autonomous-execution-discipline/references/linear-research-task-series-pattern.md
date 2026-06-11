# Linear Research Task Series Creation Pattern

When the user requests a comprehensive research project (interview an agent, audit a system, document a workflow), create a parent task with subtasks in Linear.

## Pattern

1. **Parent task**: Descriptive title covering the full scope. Label with `agent:fred`, `type:research`, `type:docs`.
2. **Subtasks**: One per deliverable. Each self-contained enough for independent execution.
3. **Blocked tasks**: If a subtask depends on pre-work (like re-auth), note it in the description with "BLOCKED on X".

## GraphQL API

```python
import json, os, urllib.request

key = os.environ['LINEAR_API_KEY']
headers = {'Authorization': key, 'Content-Type': 'application/json'}
team_id = 'b6fb2651-5a1f-4714-9bcd-9eb6e759ffef'
todo_state = '3d29ebe3-00cf-428b-b52a-bfecb5ae4410'

def graphql(query):
    data = json.dumps({'query': query}).encode()
    req = urllib.request.Request('https://api.linear.app/graphql', data, headers)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())

# Create issue
mutation = f'''mutation {{
  issueCreate(input: {{
    title: "Task Title Here",
    description: "Description here",
    teamId: "{team_id}",
    stateId: "{todo_state}",
    labelIds: ["label-id-1", "label-id-2"]
  }}) {{
    issue {{ identifier title url }}
  }}
}}'''
result = graphql(mutation)
```

## Example: GRO-802–809 (AGY + Jules Interview Series)

Parent: GRO-802 "Comprehensive AGY Interview & Documentation"
Subtasks:
- GRO-803: AGY CLI Technical Deep-Dive
- GRO-804: AGY Best Practices — Visual Matching
- GRO-805: AGY Best Practices — General Delegation
- GRO-806: AGY Nudging & Communication Analysis
- GRO-807: AGY Linear Integration Guide
- GRO-808: Jules CLI Re-Auth & Interview (BLOCKED on re-auth)
- GRO-809: AGY-Jules Task Routing Guide

## Pitfalls

- Use `terminal()` not `execute_code()` — the sandbox doesn't inherit `$LINEAR_API_KEY`
- Escape double quotes in titles/descriptions: `title.replace('"', "'")`
- Descriptions must be under ~2000 chars for the API
- The parent issue ID must be queried separately before creating subtasks with `parentId`
- If the parent isn't immediately queryable (race condition), create subtasks without parentId and link manually
