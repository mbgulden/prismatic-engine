# Stale Archived Issue Handling — Nudge Executor

## Pattern
The trigger file `/tmp/trigger-fred-work` points to a Linear issue identifier that no longer exists in any queryable state (archived, deleted, or identifier below the oldest queryable range).

## Detection
1. Read trigger file → get issue identifier (e.g. GRO-25)
2. Query Linear: `team(id: "...") { issues(first: 250, includeArchived: true, orderBy: createdAt) { nodes { id identifier } } }` gets at most 250 issues. If the identifier is below the lowest returned (e.g. GRO-25 while oldest is GRO-511), the issue is unreachable.
3. Confirm by searching the project-registry.json and triage reports for the identifier — it may still be referenced in historical context.

## How to Handle
1. **Execute the work anyway** — the signal's title line describes a real task. The trigger file was created for a reason.
2. **Reference the issue** in deliverables: "Research for GRO-25 (archived, Nudge Executor Jun 2026)"
3. **Store artifacts** in the appropriate skill directory's `references/` or project repo's `docs/`
4. **Update the project-registry.json** — add a `_completed` entry documenting what was built and update `next_action` to point to the next step. The old registry entry (e.g. "Process 15 Backlog issues. Start with Smart Lock IoT Bridge MVP (GRO-151)") is what drove the signal — updating it prevents the same stale signal from being regenerated.
5. **Delete the trigger file** — prevent infinite retries on a non-existent issue
6. **Do NOT recreate** the Linear issue — the archive was intentional
7. **Report what happened** in your delivery output so Michael knows the stale signal was resolved

## Concrete Example — GRO-25 (Jun 2026)

**Trigger file contents:**
```
retries_done=0, max_retries=3
issue_id: GRO-25
title: "Research shipping labels and porch pickup automation for sold hardware"
```

**Situation:** GRO-25 was a very old issue (archived before the active range GRO-511–GRO-760). It had been sitting unprocessed for 8+ dispatcher cycles (noted as a stalled signal in the existing pitfall "Notification-only nudge replacing execution").

**Resolution:**
1. Confirmed issue was unreachable via Linear API (oldest queryable issue was GRO-511 after 250-issue fetch with `includeArchived: true`)
2. Found GRO-25 referenced in triage reports and context corpus — confirmed it was a real hardware-flip logistics task
3. Executed the research via parallel subagents (shipping APIs + porch pickup APIs)
4. Wrote findings to `hardware-flip-protocol/references/shipping-pickup-automation.md`
5. Deleted `/tmp/trigger-fred-work`

## Pitfalls
- ❌ **Retrying forever:** Without the "delete on not-found" rule, the nudge executor would retry 3 times, fail, and leave the trigger file for the next poll cycle — creating an infinite loop. Always delete the trigger after processing, even if the issue is gone.
- ❌ **Creating a new issue for the archived work:** The archive was intentional. If the work has high value, document it in a reference file and let Michael decide whether to create a new Linear issue.
- ❌ **Silently skipping the work:** "Issue not found, skipping" is not a valid response. Execute the described work — the title captures a real need.
- ❌ **Assuming all old issues are stale:** Some very old issues may still be valid but rare. Confirm by checking the project registry and triage reports for context before deciding to execute or skip.
