# Nudge Executor — Dead-Agent Pre-Completed Work Detection

## Why This Exists

When the nudge executor (LLM-driven cron `c2cce4fec4ed`) picks up a task that was dispatched to a dead/unresponsive agent (Kai, AGY, Jules, Codex), the target agent may have **already completed the work** but never updated Linear state. The artifacts exist on disk, the code is written, the CSS is applied — but the issue is still in "Todo" with the old `agent:*` label.

### Two Detection Patterns

**Pattern A — Walkthrough comment exists (Kai pattern):** The agent posted a completion comment with artifact paths. Verify those paths exist, map to scope, mark done. This is the primary pattern and the one most pipeline-handoff reference docs cover.

**Pattern B — Unlinked artifacts (GRO-27 pattern):** No agent ever posted a completion comment. No artifact paths appear in any issue comment. But the artifacts exist on disk under `~/work/research/` or the project repo's `docs/` or `plans/` directories, created by a session that was processing the same trigger file but never cleaned up. The artifacts are **unlinked** — they exist but nobody connected them back to the Linear issue. Detection requires the broad search (Step 0.5 Searches B–E in the SKILL.md) rather than issue-comment-driven verification.

**Why Pattern B happens:** An agent session picks up the trigger file, does the research/implementation, saves artifacts to `~/work/research/<topic>/`, but crashes or times out before it can: (a) post a comment on the Linear issue, (b) move the issue, (c) delete the trigger file. The dispatcher then writes a new trigger file on its next cycle because the issue is still in Backlog. This creates an infinite retry loop — the trigger file gets recreated every N minutes, and every agent that picks it up finds work already done, but no agent leaves the system-wide breadcrumb that would make the dispatcher stop.

**Key signal that tells Pattern A from Pattern B:** Count the "Dispatcher routed to Agent X" comments on the issue. If there are 10+ of them and zero artifact-bearing comments (walkthrough, summary, implementation plan), you're in Pattern B. The dispatcher loop has been running for hours with no actual agent completing the full lifecycle.

**How to resolve Pattern B:**
1. Search broadly (Step 0.5 Searches B–E) for artifacts across `~/work/research/` using the signal topic
2. If found: post a completion comment linking to the artifact paths (even though your session didn't create them — you're the one closing the loop)
3. Move the issue to Done
4. Delete the trigger file (breaks the dispatcher loop permanently)
5. **Do NOT create a new Linear issue** — the archive was intentional if the old one is below GRO-500

### Concrete Example: GRO-27 (June 6, 2026)

**Situation:** 15+ dispatcher comments routing GRO-27 to Fred over 4 hours. Zero artifact-bearing comments. Trigger file written and re-written every cycle.

**Artifacts found via broad search:**
- `~/work/research/sentinel-itad/gdrive-business-notes-extracted.json` — structured extraction of 4 Sentinel ITAD Google Docs
- `~/work/research/gdrive-gemini-biz-notes-extraction.md` — comprehensive 6-doc extraction summary
- `~/work/research/business-notes-extracted-20260606.md` — 8-doc business notes covering AI Consulting, Active Oahu, Kaneohe Bay law

**Outcome:** Posted completion comment with artifact paths. Moved GRO-27 to Done. Deleted trigger file. Dispatcher loop broken.

The existing pipeline handoff reference (`nudge-executor-pipeline-handoff.md`) assumes the nudge executor itself **does the work**. This reference covers the case where the work is **already done** — the correction is administrative, not technical.

## The Pattern: Verify First, Build Only If Needed

### Step 1: Read the Issue Comments

Before doing any work, read ALL comments on the issue. Look for completion signals:

- "Phase Complete" / "Step done" / "Implementation complete"
- A walkthrough list of artifacts and paths
- An "Implementation Plan" comment followed by a "Walkthrough" comment (work was planned AND executed)
- File paths mentioned in comments (check if they exist on disk)

### Step 2: Check Artifacts on Disk

Check every path mentioned in comments or in the issue description's "Deliverables" section:

```bash
ls -la <path> 2>/dev/null && wc -l <path> 2>/dev/null
```

For CSS files: compare the backup (`style.css.bak`) with the current file to verify overrides were actually appended.

For generated pages: count files or check modification timestamps.

**Don't trust a comment that says "done" without artifact verification.** The comment may be aspirational (an implementation plan, not a completion report).

### Step 3: Match Against Issue Scope

Map the issue's deliverables to what exists on disk:

