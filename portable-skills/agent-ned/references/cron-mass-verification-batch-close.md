# Cron Mass Verification + Batch Close

When Ned's cron falls back to `agent:fred` with zero `agent:ned` issues, the most
common outcome is that MANY of those issues are already complete — prior sessions
shipped the work but never moved the Linear cards. The per-issue detection pattern
(`git log --grep="ISSUE_ID"` one at a time) works but is slow. The mass cross-reference
technique below handles 20+ issues in a single pass.

## Two-Pass Mass Verification

### Pass 1: Build the Issue→Commit Map
```bash
# Get all non-done issues (first: 200 to avoid pagination gaps)
python3 - <<'PYEOF'
import os, json, urllib.request
key = os.environ['LINEAR_API_KEY']
team_id = 'b6fb2651-5a1f-4714-9bcd-9eb6e759ffef'
# Query all issues, filter client-side for agent:fred
# Output: ISSUE_IDENTIFIER uuid=... state=...
PYEOF
```

Then cross-reference against git log:
```bash
cd /home/ubuntu/work/<repo>
git log --oneline --format="%h %ai %s" -80 | grep -E 'GRO-8[0-9]{2}|GRO-9[0-9]{2}'
```

Build a map: `{ 'GRO-863': 'b40df41 — Fix Lyria 2 test prompt', ... }`

### Pass 2: Batch Close Script
Write a Python script to `/tmp/ned_batch_close.py` that:
1. Iterates the close_map
2. For each issue: swap `agent:fred` → `agent:done`, move to Done state
3. Post a verification comment with commit SHA
4. 150ms sleep between mutations (rate-limit breathing room)

```python
import os, json, urllib.request, time

key = os.environ['LINEAR_API_KEY']
done_state_id = 'bbf71b3e-9a05-48ce-9418-df8b9c0b8fec'
done_id = 'a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b'

close_map = {
    'GRO-863': ('6b747ead-...', 'b40df41'),
    # ... more entries
}

for ident, (uuid, commit) in close_map.items():
    new_labels = [done_id]
    mutation = f'mutation {{ issueUpdate(id: "{uuid}", input: {{ labelIds: {json.dumps(new_labels)}, stateId: "{done_state_id}" }}) {{ success }} }}'
    payload = json.dumps({"query": mutation})
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=payload.encode(),
        headers={'Authorization': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
    success = result.get('data', {}).get('issueUpdate', {}).get('success', False)
    print(f"{'✅' if success else '❌'} {ident} ({commit[:7]})")
    time.sleep(0.15)
```

## Verification Patterns by Task Type

### Code tasks (commits exist)
Detection: `git log --oneline --grep="ISSUE_ID"` or `git log --oneline --format="%h %ai %s" -80 | grep ISSUE`
Verification: stat the deliverable files, grep for integration points (script tags, imports)

### Content tasks (file on disk, no commit needed)
Detection: `ls -la <target_file>` or `find <repo> -name "<key_file>"`
Verification: read the file, check it covers all required sections from the issue description
Example: GRO-1210 — `msp-partnership-template.md` already at 208 lines covering all 5 required sections

### Infrastructure-covers-child tasks
Detection: the parent issue (e.g., GRO-938) built a system (e.g., WAVE_CAMPAIGN) that handles the child's scope
Verification: check the parent's deliverable (file size, line count, key fields) covers the child's requirements
Example: GRO-937 (Levels 4-10 Wave Scripting) — 7,708-line WAVE_CAMPAIGN covers all 100 levels

## Batch Relabel (AGY mislabeled tasks)
When sprite-generation, image-generation, or visual-QA tasks are labeled `agent:fred`:
```python
relabel_map = {
    'GRO-877': '38732a54-...',
    'GRO-878': 'ef865027-...',
}
agy_id = '1b69d9c0-20a8-45b3-a594-771b8cba75a7'
for ident, uuid in relabel_map.items():
    mutation = f'mutation {{ issueUpdate(id: "{uuid}", input: {{ labelIds: ["{agy_id}"] }}) {{ success }} }}'
    # ... execute, 150ms sleep
```

## Pitfalls
- Always use `first: 200` — Linear's default `first: 20` silently drops issues
- Use UUIDs, not short identifiers (GRO-XXXX) in batch mutations
- Verify state ID before running — query `team(id: ...) { states { nodes { id name type } } }`
- After batch-close, scan for `agent:done` issues stuck in non-Done states and move them
