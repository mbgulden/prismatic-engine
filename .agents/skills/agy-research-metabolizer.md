---
name: agy-research-metabolizer
description: "Use AGY as a high-agency deep research and knowledge-metabolism partner: multi-source discovery, evidence ledger, history/current/trajectory reports, non-obvious insights, and recommendations for architecture/content/golden-path decisions."
category: agent-orchestration
triggers:
  - AGY research
  - deep research report
  - metabolize information
  - source map
  - evidence ledger
  - website content engine research
  - golden path research
  - architecture research
---

# AGY Research Metabolizer

AGY is not just a reviewer or summarizer. Treat AGY as a high-agency research/operator partner. This skill gives AGY enough structure to produce high-quality research while preserving its freedom to investigate beyond the initial prompt.

## Core Principle

Give AGY:

1. **The goal** — what decision/output the research should support.
2. **Anchors** — specific files, repos, URLs, notes, prior assumptions.
3. **Freedom** — permission to look beyond anchors where useful.
4. **Artifacts** — exact reports/files to produce.
5. **Quality bar** — source traceability, confidence, synthesis, implications.

Do **not** reduce AGY to a narrow checklist executor. The point is to set up greatness, not constrain it.

## When to Use

Use this when Michael asks for:

- deep research on a subject
- history/current-state/trajectory analysis
- top repos/experts/articles/videos/forums/papers synthesis
- architecture planning from ecosystem research
- website content-engine research
- golden-path strategy
- competitive/market landscape reports
- knowledge digestion from many heterogeneous sources

## Launch Pattern

### Pure research / market / ecosystem study

Use a clean room and write a brief file first:

```bash
mkdir -p /tmp/agy-research/<slug>
# write /tmp/agy-research/<slug>/AGY_RESEARCH_BRIEF.md
cd /tmp && agy --prompt-interactive "Read /tmp/agy-research/<slug>/AGY_RESEARCH_BRIEF.md. Use the provided anchors first, then investigate beyond them where useful. Produce every report artifact requested in the brief. This is research/metabolization work; do not modify project code unless explicitly requested."
```

### Repo-anchored research

When a repo is central, AGY may need workspace access. Give it specific files first, plus freedom to inspect more:

```bash
cd /tmp && agy --prompt-interactive "Read /tmp/agy-research/<slug>/AGY_RESEARCH_BRIEF.md. Start with the specific repo files listed there. You may inspect additional files if needed to understand architecture, history, or golden path. Do not modify files. Write the requested reports under /tmp/agy-research/<slug>/reports/."
```

If AGY needs direct workspace tooling, include `--add-dir <repo>` but keep the no-modify constraint explicit for research:

```bash
cd /tmp && agy --prompt-interactive --add-dir /home/ubuntu/work/<repo> "Read /tmp/agy-research/<slug>/AGY_RESEARCH_BRIEF.md. This is repo-anchored research. Inspect files as needed, but do not modify source. Write reports only under /tmp/agy-research/<slug>/reports/."
```

### Implementation after research

If the research leads to code changes, create a separate AGY implementation task. Do not blend deep research and code mutation unless the user explicitly wants that combined loop.

## Research Brief Template

Create a brief with these sections:

```markdown
# AGY Research Brief: <Topic>

## Objective
Research <topic> so we can decide/build/understand <decision or output>.

## Downstream Use
- Architecture planning / website content engine / golden path / product strategy / implementation plan / market positioning.

## Audience
Who the reports are for.

## Known Context / Anchors
- Local files/repos:
- URLs:
- Existing assumptions:
- Prior reports:

## Freedom to Investigate
Use the anchors first, then investigate beyond them where useful to resolve gaps, validate claims, discover better sources, or improve synthesis.

## Source Targets
- Top GitHub repos:
- Experts/practitioners:
- Articles/blogs/docs:
- YouTube/podcasts/interviews:
- Forums/community discussions:
- Academic papers/articles:
- Local project files:

## Report Bundle
Choose one: brief / standard / deep / architecture / content-engine / competitive / golden-path.

## Required Artifacts
- source-map.md
- evidence-ledger.md
- 01-history.md
- 02-current-state.md
- 03-trajectory.md
- 04-insight-synthesis.md
- 05-recommendations.md
- optional content/architecture/golden-path reports

## Quality Bar
- Cite/source every major claim.
- Separate facts, interpretations, recommendations, and uncertainties.
- Mark confidence: High / Medium / Low.
- Identify contradictions and gaps.
- Produce synthesis, not just source summaries.
```

## Source-Type Ingestion Rules

### GitHub repositories