| Deliverable from Description | Artifact Found? | Quality Check |
|------------------------------|-----------------|---------------|
| Item A from scope | ✅ File exists | Quick content scan |
| Item B from scope | ✅ File exists | Quick content scan |
| Item C from scope | ✅ Nav overrides in CSS | Template references verified |

**If ALL deliverables are met: the work is pre-completed. Move to Step 4.**
**If some deliverables are missing or incomplete: do the missing work, then move to Step 4.**

### Step 4: Pipeline Handoff Decision

Determine if the issue is the terminal step in its pipeline:

**Terminal step** (no further pipeline stages, or scope is self-contained): 
- Move issue to **Done**
- Add `agent:done` label
- Comment: "Work verified complete. Pipeline step was already shipped by [agent]. Moving to Done."

**Intermediate step** (pipeline continues):
- Move issue to **In Progress**
- Update `agent:*` label to the next agent in the chain
- Comment: "Step N verified complete on disk. Handing off to [next agent] for pipeline step N+1."
- Do NOT mark Done — the pipeline isn't finished

**How to tell if it's terminal vs intermediate:**
- `pipeline:*` labels often encode the stage. After a `pipeline:visual-design` or `pipeline:content` step, check if the project has issues for the next stage.
- Look at the issue's description integration section: if it says "apply X to Y built in GRO-ZZ" and GRO-ZZ is also labeled `agent:done`, both are ready to close together.
- If no subsequent pipeline step exists, this is the terminal step.

### Step 5: Clean Up Dependent Issues

If this issue's work closes a dependency chain (e.g., GRO-751 applies brand styles to GRO-750's nav redesign), move BOTH to Done together. Post a comment on each linking them.

### Step 6: Clean Up Siblings + Delete Trigger

Follow the standard sibling cleanup pattern (`nudge-executor-sibling-cleanup.md`), then delete `/tmp/trigger-fred-work`.

## Concrete Example: GRO-751 (June 6, 2026)

### Timeline

| Time | Event |
|------|-------|
| ~15:30 | Kai processes GRO-751: brand audit, CSS variables, nav overrides, showcase page |
| ~15:33 | Kai posts walkthrough comment listing all artifact paths — work is DONE |
| — | Issue stays in "Todo" with `agent:kai` — nobody moved it |
| 16:00+ | Dispatcher routes GRO-751 to Kai 6 times — no pickup from Kai |
| 12:21 (next day) | Nudge executor picks up GRO-751 from trigger file |
| 12:22 | **Artifact pre-verification:** all 5 files confirmed on disk, scope 100% complete |
| 12:23 | **Pipeline resolution:** both GRO-751 (terminal visual-design step) and GRO-750 (dependent nav redesign) moved to Done. Comments posted. Sibling check clean. Trigger file deleted. |

### Detection Signals Used

1. **Walkthrough comment exists** — Kai posted a step-by-step with artifact paths
2. **Files exist at those paths** — all 5 files confirmed with `ls -la` and `wc -l`
3. **Nav overrides in `style.css`** — confirmed brand variables + nav selectors appended
4. **Issue scope mapped 1:1** — all 3 deliverables (A: brand guide, B: code reference, C: nav application) met
5. **GRO-750 also had `agent:done`** — both issues ready for parallel close

### Pitfall: Implementation Plan vs Completion Comment

Kai posted THREE comments on GRO-751 in this order:
1. "Implementation Plan" — describes planned steps, NO artifact paths
2. "Summary Response" — describes accomplishments but NO artifact paths
3. "Walkthrough" — step-by-step WITH artifact paths

**Only the walkthrough comment proves work was done.** The first two are planning/reporting. Always skip to the last substantive comment for verification.

### Pitfall: The "Todo" state is deceptive

GRO-751 was in "Todo" — which normally means unstarted work. The stale-issue verification in Golden Thread (Step 3.5) only checks "In Progress" issues for completion. **An issue in Todo with artifacts on disk is invisible to the standard verification loop.** The nudge executor is the only system that catches these — which is exactly what happened here.

## Quick Reference Card

```
1. Read ALL comments — look for walkthrough with artifact paths
2. Verify EVERY artifact on disk — ls -la + wc -l + content spot-check
3. Map deliverables → artifacts — all present? Pre-completed.
4. Terminal pipeline step? → Move to Done. Intermediate? → Move to In Progress.
5. Close dependent issues together (post comments linking them)
6. Sibling check → Delete trigger file
```
