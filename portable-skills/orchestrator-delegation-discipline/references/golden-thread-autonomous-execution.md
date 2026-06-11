# Golden Thread Autonomous Execution

When the user has a project registry (`project-registry.json`) with `next_action` fields, the agent should push forward autonomously rather than pausing after each completed task.

## Pattern

1. **Start**: Load the golden thread system — read `project-registry.json`, check Linear for stalled items, scan GitHub PRs.
2. **Execute**: Work through the registry. Complete a task → immediately identify the next one → execute it. Do not wait for the user to prompt you.
3. **Update**: After each task or batch of tasks, update the `next_action` fields in the registry to reflect what was completed and what's next.
4. **Push**: Commit and push to GitHub after non-trivial work.
5. **Deliver**: At the end, give the user a clear summary of what changed, what's running, and what they need to do (e.g., set environment variables, approve a PR).

## User Signals

The user may explicitly trigger this mode with phrases like:
- "Keep going on all the golden threads"
- "Be proactive"
- "Keep going"

When triggered: do not pause to ask "should I continue?" — identify the highest-impact next action across all projects and execute it immediately.

## Registry Pattern

The project registry at `/home/ubuntu/work/project-registry.json` tracks:
- `_last_updated`: timestamp — update after each work session
- `ventures.<id>.next_action`: the single next concrete step for each project
- `ventures.<id>.products.<pid>.next_action`: per-product next steps
- `standalone_projects.<id>.next_action`: standalone project next steps

**After completing work**: update the relevant `next_action` fields. Use checkmark emoji (✅) for completed items and clear next steps for remaining work. This keeps the registry as the single source of truth for autonomous execution.

## Priority Heuristic

When deciding what to work on next:
1. **Revenue-enabling** tasks first (payments, reports, user-facing features)
2. **Blocked** items second (things the user needs to provide — flag them)
3. **Growth** tasks third (SEO, marketing, distribution)
4. **Infrastructure** last (monitoring, cleanup, documentation)

## Pitfalls

- Don't wait for permission after completing a task — identify the next one and start.
- Don't skip the registry update step — stale `next_action` fields make future sessions less autonomous.
- Don't push half-finished work. Either complete it or leave a clear status in the registry.
