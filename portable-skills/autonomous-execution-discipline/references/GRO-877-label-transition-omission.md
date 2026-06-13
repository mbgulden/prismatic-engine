# GRO-877 — Agent Output Without Label Transition (Dispatcher Spam Variant)

**Date:** June 13, 2026  
**Issue:** GRO-877 — "Generate boss state frames for Cyber Coelacanth"  
**Signal ID:** `1b7b9b4a-c863-4b9e-9955-3d4b3063fde7`

## Summary

The dispatcher comment spam pitfall covers the case where an issue has dispatcher routing comments with **zero agent output**. GRO-877 reveals a distinct variant: **agent output EXISTS** (multiple sessions posted full implementation plans, summary responses, and walkthroughs with absolute file paths), but the **agent label was never transitioned** from `agent:agy` to `agent:done`, and the issue state was never moved from Backlog to Done. The dispatcher reads the stale label and keeps routing.

## The Pattern

1. Issue is created with `agent:agy` label (auto-generation task)
2. AGY fails 3 times — dispatcher escalates to Fred
3. Fred sessions complete the work: Imagen 3 generation → BFS background removal → manifest registration → sprites.js/enemies.js integration → verification scripts
4. Each Fred session posts **Implementation Plan**, **Summary Response**, and **Walkthrough** comments — all with absolute file paths and verification output
5. BUT: the Fred session that completes the work crashes or times out before calling `issueUpdate` to transition the label from `agent:agy` → `agent:done` and move state to Done
6. The trigger file survives (never deleted) because the session crashed after posting comments
7. Next dispatcher poll: reads `agent:agy` label → routes to Fred again → another session repeats the same work → same crash → infinite loop
8. Result: 20+ dispatcher routing comments interspersed with 5+ complete walkthrough cycles, all verifying the same assets

## Detection (different from base "zero output" case)

When you see dispatcher spam:
1. Don't stop at "no agent output" — also scan for **walkthrough/summary comments** in the comment history
2. If ANY walkthrough describes complete artifacts → run Step 0.5 pre-verification
3. If Step 0.5 confirms assets on disk → the failure mode is **label-transition omission**, not execution failure
4. Key differentiator: base case = dispatcher comments with nothing between them. GRO-877 variant = dispatcher comments with full completion cycles between them.

## Verified Artifacts (Jun 13, 2026)

```
/home/ubuntu/work/darius-star/assets/sprites/boss_idle.png   — 681KB
/home/ubuntu/work/darius-star/assets/sprites/boss_rage.png   — 915KB
/home/ubuntu/work/darius-star/assets/sprites/boss_charge.png — 747KB
/home/ubuntu/work/darius-star/assets/sprites/boss_fire.png   — 835KB
/home/ubuntu/work/darius-star/assets/sprites/boss_death.png  — 629KB
```

All: 1024x1024, transparent RGBA, confirmed on disk.

## Fix Applied

1. Verified all 5 boss sprites exist on disk
2. Posted "Nudge Executor — Breaking the Loop" comment referencing the 5+ prior completions
3. Moved issue to Done (`bbf71b3e-...`)
4. Transitioned labels: removed `agent:agy`, added `agent:done` (kept `requires:human-approval`)
5. Deleted all nudge files: `/tmp/prismatic/nudge-fred`, `/tmp/trigger-fred-work`
6. Updated `cleaned-signals-tracker.json`

## Root Cause

The agent session that completed the work crashed after posting comments but before transitioning the label and deleting the trigger file. The label stayed `agent:agy`, so the dispatcher treated the issue as still needing AGY work and kept routing.

## Prevention

After posting a completion comment, the agent MUST transition the label to `agent:done` in the SAME mutation batch or immediately afterward. If the session crashes between comment-post and label-transition, this variant occurs. Recommendation: use a single `issueUpdate` mutation that sets both `stateId` and `labelIds` in one call, reducing the window for crash-while-incomplete.
