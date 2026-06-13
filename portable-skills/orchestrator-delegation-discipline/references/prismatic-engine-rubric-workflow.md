# Prismatic Engine Rubric → Task Pipeline

**Proven:** June 11, 2026  
**Pattern:** Assess multi-agent orchestration system → score against rubric → create phased tasks → launch AGY on foundational builds

## When to Use

When the user asks for a comprehensive assessment of their agent orchestration system ("how well are we using this, what's missing, make a rubric"), and wants actionable tasks from the assessment.

## The 5-Phase Workflow

### Phase 1: Research (read, don't re-research)
Before dispatching AGY, check if prior research exists:
- `find /home/ubuntu/work/prismatic-engine/reports -name "*.md"` — AGY already produced 24 reports
- `find /home/ubuntu/work/prismatic-engine/specs -name "*.md"` — architecture specs
- Read the synthesis briefing (agy-comprehensive-synthesis-briefing.md) — it already maps all reports to a current-state audit
- Only dispatch AGY if existing research is insufficient or stale (>1 week with significant code changes)

### Phase 2: Build the Rubric
Score 6 dimensions (Dispatch, Governance, Visibility, Refinement, Portability, Skills) on a 0-5 scale:
- **5:** Production-grade, daily use
- **4:** Working but rough edges
- **3:** Partial — works in some contexts
- **2:** Designed but not built
- **1:** Identified but no plan
- **0:** Blind spot

Each dimension gets 10-15 specific scored items. Total possible: ~265 points. Current score: 89 (34%).

### Phase 3: Identify Top 10 Gaps
Sort by (impact × gap_size). Governance and Portability are typically the biggest gaps because they require cross-cutting infrastructure.

### Phase 4: Create Phased Linear Tasks
Split tasks by agent lane:
- **AGY (P1-P2):** Foundational design + build — lane governance, dashboards, portable skills, standalone mode
- **Ned (P1-P2):** Code implementation — CLI tools, pre-push hooks, data wiring
- **Jules (P1-P2):** Code review + gap analysis against specs

Each task: specific deliverable, reference to existing spec/doc, target repo, estimated scope.

### Phase 5: Launch AGY on Phase 1
AGY excels at foundational work. Launch sequentially (not parallel — AGY works best focused):
1. Lane governance (PRISMATIC_ENGINE.yaml + SOUL.md)
2. Portable skills packaging
3. Dashboard design (mobile-responsive, dark theme)
4. Standalone mode (pip package + SQLite + subprocess adapter)

## Pitfalls

- **Don't re-research what's already researched.** AGY's 24 prismatic-engine reports from June 8 contained complete specs. The gap was implementation, not research.
- **Dashboards need manifest.json updates.** Each plugin has a `manifest.json` that declares tabs. AGY must update `tabs` field and `index.js` entrypoint, not just `index.html`.
- **AGY timeout on standalone mode.** The standalone pip package build can exceed 600s. Break into phases if needed, or retry — second launch often succeeds.

## Key Files

| What | Where |
|------|-------|
| Rubric assessment | `/home/ubuntu/work/prismatic-engine/reports/rubric-assessment-2026-06-11.md` |
| AGY synthesis briefing | `/home/ubuntu/work/prismatic-engine/reports/agy-comprehensive-synthesis-briefing.md` |
| Core evaluation | `/home/ubuntu/work/prismatic-engine/reports/core-evaluation.md` |
| Architecture spec | `/home/ubuntu/work/prismatic-engine/specs/prismatic-engine-architecture-v1.md` |
| 7-step loop spec | `/home/ubuntu/work/prismatic-engine/specs/7-step-loop-specification.md` |
| Dashboard mockup | `/home/ubuntu/work/agentic-swarm-ops/docs/architecture/prismatic-hub-dashboard-mockup.md` |
| Plugin dashboards | `/home/ubuntu/work/agentic-swarm-ops/plugins/hermes-plugin-*/dashboard/` |
| Portable skills | `/home/ubuntu/work/prismatic-engine/portable-skills/` |
| Agent profiles | `/home/ubuntu/work/prismatic-engine/profiles/` |
