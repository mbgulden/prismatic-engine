# Linear Task Creation — Reliable Pattern

## Problem

Inline `python3 -c "..."` heredocs fail unpredictably when task descriptions contain:
- Em-dashes (—) and other Unicode punctuation
- Apostrophes and smart quotes (`'` vs `'`)
- Multiple levels of escaping needed for JSON inside Python inside shell

The inline pattern in SKILL.md works for simple titles but breaks on rich descriptions with markdown formatting, special characters, or multi-paragraph content.

## Solution: Write .py File First, Then Execute

```bash
# 1. Write the Python script to a temp file
write_file /tmp/create_tasks.py

# 2. Run it
python3 /tmp/create_tasks.py
```

### Template Script

```python
import os, json, urllib.request

key = os.environ['LINEAR_API_KEY']
team_id = 'b6fb2651-5a1f-4714-9bcd-9eb6e759ffef'
proj_id = '<PROJECT_ID>'

def gql(query, variables=None):
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

tasks = [
    {
        'title': 'Task title here',
        'desc': """Multi-line description with "quotes" and em-dashes — no escaping needed
because we're in a Python triple-quoted string, not a shell heredoc."""
    },
    # ... more tasks
]

for task in tasks:
    result = gql("""
    mutation($t: String!, $d: String!, $team: String!, $proj: String!) {
      issueCreate(input: {title: $t, description: $d, teamId: $team, projectId: $proj}) {
        issue { id identifier title }
      }
    }
    """, {'t': task['title'], 'd': task['desc'], 'team': team_id, 'proj': proj_id})
    issue = result['data']['issueCreate']['issue']
    print(f"  {issue['identifier']}: {issue['title']}")
```

## Benefits

- No shell escaping — triple-quoted Python strings handle any character
- Reusable template — copy the gql() function, change project_id and tasks array
- Easier to review before running — file can be checked before execution
- Works with `write_file` tool — the Hermes-native way to create files

## When NOT to Use

- Single simple issue with no markdown/special chars → inline is fine
- Issues being created by a subagent via delegate_task → subagent handles its own approach
