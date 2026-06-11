# Nudge Executor — Sibling Issue Cleanup Pattern

## Why This Exists

When the Unified Agent Dispatcher routes an issue to Agent X and Agent X never responds, the dispatcher may create **new issues** with different `agent:*` labels instead of re-routing the original. This produces sibling issues — multiple Linear cards tracking the same scope of work.

The nudge executor (cron `c2cce4fec4ed`, every 5 min) is the fallback that ultimately processes the original issue. When it completes, the siblings are left behind in Todo/Backlog as stale duplicates.

## The Rule

**When the nudge executor completes an issue, it must check for and clean up sibling issues before deleting the trigger file.**

## Concrete Example: GRO-750 Sibling Sprawl (June 6, 2026)

### Timeline

| Time | Event |
|------|-------|
| 16:28 | Dispatcher routes GRO-750 ("Nav Re-Vamp: Desktop & Mobile Full Redesign") to Kai — **no pickup** |
| 17:11 | Dispatcher routes GRO-750 to Kai again — **no pickup** |
| 17:27 | Dispatcher routes GRO-750 to Kai again — **no pickup** |
| 17:44 | Dispatcher routes GRO-750 to Kai again — **no pickup** |
| — | Dispatcher creates **GRO-752**: "AGY: Fix nav bugs — mobile menu, dropdown indicators, font case, active state, Contact link" (`agent:fred`) |
| — | Dispatcher creates **GRO-753**: "AGY: NAV BUGS — MUST TEST PREVIEW LINK AND ITERATE UNTIL DONE" (`agent:fred`) |
| — | Dispatcher creates **GRO-754**: "AGY: FIX NAV COMPLETELY — mobile menu, font, dropdowns, active states — ITERATE UNTIL DONE" (`agent:fred`) |
| 17:45 | AGY plan saved to `plans/gro-750-nav-revamp-plan.md` |
| — | GRO-756 created: "AGY: Find a proven, accessible, mobile-responsive nav template" (`agent:fred`) |
| 17:46 | AGY comment posted on GRO-750: "AGY Phase Complete" |
| — | **FAIL: Nudge executor implements GRO-750 nav hierarchy but forgets sibling cleanup** — GRO-752/753/754/756 left in Todo |
| 18:07 | Nudge executor picks up GRO-750 again (trigger file was NOT deleted on first run). Escalated to Fred. |
| 18:08 | **FIX: Nudge executor runs pipeline handoff** — comments on GRO-750 with completed step, updates label to `agent:jules`, posts "Superseded by GRO-750" on each sibling, moves GRO-752/753/754/756 → Canceled, deletes trigger file. |

### Aftermath (Corrected)

GRO-750 is at Step 3/5 (`agent:jules` — code review). Siblings are in Canceled with explanatory comments. The trigger file is clean — dispatcher won't retry.

### What Should Have Happened

After posting the completion comment on GRO-750 and before deleting `trigger-fred-work`:

1. Query Linear for issues in the same project with overlapping keywords ("nav bug", "nav fix", "nav complete", "mobile menu")
2. For each match, check if its scope is a subset of GRO-750's scope
3. If yes: post a comment ("Superseded by GRO-XXX which was completed. Scope achieved. Closing.") and move to **Canceled** state
4. If the sibling has the same `agent:*` label: move directly to Canceled
5. If the sibling has a different `agent:*` label: post the comment and leave the label — the next agent or cleanup pass will handle it

### State Target: Canceled (not Duplicate)

The GrowthWebDev team does NOT have a "Won't Do" state. Two relevant states exist:
- **Canceled** (stateId: `a19484ec-9752-4c31-8110-f5043312e328`) — reliable, no prerequisites, use this for sibling cleanup
- **Duplicate** (stateId: `8a67aa62-ee98-4d67-a513-64217d8859c3`) — requires a duplicate relation to be created FIRST via `relationCreate` mutation. The relation itself may fail with HTTP 400 (same GraphQL variable-binding quirk as issue mutations — inlining the ID doesn't always fix it). **Do not attempt Duplicate unless you've verified relationCreate works.** Fall back to Canceled.

### Detection Heuristic

Check for sibling issues when:

- The original issue was dispatched 2+ times before landing with the nudge executor
- The trigger file title contains words the dispatcher tends to abbreviate or rephrase
- The project has multiple Todo/Backlog issues with overlapping keywords

**Implementation** — query at the team level:
```graphql
query($team: String!) {
  team(id: $team) {
    issues(first: 50, filter: {state: {name: {in: ["Todo", "Backlog"]}}}) {
      nodes { id identifier title state { name } }
    }
  }
}
```
Then filter client-side by keyword overlap with the original issue's title/description.

### Pitfall — Don't Close Active Parallel Work

Not every issue in the same project is a sibling. GRO-755 ("Implement SignalProvider") and GRO-757 ("Add nudge poll loop to Kai") were created around the same time but are **unrelated** to the nav work. Only close issues whose scope is a clear subset of the completed original.
