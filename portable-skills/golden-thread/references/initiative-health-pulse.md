# Initiative Health Pulse — Quick Dashboard Format

When running a golden thread review after reorganizing into Linear Initiatives, prefer this compact tabular format over per-thread narrative sections. It gives Michael a one-glance dashboard.

## Query Pattern

Split the query to avoid complexity limits (10,000 cap):

```graphql
# Structure only (no nested issues)
{ projects { nodes { id name initiatives { nodes { name } } } } }

# Then per-project issue counts (or flat issues query with project filter)
{ issues { nodes { id state { name } project { id name } } } }
```

Aggregate client-side by initiative.

## Output Format

```
| Initiative | Projects | Issues | Active | Status |
|---|---|---|---|---|
| Name | N | N | N (X%) | emoji |

TOTAL: N issues across M projects in K initiatives
```

**Status logic:**
- 🟢 = at least 1 active issue (In Progress or In Review)
- 🟡 = has issues but 0 active — stalled
- 🔴 = has active issues but all stale >48h
- ⚫ = 0 issues — empty project

**Below the table**, add one-line per initiative showing state distribution:
```
   Backlog:39 | In Progress:1 | In Review:1 | Todo:4
```

## Python Aggregation

```python
import json, os, urllib.request
api_key = os.environ['LINEAR_API_KEY']

req = urllib.request.Request('https://api.linear.app/graphql',
    data=json.dumps({'query': '{ projects { nodes { id name initiatives { nodes { name } } issues { nodes { state { name } } } } } }'}).encode(),
    headers={'Authorization': api_key, 'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=20) as r:
    data = json.loads(r.read())

by_init = {}
for p in data['data']['projects']['nodes']:
    inits = [i['name'] for i in p.get('initiatives',{}).get('nodes',[])]
    init = inits[0] if inits else 'No Initiative'
    issues = p['issues']['nodes']
    states = {}
    for i in issues:
        s = i['state']['name']
        states[s] = states.get(s, 0) + 1
    if init not in by_init:
        by_init[init] = {'projects': 0, 'issues': 0, 'states': {}}
    by_init[init]['projects'] += 1
    by_init[init]['issues'] += len(issues)
    for s, c in states.items():
        by_init[init]['states'][s] = by_init[init]['states'].get(s, 0) + c

for init in sorted(by_init.keys()):
    d = by_init[init]
    active = d['states'].get('In Progress', 0) + d['states'].get('In Review', 0)
    pct = active / d['issues'] * 100 if d['issues'] else 0
    flag = '🟢' if active > 0 else ('🟡' if d['issues'] > 0 else '⚫')
    state_str = ' | '.join(f'{s}:{c}' for s,c in sorted(d['states'].items()))
    print(f'{flag} {init}: {d["projects"]}p | {d["issues"]} issues | {active} active ({pct:.0f}%)')
    print(f'   {state_str}')
```

## When to Use

- After major Linear reorganization (initiative creation, project reassignment)
- During morning golden thread review when 7+ initiatives exist
- When Michael asks "how's everything looking?"

## Pitfalls

- If `issues { nodes { ... } }` inside `projects { nodes { ... } }` exceeds complexity (31+ projects), split: query projects without issues, then query flat `issues` and join client-side
- The `execute_code` sandbox doesn't have `LINEAR_API_KEY` — use `terminal()` with shell heredoc instead
- Initiatives field is `initiatives { nodes { name } }` (plural, nested) — not `initiative` (singular)
