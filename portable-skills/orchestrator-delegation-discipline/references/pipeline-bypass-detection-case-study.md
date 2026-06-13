# Pipeline Bypass Detection — Case Study

Concrete example from June 12, 2026 session (Darius Star).

## The Bypass

Ned committed 5 fixes to master and the dispatcher moved all 5 Linear issues directly to Done with `agent:done`:

| Issue | Title | Reality |
|-------|-------|---------|
| GRO-1468 | Slice sprite sheets | Utility exists but sprites never loaded |
| GRO-1469 | Death freeze fix | 30s fallback wired — may work |
| GRO-1470 | Wire MP3 audio | AudioManager wired to canvas click |
| GRO-1471 | Fix parallax backgrounds | Backgrounds rendering |
| GRO-1472 | Redesign mobile controls | **BROKEN** — buttons off-canvas |

## How Fred Detected It

1. **Golden thread scan** of project `issues(first: 80)` — saw 5 Done issues with only `agent:done` + `agent:ned` labels, no `agent:agy`.
2. **Git log check** — `git log --oneline --all -30` showed Ned commits for all 5, no AGY review commits between implementation and Done.
3. **Review artifact search** — `find docs/ -name "*audit*" -o -name "*review*"` returned zero matching files from the relevant time window.
4. **Live verification** — browser test confirmed mobile buttons broken, confirming at least GRO-1472 was a false Done.

## What Fred Did

1. Reopened GRO-1472: moved from Done → In Progress, removed `agent:done` + `agent:agy`, added `agent:fred`
2. Created GRO-1473 (fix mobile buttons) + GRO-1474 (wire sprites) + GRO-1475 (AGY audit)
3. Launched AGY on GRO-1475 with 600s timeout, PTY background mode
4. Created 4 Prismatic Pipeline implementation issues (GRO-1476–1479) to prevent future bypasses
5. Moved GRO-1272 (Lyria music) to Done — tracks existed on disk, issue was just stale

## Root Cause

The dispatcher's label chain (`agent:ned` → `agent:fred` → `agent:done`) is enforced for NEW issues, but Ned's cron picks up `agent:fred` issues and can set `agent:done` directly if no verification step blocks it. The dispatcher doesn't currently enforce that a peer review label (`agent:agy`/`agent:jules`) must be present before `agent:done`.

## Fix (In Progress)

GRO-1479: Implement credit policy engine in dispatcher — will also enforce pipeline stage gating.