Extract:
- README/product promise
- architecture and directory model
- examples/tutorials
- dependency graph
- tests
- issues/PRs if available
- release history/changelog
- maintainer/contributor patterns
- signs of maturity or abandonment
- design tradeoffs and golden path

Questions:
- What problem does it solve?
- What abstractions does it expose?
- What does the source tree reveal that the README hides?
- What do issues/PRs reveal about user pain?
- What changed over time?

### Articles, blogs, docs

Extract:
- thesis
- claims
- evidence
- assumptions
- novelty
- author credibility
- date/context
- relationship to other sources

### YouTube, podcasts, interviews

Prefer transcripts. Extract:
- explicit claims
- implicit worldview
- anecdotes
- predictions
- mental models
- disagreements
- timestamped notable segments if available

If no transcript exists, summarize description/comments only and mark low confidence.

### Forums/community

Use for demand signals and language, not final truth. Extract:
- recurring pain
- objections
- workarounds
- misconceptions
- adoption barriers
- insider comments
- sentiment distribution

### Academic papers

Extract:
- research question
- method/dataset
- results
- limitations
- relationship to prior work
- practical implications
- whether it is still current

### Experts

Extract:
- domain of expertise
- recurring frameworks
- stance
- predictions
- disagreements
- operational advice
- track record where visible

## Report Bundles

### Brief
- executive summary
- top findings
- recommendations
- open questions

### Standard
- source map
- evidence synthesis
- executive synthesis
- recommendations

### Deep
- source map
- evidence ledger
- history report
- current-state report
- trajectory report
- insight synthesis
- recommendations
- appendices

### Architecture
- current-state report
- repo/tool landscape
- design patterns
- architecture implications
- golden path
- implementation recommendations

### Content Engine
- audience map
- source themes
- expert vocabulary
- search/content opportunities
- article cluster plan
- authority-building strategy
- recommended content briefs

### Competitive
- landscape
- differentiation
- category tensions
- adoption barriers
- risks
- opportunities

### Golden Path
- user journey
- setup friction
- canonical workflow
- docs/onboarding implications
- product/architecture recommendations

## Standard Output Template

```markdown
## Research Objective

## Scope and Method

## Source Map

## Executive Summary

## Key Findings

## History

## Current State

## Trajectory

## Cross-Source Synthesis

## Non-Obvious Insights

## Implications
- Architecture
- Product
- Content
- Golden Path
- Risks

## Recommendations

## Open Questions

## Confidence and Limitations

## Source Notes / Appendix
```

## Quality Standards

- **Traceability:** Every important claim ties back to source evidence.
- **Source diversity:** Include official sources, implementation artifacts, expert interpretation, user/community discussion, historical context, and disconfirming views.
- **Cross-source comparison:** Explicitly identify agreement, disagreement, stale info, hype, and unresolved questions.
- **Time awareness:** Distinguish old/foundational, recently changed, currently dominant, emerging, and speculative.
- **Confidence labels:** High / Medium / Low.
- **Insight over summary:** Produce mental models, tensions, implications, and recommendations.

## Prompt Pattern

```text
You are conducting deep research on <TOPIC> to support <DECISION/OUTPUT>.

Use the provided context and anchors first:
- <FILES/REPOS/LINKS/NOTES>

You may investigate further where useful. Do not stop at summarization. Metabolize the information into reusable insight.

Produce:
1. Source map
2. Evidence ledger
3. History report
4. Current-state report
5. Trajectory report
6. Non-obvious insights
7. Recommendations for <architecture/content/golden path/product/etc.>
8. Open questions and suggested follow-up research

For each major claim, indicate source basis and confidence. Separate facts, interpretations, recommendations, and uncertainties.
```

## Anti-Patterns

Do not let AGY:

- summarize sources one-by-one without synthesis
- over-trust forum sentiment
- conflate old sources with current reality
- treat a README as full product reality
- ignore issues/PRs/release history for repos
- produce recommendations without evidence
- hide uncertainty
- overfit to the user’s initial assumptions
- stop at “top 10 links” instead of metabolizing what they mean

## Verification

Before calling done, verify:

- all requested report files exist and are non-empty
- source map includes source type + relevance + confidence
- evidence ledger has traceability
- reports include history/current/trajectory, not just summary
- recommendations connect to the downstream use case
- uncertainties/open questions are explicit

## Linear/Prismatic Notes

When turning this into repeatable engine utility:

- canonical CLI should use `prismatic-*` naming, not `hermes-*`
- Hermes skills are harness ergonomics, not the engine contract
- a future utility should generate brief/report scaffolds and AGY launch commands
- for implementation tasks, include “Documentation update (in this same commit)” in Linear descriptions
