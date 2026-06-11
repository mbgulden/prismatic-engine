# Synthesis from Existing Artifacts

**Pattern:** When a task says "Read files X, Y, Z first — produce deliverable D," and X/Y/Z already exist as complete artifacts covering most of D's scope, the efficient approach is **synthesis** — adapt and combine existing work rather than building from scratch.

This is a sub-case of Step 0.5 Case 3a (Missing features) but with a specific technique: the "missing features" are integration, reformatting, and bridging sections, not net-new implementation.

---

## Detection

The issue description references specific files as input ("Read these files first"), and those files:
- Exist (verified with `ls` or `search_files`)
- Are substantive (50+ lines, not stubs or placeholders)
- Cover 60-80% of the required deliverable's scope

The deliverable is a **new format/combination** of existing content, not net-new research.

---

## Worked Example: GRO-819 (June 2026)

**Task:** "Design the 7-Step Iterative Loop — read: (1) 7-step spec, (2) Claude Code build pattern, (3) core evaluation, (4) Antigravity Hub source."

**Pre-existing artifacts found:**
- `specs/7-step-loop-specification.md` — 252 lines, covered Steps 1-3 and 5 of 5 requirements (missing Claude Code mapping + Mermaid diagram)
- `reports/agy-claude-code-build-pattern.md` — 166 lines, covered Section 4 (Claude Code mapping) and had a Mermaid diagram for the orchestration loop

**Remaining gaps:** Only 2 sections needed — Claude Code loop mapping (which existed in the second artifact) and a combined Mermaid diagram.

**Synthesis approach:**
1. Copy the 252-line spec as the base (it had the detailed step specs, mode switch, state machine, handoff)
2. Adapt Section 5 from the Claude Code report (mapping Claude's inner loop to Prismatic's outer loop)
3. Adapt the Mermaid sequence diagram from the Claude Code report (it already showed the full orchestration sequence)
4. Add the DONE SIGNAL checklist and header metadata

**Result:** 314-line deliverable in ~2 minutes vs. ~15+ minutes to research and write from scratch.

**What NOT to do:** Do not re-research the 7 steps or Claude Code's loop — the artifacts already contain the substantive content. Only the bridging and formatting are new work.

---

## When NOT to Synthesize

- **One artifact is a stub** (< 20 lines, placeholder content) — treat as Case 4 (fresh work)
- **Artifacts contradict each other** — treat as Case 4; you need to resolve the conflicts with fresh analysis
- **The deliverable format is fundamentally different** (e.g., input is markdown, output is a JSON API schema) — synthesis may still work but verify all transformations are correct
- **The issue is a review/audit task** — per Step 0.5's review-task exception, the review IS the deliverable

---

## Synthesis Execution Pattern

```
1. Verify all referenced artifacts exist + are substantive (wc -l)
2. Map each artifact to the deliverable's requirements — which artifact covers which section?
3. Identify gaps — which requirements have NO pre-existing artifact?
4. Assemble: base document + adapted sections from artifacts + new bridging content
5. Verify all deliverable requirements are met
6. Post DONE SIGNAL comment with artifact paths
```

---

## Efficiency Gains

- GRO-819: 314 lines delivered in ~2 minutes vs. estimated 15+ minutes from scratch (~87% time savings)
- Pattern applies when: 60%+ of deliverable exists in referenced artifacts, gaps are integration/bridging not net-new research
