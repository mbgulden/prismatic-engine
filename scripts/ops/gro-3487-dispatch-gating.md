# GRO-3487 — Dispatch-ready gating and starvation signal

The dispatcher launch contract is explicit: an issue must carry both an agent label and `dispatch:ready` before a launch is allowed.

## Runtime behavior

- Candidate issues are still discovered per lane from `agent:<name>` / `agent::<name>` labels.
- Before credit-policy checks or launcher calls, candidates are filtered through the `dispatch:ready` gate.
- Issues missing `dispatch:ready` are skipped without being marked processed, so adding the gate label later makes them runnable on the next cycle.
- If a lane has no runnable work, the dispatcher prints a `STARVED agent:<name>` line with candidate and missing-gate counts.
- Cycle summaries include `starved lanes` and `missing-ready` counts so an empty lane is visible in cron/service logs instead of silently ignored.

This keeps Backlog/Todo routing labels from accidentally launching agents before the dispatcher has been given the explicit launch gate.
