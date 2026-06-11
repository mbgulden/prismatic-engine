# Linear Batch Agent Assignment Pattern

## When to use
You have a project with 30+ issues all in Backlog, zero agent labels, zero priorities. You need to batch-assign agents and priorities in one pass.

## Pattern

```python
import os, json, urllib.request, time

key = os.environ['LINEAR_API_KEY']

# Label IDs (query once, reuse)
LABELS = {
    'agent:fred': 'a43efb77-534a-4e39-8ff3-76f0e42019d1',
    'agent:jules': '5bc301fb-e4dc-404c-97fb-290c49ed2528',
    'agent:agy': '1b69d9c0-20a8-45b3-a594-771b8cba75a7',
    'agent:kai': 'c4d929be-8d15-4482-b6d7-a5ed85aa2e73',
}

def gql(query, variables=None):
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# First: get all issues in the project with their UUIDs and current labels
proj_id = 'aa3f825d-74c8-4366-9155-799100abccdd'
result = gql("""
  query($proj: String!) {
    project(id: $proj) {
      issues(first: 50) {
        nodes { id identifier title labels { nodes { name } } }
      }
    }
  }
""", {"proj": proj_id})

issues = {i['identifier']: i for i in result['data']['project']['issues']['nodes']}

# Assignment map: identifier → {labels: [...], priority: 1-4}
assignments = {
    'GRO-1029': {'labels': ['agent:jules'], 'priority': 2},
    'GRO-1030': {'labels': ['agent:fred'], 'priority': 2},
    # ... etc
}

# Apply: merge new labels with existing labels, then update
for identifier, config in assignments.items():
    issue = issues[identifier]
    current_labels = [l['name'] for l in issue['labels']['nodes']]
    new_labels = [l for l in current_labels if l.startswith('agent:')]  # keep existing agent labels
    for lbl in config['labels']:
        if lbl not in new_labels:
            new_labels.append(lbl)
    label_ids = [LABELS[l] for l in new_labels if l in LABELS]

    # Inline the ID — variables break issueUpdate
    mutation = f'''
    mutation {{
      issueUpdate(id: "{issue['id']}", input: {{ labelIds: {json.dumps(label_ids)}, priority: {config['priority']} }}) {{
        success
      }}
    }}
    '''
    result = gql(mutation)
    time.sleep(0.15)  # Rate limit breathing room
```

## Pitfalls
- **issueUpdate with GraphQL variables returns 500** — inline the issue ID directly in the mutation string
- **labelIds is a SET operation** — pass the FULL array, not add/remove. Query current labels first, merge, then set.
- **Rate limit**: add 150ms sleep between mutations
- **Write the script to a file first**, then execute via `terminal()` — the `execute_code` sandbox doesn't inherit `LINEAR_API_KEY`

## Proven
Used June 10, 2026 to update 37 Darius Star issues (GRO-1029 through GRO-1068) with agent labels and priorities in one batch. All succeeded.
