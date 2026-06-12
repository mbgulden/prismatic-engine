---
name: alchemy-quality-gates
description: >-
  Apply Alchemy Mode quality gates to agent output — structured intake briefs,
  recipe-based agent chains, multi-stage YAML checklists, provenance logging,
  and bounded self-healing retry loops. Transforms "Mystery Gift" output into
  "Lead In → Gold Out" deterministic quality.
---

# Alchemy Quality Gates

## Trigger
Load this skill when executing tasks in Alchemy Mode or when the user requires
repeatable, high-quality agent output with provenance tracking.

## Overview

In standard agent execution, raw prompts fed directly to LLMs produce
unpredictable results ("Mystery Gift Out"). **Alchemy Mode** wraps the
Prismatic Engine with structured intake, recipe-based agent chains, strict
quality gates, and provenance logging to deliver gold-standard repeatable
outputs ("Lead In → Gold Out").

Alchemy Mode draws from three domains:

| Domain | Principle | Alchemy Translation |
|---|---|---|
| **Creative Agency Briefs** | No copywriter works from a raw sentence — use a Creative Brief with objectives, audience, tone, mandatories | Structured intake brief (`brief.yaml`) parsed before work begins |
| **CI/CD Quality Gates** | Code passes static analysis, dynamic tests, and semantic gates | Multi-stage YAML checklists with `blocker`/`warning` severities |
| **Constitutional AI** | Critic model evaluates drafts against a constitution, iterating until compliant | Reviewer agent evaluates output against gate criteria; structured failure reports |

## Quality Spectrum

```
Garbage in → Garbage out        (no structure, no review)
Garbage in → Mystery gift out   (unpredictable LLM; Standard Mode)
Lead in → Gold out              (ALCHEMY — opinionated, repeatable)
```

## Alchemy Mode Pipeline

### 1. Structured Intake (`brief.yaml`)

Every task begins with a briefing agent converting raw input into a structured
brief. Never feed raw text directly to worker agents.

```yaml
# brief.yaml — required fields
task_id: "string"
objective: "One sentence — what must this output achieve?"
audience:
  primary: "Who is this for?"
  knowledge_level: "beginner|intermediate|expert"
tone: "professional|conversational|technical|playful"
brand_voice: "Reference to brand guidelines"
mandatory_elements:
  - "Must include X"
  - "Must reference Y"
forbidden:
  - "Do NOT use jargon Z"
  - "Do NOT mention competitor W"
success_criteria:
  - "Measurable outcome 1"
  - "Measurable outcome 2"
```

### 2. Recipe Pipeline

Define a multi-stage recipe mapping roles and quality gates:

```yaml
# recipe.yaml
pipeline:
  - stage: draft
    agent: agent:ned
    gate: draft_quality_gate
  - stage: review
    agent: agent:agy
    gate: review_quality_gate
  - stage: publishing
    agent: agent:fred
    gate: publishing_quality_gate
```

### 3. Quality Gates (`gates.yaml`)

Each gate is a YAML checklist with `blocker` (must pass) and `warning`
(should pass) severities:

```yaml
# gates.yaml — example draft gate
draft_quality_gate:
  checks:
    - id: "structural-completeness"
      description: "All required sections present"
      severity: blocker
      validate: "count_sections >= required_sections"
    - id: "brand-voice-compliance"
      description: "Matches brand voice guidelines"
      severity: blocker
      validate: "voice_score >= 0.8"
    - id: "factual-accuracy"
      description: "No hallucinated claims, stats, or links"
      severity: blocker
      validate: "hallucination_check == clean"
    - id: "link-validity"
      description: "All URLs resolve (200 OK)"
      severity: warning
      validate: "broken_links == 0"
    - id: "grammar-spelling"
      description: "No spelling or grammar errors"
      severity: warning
      validate: "error_count < 3"
```

### 4. Self-Healing Loop

When a gate fails:
1. Reviewer agent generates a **structured failure report** with:
   - Exact line references
   - Severity level (blocker/warning)
   - Suggested fix
2. Original worker agent applies fixes and re-submits
3. **Bounded retry quota:** 3 attempts maximum
4. On exhaustion: **escalate to human** — do not loop indefinitely

```
EXECUTE → GATE_CHECK → (PASS) → NEXT STAGE
                    → (FAIL) → FAILURE_REPORT → REFINE → EXECUTE
                                    ↑                       │
                                    └── (retry_count < 3) ──┘
                                    (retry_count >= 3) → ESCALATE
```

### 5. Provenance Logging

Every Alchemy Mode run produces a provenance JSON file:

```json
{
  "task_id": "GRO-XXXX",
  "pipeline": "recipe-name",
  "started_at": "ISO8601",
  "completed_at": "ISO8601",
  "stages": [
    {
      "stage": "draft",
      "agent": "agent:ned",
      "gate_results": { "passed": 5, "failed": 0 },
      "retries": 0,
      "output_commit": "abc123"
    }
  ],
  "decisions": [
    {
      "timestamp": "ISO8601",
      "decision": "Replaced generic CTA with FareHarbor deep-link",
      "rationale": "Gate check: brand-voice-compliance requires specific booking flow"
    }
  ]
}
```

## Standard Mode vs. Alchemy Mode

| Dimension | Standard Mode | Alchemy Mode |
|---|---|---|
| **Intake** | Raw user prompt → agent | Briefing agent → `brief.yaml` → pipeline |
| **Validation** | Basic lint/test | Multi-stage YAML gates |
| **Self-Healing** | Compiler loop retries | Structured feedback + bounded retries (3) + escalation |
| **Provenance** | Git commit message | Full JSON tracking (edits, rationales, critiques) |
| **Review Rounds** | 0-1 | Minimum 2 (draft + publishing) |
| **Outcome Variance** | High (style drift, hallucinations) | Low (consistent, verifiable, auditable) |

## When to Use Alchemy Mode

- **Content generation** where brand voice and factual accuracy matter
- **Multi-agent pipelines** where handoffs must be verifiable
- **Client deliverables** requiring auditable quality
- **SEO content** where structured data and AEO compliance are required
- **Code generation** with security or compliance requirements

## When NOT to Use Alchemy Mode

- Quick one-shot fixes (add a log line, fix a typo)
- Exploratory research (AGY's domain)
- Interactive debugging sessions
- Tasks where the overhead of brief + gates exceeds the task itself

## Pitfalls

- ❌ **Skipping the brief:** Raw prompt → agent is "Mystery Gift Out." Always
  run the briefing agent first.
- ❌ **Unbounded retries:** Set the retry quota to 3. After exhaustion,
  escalate — do not loop.
- ❌ **Gate checklists too vague:** "Check quality" produces inconsistent
  results. Each check must have a specific, measurable `validate` condition.
- ❌ **Provenance as afterthought:** Log decisions at decision time, not at
  the end. Retrospective provenance is fiction.

See also: `prismatic-7-step-loop` skill, `lane-governance` skill.
