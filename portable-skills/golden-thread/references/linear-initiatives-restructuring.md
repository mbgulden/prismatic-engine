# Linear Initiatives — Account Restructuring Pattern

## When to Use
When a Linear account has grown organically (30+ projects, flat structure, no Initiative grouping) and needs reorganization for strategic clarity and initiative-driven execution.

## The Pattern

### Phase 1: Full State Audit (API-Driven)
1. **Query teams + states**: Get all work states, team IDs — needed for any mutations
2. **Query projects (structure only)**: Get project IDs, names, states, progress, current initiative links. Do NOT nest issues — complexity cap.
3. **Query initiatives**: Get existing initiative IDs, names, statuses
4. **Query issues (flat)**: Get all issues with `state { name }` for aggregation, or per-project for counts

### Phase 2: Gap Analysis
Produce a table showing:

| Metric | Target |
|---|---|
| Projects with no Initiative | Should be 0% |
| Empty projects (0 issues) | Fill or archive |
| Near-empty projects (1-2 issues) | Expand or merge |
| Issues in Backlog | Should be <50% |
| Actively worked issues | Should be >10% of non-done |

### Phase 3: Initiative Design
Design initiatives around **revenue/operational swimlanes**, not around team structure. Each initiative gets:

- **Name**: Purpose-driven (e.g., "AI Consulting Revenue Engine")
- **KPI**: Single success metric
- **Projects**: All related work under one umbrella
- **Status**: Active/Planned/Paused

**Rule of thumb**: 4-8 initiatives for a solo operator. More than 8 = fragmentation.

### Phase 4: Execution (Bulk API Mutations)
Use the `projectUpdate` mutation to link projects to initiatives:

```graphql
mutation {
  projectUpdate(id: "PROJECT_ID", input: {
    initiativeId: "INITIATIVE_ID"
  }) { project { id name } }
}
```

For 30+ projects, batch via Python script (write to file, execute):

```python
import os, json, urllib.request
key = os.environ['LINEAR_API_KEY']

mapping = {
    "AI Implementation Consulting": "90c78190-363b-46ab-81dd-0b3e739f97a4",
    # ... project_name: initiative_id
}

for proj_name, init_id in mapping.items():
    # Get project ID, then update
    mutation = f'mutation {{ projectUpdate(id: "{proj_id}", input: {{ initiativeId: "{init_id}" }}) {{ project {{ id name }} }} }}'
    print(f"  {proj_name}: OK")
```

### Phase 5: Cleanup
- Archive empty projects or fill them with complete issue sets
- Merge duplicate initiatives (check for near-identical names)
- Archive stale Backlog issues (>90 days untouched)
- Reassign issues to correct projects where scope drifted

## Common Pitfalls

- **Query complexity cap**: 31 projects x nested issues = 13,990 complexity (cap is 10,000). Always split structure + issue queries.
- **Field name `initiative` vs `initiatives`**: Project type uses plural `initiatives { nodes }`, not singular `initiative`
- **Field name `state` vs `status`**: Initiative uses `status`, Project and Issue use `state`
- **One initiative per project is convention**: Linear technically supports multiple, but one-initiative-per-project keeps navigation clean
- **Don't rename initiatives mid-restructure**: Project links use IDs, not names — renaming is safe, but recreating with same name = new ID = orphaned links

## Example: GrowthWebDev Restructure (June 2026)

31 projects in 7 initiatives:

| Initiative | Projects | Issues | KPI |
|---|---|---|---|
| AI Consulting Revenue Engine | 3 | 50 | MRR from retainers |
| Active Oahu Digital Ecosystem | 5 | 92 | Booking conversion |
| HD Engine — DTC Revenue | 4 | 43 | API/Report MRR |
| HD Engine — B2B & Enterprise | 6 | 41 | B2B pipeline value |
| Hermes Swarm Infrastructure | 5 | 41 | Swarm uptime |
| Operations & Infrastructure | 4 | 60 | System health |
| Paused / Experimental | 3 | 9 | — |

Result: 87% of projects had no initiative before; 0% unassigned after. Navigation reduced from 31 flat items to 7 swimlanes.
