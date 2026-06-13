# Document Ingestion Pipeline — INGEST-1→2→3→AGY Review

Proven pattern (June 2026) for turning raw documents into production Hermes skills. Three sequential phases, each validated before next.

## Phase Flow

```
Raw Docs → INGEST-1 (Index) → INGEST-2 (Extract) → INGEST-3 (Build Skills) → AGY Review → Done
```

## INGEST-1: Index (AGY)

**Goal:** Read raw documents, produce a structured index of capabilities.

**Input:** Raw markdown/PDF/text documents in `/tmp/doc-ingestion/` or project `docs/`.

**Output:** `01-index.md` — structured index mapping each document to its domains, key patterns, and suggested skills.

**AGY invocation:**
```bash
agy --print "Read /tmp/doc-ingestion/docs/. Produce structured index at /tmp/doc-ingestion/01-index.md. Map each doc to domains, key patterns, suggested skills." \
    --dangerously-skip-permissions --print-timeout 300s 2>/dev/null
```

**Verification:** Index covers all input docs, domain mapping is clear, skill suggestions are actionable.

## INGEST-2: Extract (AGY)

**Goal:** From the index, extract reusable capability blocks — commands, workflows, pitfalls, dependencies.

**Input:** `01-index.md` + original documents.

**Output:** `02-extracted-blocks/` directory with one `.md` file per capability domain. Each block contains:
- Exact copy-pasteable commands
- Workflows with numbered steps
- Pitfalls and resolutions
- Cross-domain dependencies
- Confidence score (1-5)

**AGY invocation:**
```bash
agy --print "Read /tmp/doc-ingestion/01-index.md. Read all referenced docs. Extract capability blocks. Save each to /tmp/doc-ingestion/02-extracted-blocks/<domain>.md" \
    --dangerously-skip-permissions --print-timeout 600s 2>/dev/null
```

**Verification:** Each block is 2-4KB, has all 5 sections, commands are exact (no placeholders where avoidable).

## INGEST-3: Build Skills (Fred)

**Goal:** Convert each extraction block into a Hermes skill with dual Hermes/AGY-native sections.

**Input:** `02-extracted-blocks/*.md`.

**Output:** Skills at `~/.hermes/profiles/orchestrator/skills/agent-orchestration/<skill-name>/SKILL.md`. Each skill has:
- YAML frontmatter (name, description, triggers, category)
- **Hermes Invocation** section — how Fred/Ned use it
- **AGY-Native Invocation** section — how AGY loads and executes it
- Pitfalls, workflows, dependencies

**Verification:** Skill parses via `skill_view()`, both invocation sections present.

## AGY Review (Quality Gate)

**Goal:** AGY reviews all new skills for completeness, AGY-native usability, and copy-pasteable commands.

**Input:** All new skill paths.

**Output:** `03-review/agy-skills-review.md` — per-skill verdict with PASS/NEEDS_FIXES.

**AGY invocation:**
```bash
agy --print "Read skills at <paths>. Review each for: Hermes section, AGY-native section, exact commands, pitfalls, confidence score. Save to /tmp/doc-ingestion/03-review/agy-skills-review.md" \
    --dangerously-skip-permissions --print-timeout 600s 2>/dev/null
```

**What AGY will flag:** Missing dual-section headers, placeholder commands, missing pitfalls. **What AGY will also flag (ignore):** "No confidence score" (skills don't need them — that's a research-report format, not an operational playbook), "Commands not exact" (skills are templates with `<placeholder>` — that's by design).

## Proven Throughput

**June 12, 2026 session:** 3 test docs → 162-line index → 11 extraction blocks → 9 new skills + 2 patched → AGY review → all live. Pipeline time: ~90 minutes end-to-end.

**Scale target:** 65+ Prismatic Engine documents. Same pattern, parallelized via:
- INGEST-1: 1 AGY session (reads all docs)
- INGEST-2: 2-3 parallel AGY sessions (domain-split)
- INGEST-3: Fred batch builds skills (11 skills in one session)
- AGY Review: 1 AGY session (reviews all)

## Pitfalls

- **INGEST-2 needs `--print-timeout 600s`** — complex extractions hit 300s limit
- **INGEST-1 may discover overlooked documents** — flag them for a second pass
- **Existing skills should be PATCHED, not replaced** — use `skill_manage(action='patch')` when adding to an existing skill
- **AGY will apply research-review standards to operational skills** — its "NEEDS_FIXES" for missing confidence scores or placeholder commands should be noted and dismissed
