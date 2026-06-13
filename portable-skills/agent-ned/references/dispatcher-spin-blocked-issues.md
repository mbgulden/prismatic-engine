# Dispatcher Spin on Blocked `agent:ned` Issues

## Problem

When an `agent:ned` issue is labeled `requires:human-approval` and Ned verifies it but leaves it as `agent:ned`, the dispatcher re-routes it on every tick. This creates an infinite loop where Ned re-verifies the same pre-flight checks endlessly.

## Case Study: GRO-1021 (Jun 10–11, 2026)

**Issue:** "Send Idaho Personalized Outreach Emails — Hawley Troxell Partners"

**What happened:**
- 4 outreach emails drafted at `$PRISMATIC_HOME/work/research/ai-consulting/idaho-personalized-outreach-emails-2026-06-09.md`
- Pre-flight checks: LinkedIn updated ✅, Cal.com published ✅, SMB Report live ✅
- Ned verified everything 3 times, posted the same "Blocked — Needs Human Send" comment 3 times
- Dispatcher routed 5+ times: Jun 11 08:38, 09:49, 10:12, 11:01, 11:15
- Each time Ned re-verified the same files and posted the same conclusion

**Root cause:** The Ned skill said "If you hit a blocker: comment with the blocker, leave as agent:ned." This was correct for execution-time blockers (missing dependency, broken build) but wrong for human-action blockers (send email, make call, manual deploy).

**Fix applied:**
1. Ned's final verification confirmed all pre-flight was green
2. Swapped labels: `agent:ned` → `agent:fred` + kept `requires:human-approval`
3. Posted comment with clear ⛔ BLOCKED section + exact human action needed
4. Fred now handles the Michael handoff; dispatcher stops re-routing

**Label IDs used:**
- `agent:ned`: `6e0400c9-fc04-4868-86e3-f3156821f413`
- `agent:fred`: `a43efb77-534a-4e39-8ff3-76f0e42019d1`
- `requires:human-approval`: `9e976f5a-ccb0-4e6a-a071-a462cc4d0205`

## Decision Matrix

| Blocker Type | Action | Label After |
|---|---|---|
| Execution-time (missing dep, broken build, conflicting PR) | Comment + leave `agent:ned` | `agent:ned` |
| Human-action (send email, make call, manual deploy, review creative) | Verify + swap to `agent:fred` + keep `requires:human-approval` | `agent:fred` + `requires:human-approval` |
| Partial execution (some steps done, rest needs interactive) | Comment with done/remaining table + swap | `agent:fred` |
| Full completion (all autonomous work done) | Verify deliverables + move to Done | `agent:done` |

## Verification Checklist (before swapping to agent:fred)

Before declaring a human-action task "verified and ready":
1. ✅ All referenced files exist on disk
2. ✅ Live URLs return 200 (if applicable)
3. ✅ Pre-flight checklist in issue description is fully checked
4. ✅ Comment clearly states ⛔ BLOCKED with exact action Michael needs to take
5. ✅ `requires:human-approval` label is present on the issue
