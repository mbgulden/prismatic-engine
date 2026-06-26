# GRO-653 — Daily Transit Briefing V2 Optimization

**Issue**: Optimize daily transit briefings with Becca's feedback
**Owner**: Ned (agent:ned label)
**Date**: 2026-06-26

## What was done

The skill `daily-transit-briefing` was audited against the "better than Co-Star" standard.
V1 (current) was strong on data but weak on format. The optimization adds a V2 format
that mirrors Co-Star's screenshot-first structure while keeping the HD specificity
that beats Co-Star on data.

## Files changed

1. **Skill** — `~/.hermes/profiles/ned/skills/human-design/daily-transit-briefing/SKILL.md`
   - Added `## Optimization for screenshot-readiness` section with V2 format spec for both Sage→Becca and Fred→Michael
   - Added `## Becca-feedback checklist` (v2 → v3 candidates)
   - Added `## Cron v2 — when to switch from V1 to V2` (default: V2)
   - Added reference to today's worked example

2. **Reference** — `~/.hermes/profiles/ned/skills/human-design/daily-transit-briefing/references/ab-test-2026-06-26.md`
   - Today's real V1 vs V2 side-by-side using live engine data
   - Michael's 1-8 Inspiration channel is fully lit (Moon G1 + Mars G8) — rare
   - Becca's G26 Mean Lilith side-hit on 26-44 — shared convergence day with Michael
   - Benjamin's 7-31 + 20-34 both lit — rare MG two-channel day
   - William, Victoria: heavy Throat noise

3. **Validator** — `~/.hermes/profiles/ned/skills/human-design/daily-transit-briefing/scripts/validate_v2.py`
   - 9-check validator for the V2 format
   - Returns 0 (pass) or 1 (fail with reasons)
   - Tested: V2 optimized example passes, V1 current fails on 5 of 9 checks

4. **Cron prompts** — `~/.hermes/profiles/orchestrator/cron/jobs.json`
   - Sage Daily Transit Briefing (b1f498e17351): preamble updated to call V2 format
   - Fred Daily Transit Briefing (6f293e3a8044): preamble updated to call V2 format
   - Default: V2. Fall back to V1 only on user preference or engine error.

## Engine pipeline verified

`python3 ~/.hermes/profiles/ned/skills/human-design/daily-transit-briefing/scripts/family-overlay.py`
exited 0 on 2026-06-26 with full Type A/B/C classification for all 5 family members.

## What's blocking the crons (still)

The crons themselves remain paused per the issue ("Crons are paused pending her input").
The skill is now ready to generate V2 briefings as soon as Becca unpauses them. When she
does, the cron will produce V2 format directly. The validator will catch any drift.

## What Ned cannot do (escalation needed)

- Resume the crons. The issue text says they are paused pending Becca's feedback. Ned
  does not have authority to resume them — that is Michael's call (and Becca's review).
- Decide the V3 format. Becca's review of V2 is required. The skill now ships a
  checklist she can answer to drive V3.

## Recommendation for next step

1. Resume crons (Michael) — they'll produce V2 starting today.
2. Send Becca 2-3 days of V2 briefings.
3. Ask her the 9-question checklist (in the skill) to drive V3.
