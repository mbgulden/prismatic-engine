# Agent:done Batch Cleanup Script

When Ned finishes a session and `agent:done` issues have accumulated in non-Done states (Backlog, Todo, In Progress), run this batch cleanup. Issues labeled `agent:done` should be in the Done state — they represent completed work where someone forgot to move the card.

## Detection

```python
result = gql('{ issues(filter: { labels: { name: { eq: "agent:done" } }, state: { type: { nin: ["completed"] } } }, first: 50) { nodes { id identifier state { name } } } }')
```

These issues are invisible to project-scoped queries (they're already "done" from a work perspective). Only a team-level or label-filtered query will find them.

## Batch Move Script

```python
import os, json, urllib.request

key = os.environ['LINEAR_API_KEY']
done_state_id = 'bbf71b3e-9a05-48ce-9418-df8b9c0b8fec'

def gql(query):
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps({'query': query}).encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# Get all agent:done issues in non-completed states
result = gql('{ issues(filter: { labels: { name: { eq: "agent:done" } }, state: { type: { nin: ["completed"] } } }, first: 50) { nodes { id identifier state { name } } } }')
issues = result['data']['issues']['nodes']
print(f"Found {len(issues)} agent:done issues to move to Done")

moved = 0
for issue in issues:
    iid = issue['id']
    ident = issue['identifier']
    try:
        r = gql(f'mutation {{ issueUpdate(id: "{iid}", input: {{ stateId: "{done_state_id}" }}) {{ success }} }}')
        if r.get('data', {}).get('issueUpdate', {}).get('success'):
            moved += 1
            print(f"  ✅ {ident} → Done")
        else:
            # Retry once — stateId mutations can fail transiently
            r2 = gql(f'mutation {{ issueUpdate(id: "{iid}", input: {{ stateId: "{done_state_id}" }}) {{ success }} }}')
            if r2.get('data', {}).get('issueUpdate', {}).get('success'):
                moved += 1
                print(f"  ✅ {ident} → Done (retry)")
            else:
                print(f"  ❌ {ident}: {r.get('errors', 'unknown')}")
    except Exception as e:
        print(f"  ❌ {ident}: {e}")

print(f"\nMoved {moved}/{len(issues)} issues to Done")
```

## When to Run

- After every Ned session that processes 2+ issues — the accumulation is gradual
- After any bulk label reassignment session (e.g., agent:fred → agent:ned)
- When `golden-thread` review finds `agent:done` issues in non-Done states
- June 2026 example: 50 issues found in Backlog — all moved to Done in one pass

## Pitfalls

- Do NOT run this during active Fred review cycles — Fred may be mid-review on some of these
- `stateId` mutations can fail transiently (INPUT_ERROR: "Entity not found in validateAccess: stateId") — the retry pattern handles this
- Always check the count BEFORE running — if 50+ issues accumulate, they've likely been building for weeks and are safe to move
- This cleanup is separate from the "agent:done stuck in wrong state" check in golden-thread Step 3.6 — that covers individual issues; this handles bulk accumulation
