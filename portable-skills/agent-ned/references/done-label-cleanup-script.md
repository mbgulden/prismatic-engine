# Batch Done-Label Cleanup Script

When Ned completes a session, any Done issues still carrying `agent:ned` should be relabeled to `agent:done` so they don't clutter future queries. This applies after BOTH bulk reassignments and normal execution sessions — any `agent:ned` issue in a completed state is stale.

## Working Python Script

Run via `terminal` (not `execute_code` — the sandbox lacks `LINEAR_API_KEY`):

```python
import os, json, urllib.request

key = os.environ['LINEAR_API_KEY']
NED_LABEL = '6e0400c9-fc04-4868-86e3-f3156821f413'
DONE_LABEL = 'a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b'

def gql(query):
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps({'query': query}).encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# Query with high first: to beat pagination. Linear defaults to 20 per page —
# 41+ Done issues have accumulated in a single session before.
all_issues = []
for attempt in range(3):
    result = gql('{ issues(first: 100, filter: { labels: { name: { eq: "agent:ned" } }, state: { type: { eq: "completed" } } }) { nodes { id identifier } } }')
    batch = result['data']['issues']['nodes']
    all_issues.extend(batch)
    if len(batch) < 100:
        break

# De-duplicate by issue ID
seen = set()
unique = []
for i in all_issues:
    if i['id'] not in seen:
        seen.add(i['id'])
        unique.append(i)

print(f"Found {len(unique)} Done issues with agent:ned label")

swapped = 0
for issue in unique:
    ident = issue['identifier']

    # CRITICAL: Get current labels first. issueUpdate labelIds REPLACES the full
    # set — we must keep all other labels and only swap agent:ned to agent:done.
    # The old script set labelIds to ONLY [DONE_LABEL], which stripped every
    # other label (type:docs, requires:human-approval, etc.).
    r_labels = gql(f'{{ issue(id: "{ident}") {{ labels {{ nodes {{ id name }} }} }} }}')
    current_labels = r_labels['data']['issue']['labels']['nodes']
    new_ids = [DONE_LABEL if l['id'] == NED_LABEL else l['id'] for l in current_labels]

    # Ensure agent:done is present
    if DONE_LABEL not in new_ids:
        new_ids.append(DONE_LABEL)

    ids_json = json.dumps(new_ids)
    r = gql(f'mutation {{ issueUpdate(id: "{ident}", input: {{ labelIds: {ids_json} }}) {{ success }} }}')
    if r.get('data', {}).get('issueUpdate', {}).get('success'):
        swapped += 1
        print(f"  ✅ {ident}")
    else:
        print(f"  ❌ {ident}: {r}")

print(f"\nSwapped {swapped}/{len(unique)}")
```

## Key Details

- **Label IDs** (GrowthWebDev team):
  - `agent:ned`: `6e0400c9-fc04-4868-86e3-f3156821f413`
  - `agent:done`: `a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b`
  - `agent:fred`: `a43efb77-534a-4e39-8ff3-76f0e42019d1`

- **State filter**: `state: { type: { eq: "completed" } }` catches all Done issues regardless of the team's state name
- **`first: 100`** — use a high `first:` and iterate until `len(batch) < 100` to catch all issues even when 41+ accumulate (Linear's default page size is 20)
- **`labelIds` REPLACES the full set** — always GET current labels first, swap NED to DONE, keep everything else. The old script set `labelIds` to ONLY `[DONE_LABEL]`, silently stripping all other labels.
- **Run via `terminal`** — `execute_code` sandbox lacks `LINEAR_API_KEY`
- **Run after every Ned session** with 2+ days of accumulated Done items
- **Use short identifiers** (`GRO-XXXX`) in mutations, not UUIDs — self-documenting and catch copy-paste mistakes

## When to Run

- After any bulk label reassignment (Michael's routing directives)
- After a Ned session that completed 2+ tasks
- After 2+ days since last cleanup (Done issues accumulate from prior agent runs)
- Before querying `agent:ned` Todo/Backlog issues — clean Done first to reduce noise

## Pitfalls

- **Pagination bites**: `first: 20` (Linear's default) silently drops issues. Always use `first: 100` with a pagination loop. June 2026: 41 Done issues with `agent:ned` were found — a single `first: 20` query would have missed 21.
- **`labelIds` is a REPLACE operation**, not an add/remove. Setting it to `[DONE_LABEL]` strips every other label. Always fetch current labels first, swap the one you want, and send the full list back.
- **Some mutations fail with null** when the issue has many labels or a complex state. If an `issueUpdate` returns `data: None`, retry the individual mutation — the second attempt usually works.
