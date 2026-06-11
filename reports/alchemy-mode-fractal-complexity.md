# Prismatic Engine — Fractal Complexity: Quality, Parallel Instances & Alchemy Mode

**Author:** Kai (Content Agent)  
**Date:** 2026-06-07  
**Status:** Exploration — Not Yet Scoped as Tasks

---

## 1. The Agent Bundle Problem

AGY is not a single-purpose agent — it's a bundle capable of:
- Vision/design (assets/ lane)
- Research (research/ lane)  
- Review (read-only across all lanes)
- Synthesis (reports/ lane)

**Problem:** A single lane assignment doesn't work for a multi-modal agent.

**Solution:** Lane assignment must be **per-task, not per-agent**. The PRISMATIC_ENGINE.yaml needs a capability registry:

```yaml
capabilities:
  agy:
    - role: designer
      lanes: ["assets/", "designs/"]
      branch_prefix: "design/"
    - role: researcher
      lanes: ["research/"]
      branch_prefix: "research/"
    - role: reviewer
      lanes: ["*"]  # read-only everywhere
      read_only: true
    - role: synthesizer
      lanes: ["reports/", "docs/"]
      branch_prefix: "synthesis/"
```

The dispatcher routes to a **capability**, not an agent name. The agent is just the runtime.

---

## 2. The Quality Spectrum

```
Garbage in → Garbage out        (no structure, no review)
Garbage in → Mystery gift out   (unpredictable LLM behavior)
Lead in → Gold out              (ALCHEMY — opinionated, repeatable)
```

### Alchemy Mode (Plugin)

A quality layer that wraps the core engine:

#### A. Structured Intake
A briefing agent converts raw user input into:
- **Core ask** — what does the user actually want?
- **Constraints** — budget, tone, brand, audience, format
- **Success criteria** — how will we know it's done well?
- **Reference material** — what context exists?
- **Preserved "heart"** — the user's intent/feeling that must not be lost

#### B. Opinionated Recipe System
Each output type has a fixed recipe:

```
Recipe: "Tour Page"
  1. Research (AGY: keywords, competitors, local knowledge)
  2. Outline (Kai: structure)
  3. First draft (Kai: write)
  4. Review (AGY: accuracy, SEO, brand voice)
  5. Refine (Kai: incorporate feedback)
  6. Polish (Fred: format, links, deploy)
  7. Verify (AGY: check live page)
```

#### C. Quality Gates with Explicit Criteria

```
Tour Page Review Gate:
  ✅ Title is H1, contains primary keyword
  ✅ Meta description under 160 chars with CTA
  ✅ All booking links point to FareHarbor correct shortname
  ✅ Hawaiian diacritical marks correct
  ✅ No corporate-speak
  ✅ Tour duration, price, location, gear accurate
  ❌ If any fail → revision requested with line items
```

#### D. Provenance Tracking
Every decision logged:
- "Kai chose 'crystal clear waters' because AGY research showed 23% better conversion"
- "AGY rejected first draft — missing difficulty rating"

---

## 3. Testing & Tuning

### Infrastructure Tests (deterministic — pass/fail)
From test-batches-v1.md:
- Lane compliance
- Lock integrity
- Branch discipline
- Pre-push validation

### Quality Tests (scored — 1-10)
Need to be added:
- **Output comparison** — same request twice, compare outputs
- **Regression suite** — archive 10 "gold" outputs, test new versions
- **Blind review** — human rates output without knowing which version
- **Edge case catalog** — vague requests, contradictions, missing context

### Tuning Levers
- Agent temperature (creative vs conservative)
- Review strictness (revisions before escalation)
- Context volume (how much research material)
- Agent specialization (generalist vs specialist per task)

---

## 4. Multiple Parallel Instances

Running 2-3 Prismatic Engine loops on the same repo:

```
PE-1: "Write 5 tour pages"  → uses Kai + AGY
PE-2: "Redesign nav"        → uses Fred + Codex
PE-3: "Create brand guide"  → uses AGY + Kai
```

### Problem: Same agents, multiple masters
- Kai can't work on PE-1 and PE-3 simultaneously
- AGY can't review PE-1 while designing PE-3

### Solution: Instance Scheduler (new core component)

```yaml
instances:
  - id: pe-1
    pipeline: content-pipeline
    priority: 1
    agents:
      kai: { role: writer,   max_concurrent: 1 }
      agy: { role: reviewer, max_concurrent: 2 }
  - id: pe-2
    pipeline: code-pipeline
    priority: 2
    agents:
      fred: { role: developer, max_concurrent: 1 }
  - id: pe-3
    pipeline: design-pipeline
    priority: 3
    agents:
      agy: { role: designer, max_concurrent: 2 }
```

The Instance Scheduler:
1. Checks agent availability (not locked, not at max)
2. Assigns from highest-priority pipeline first
3. Queues lower-priority tasks
4. Handles deadlock (PE-1 needs AGY but AGY is on PE-3)

---

## 5. What Exists vs What's Needed

| Component | Status |
|-----------|--------|
| Dispatch (route work) | ✅ Fred's dispatcher.py |
| Governance (locks, lanes, branches) | ✅ Designed, AGY fixes incorporated |
| 7-Step Loop (review→feedback→refine) | 🚧 GRO-819 designing |
| Alchemy Mode (quality gates + recipes) | ❌ Not designed |
| Instance Scheduler (parallel loops) | ❌ Not designed |
| Quality Metrics & Tuning | ❌ Not designed |
| Agent Bundle/Capability Registry | 🚧 Implied by GRO-815 findings |

---

## 6. Key Questions for AGY

1. **Agent Bundles:** Should the capability registry be part of PRISMATIC_ENGINE.yaml or a separate file?
2. **Quality Gates:** Can review criteria be expressed as structured YAML (like a checklist), or do they need natural language?
3. **Instance Scheduler:** Should this be part of the core dispatcher or a separate process that feeds the dispatcher?
4. **Alchemy Mode Plugin vs Core:** Is the structured intake / recipe system a plugin that uses the core, or a core feature that plugins extend?
