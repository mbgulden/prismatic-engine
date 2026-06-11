# Pipeline Deliverable Quality Review Checklist

When an issue arrives as a review step (labeled `pipeline:*`, `agent:fred`, or described as "quality check + gap analysis"), use this checklist to assess deliverables before handing off to the next pipeline phase.

> **June 2026 example:** GRO-722 "AGY Review: Idaho AI Consulting Pipeline" — reviewed 23 leads across 7 sectors. Artifacts: `leads.json` (35KB), `idaho-pipeline.md` (539 lines), `outreach.py`. Strengths: complete contacts, custom hooks, verified research. Gaps: no email templates, no tracking, no follow-up cadence.

---

## 1. Artifact Verification

| Check | How | Signal |
|-------|-----|--------|
| Artifact files exist | `ls -la <path>` from issue description | File exists with expected size |
| Artifact structure valid | `read_file` first 20 lines + last 10 lines | Right format, not truncated |
| Artifact claims match reality | Cross-reference issue description with actual files | All claims in desc are reflected on disk |
| External dependencies exist | Check referenced scripts/utilities run without error | `python3 outreach.py --help` works |

---

## 2. Completeness Scan by Deliverable Type

### Lead Database / CRM (`leads.json` or similar)

- [ ] Minimum viable count (at least 10-15 leads for a new pipeline)
- [ ] Contact name, email, phone for each active lead
- [ ] Custom hook or bottleneck per lead (generic hooks = weak pipeline)
- [ ] Priority/sector classification (enables sequencing)
- [ ] Dead/moved leads marked (not silently dropped)
- [ ] No placeholder/fake data ("TODO", "researching", blank fields)
- [ ] Sector diversity (3+ sectors if targeting multiple industries)

### Strategy / Pipeline Document (`*-pipeline.md` or similar)

- [ ] CRM table with lead IDs, companies, contacts, status
- [ ] Detailed profiles per lead (beyond the table — context that enables personalization)
- [ ] Revenue projection (realistic range, not just top-line fantasy)
- [ ] Outreach strategy or sequence (when, how, cadence)
- [ ] Lead lifecycle stages defined (contact_found → contacted → replied → meeting → proposal → won → lost)
- [ ] Timestamp/version so freshness is knowable
- [ ] Authored-by attribution (so Michael knows who to ask about it)

### Technical Architecture / Deployment Plan

- [ ] Stack choices justified (not just "use X" but "use X because Y")
- [ ] Hardware requirements listed (VRAM, RAM, storage)
- [ ] Security model defined (auth, encryption, air-gap considerations)
- [ ] Deployment workflow (git? Docker? one-command spin-up?)
- [ ] Audit/logging defined (how to verify it's working in production)

### Email / Outreach Templates

- [ ] Personalized per sector or company (not one template for all)
- [ ] Subject line included
- [ ] Call to action clear (what happens next)
- [ ] Tracking mechanism defined (open tracking, reply routing)
- [ ] Fallback/follow-up sequence defined (what if no reply in 5 days?)

---

## 3. Structural Quality Check

| Dimension | Green Signal | Yellow Signal | Red Signal |
|-----------|-------------|---------------|------------|
| **Depth** | Detailed per-item data with context | Table-only, no profiles behind the rows | Placeholder content or "TODO" fields |
| **Organization** | Clear sections, navigation, cross-references | Sections present but no navigation | Wall of text, no headings |
| **Actionability** | A reader knows exactly what to do next | General direction given | "This needs work" with no specifics |
| **Freshness** | Updated within 48h | Updated within 7 days | No timestamp or >7 days stale |

---

## 4. Gap Detection Template

Ask these questions after scanning each artifact:

1. **What blocks execution right now?** — The single missing piece that prevents the next agent from taking action.
2. **What's missing for the next phase?** — Items that aren't blockers for review but will be blockers when the pipeline reaches them.
3. **What's the monitoring/feedback loop?** — If the deliverable is about outreach, how will we know it worked?
4. **What would make this undeliverable?** — Fatal issues (copyright risk, impossible hardware req, unattainable cert).

---

## 5. Review Comment Format

Post this structure to the Linear issue:

```
## 🔍 [Agent Name] Review: [Issue Title]

### ✅ Strengths
1. [Specific strength with concrete evidence]
2. [Second strength]
3. [Third strength]

### ❌ Gaps Found
1. **[Gap name]** — [Description of what's missing and why it matters]
2. **[Gap name]** — [Same pattern]

### 📋 Verdict
[One sentence: "Foundation is strong and production-ready. Gaps are execution-phase items, not research gaps." or "Needs significant rework before the next pipeline step."]

### Next Step
[Explicit: what happens now that the review is done. E.g., "Create a Phase 2 issue for email template generation." or "Pipeline is ready to launch."]
```

---

## 6. Pipeline State After Review

After posting the review comment:

| Field | Value |
|-------|-------|
| **Linear state** | In Progress (NOT Done — pipeline continues) |
| **Labels** | Keep existing `pipeline:*` label for the next agent |
| **Comment** | Posted with review findings |
| **Next agent action** | Determined by gap severity: fix gaps in existing issue, or create a new Phase 2 issue |

---

*Last updated: June 6, 2026 — Applied to GRO-722 (Idaho AI Consulting Pipeline review).*
