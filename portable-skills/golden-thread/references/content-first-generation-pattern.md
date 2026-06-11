# Content-First Generation Pattern

## When to Use
Any task that involves generating a large batch of artifacts (voice files, SEO pages, sprites, docs) from source content. The pattern applies whether the generation is via API (Gemini TTS, Imagen, Vertex AI) or mock/fallback.

## The Pattern

### Phase 1: Verify Existing Content (don't redo work)
Before writing anything new, check what content already exists on disk. The source content (banter lines, character profiles, SEO research) may already be complete — only the generated artifacts (audio files, HTML pages, sprite sheets) are missing.

**Darius Star voice example:** `banter_engine.js` already had 80 pull-out lines across 10 biomes, verified by `verify_pull_out.js`. The content was done. Only the TTS audio files were missing.

**Checklist:**
- Search for source content files (`.md`, `.json`, `.js` with dialogue/text arrays)
- Run any existing verification scripts (`verify_pull_out.js`, `pytest`, linters)
- Count what exists vs what the issue asks for
- If source content is complete → skip to Phase 3 (generation)
- If source content is partial → Phase 2 then Phase 3

### Phase 2: Create the Master Content Document
Write ONE comprehensive document that catalogs ALL content across ALL categories the issue asks for. This serves as:
- Single source of truth for all future generation
- Validation checklist — every line in this doc should produce one output file
- Handoff artifact — the next agent can pick up from here

**Structure:**
```markdown
# TITLE — Master Document

## CATEGORY 1: [Name] (N items)
| # | Key fields... | Line/Content |
|---|--------------|-------------|

## CATEGORY 2: [Name] (N items)
...

## MANIFEST SUMMARY
| Category | Count | Status |
|----------|-------|--------|

## Generation Command
```bash
generate_tool.py --all
```
```

**Rules:**
- Use tables for structured data (one row per line/item)
- End with a manifest summary table (category → count → status)
- Include the exact generation command at the bottom
- Keep it self-contained — no external dependencies to understand the content

### Phase 3: Generate Artifacts
Run the generation tool. If the real API is unavailable (credentials, quota, billing), use the mock/fallback path to establish the file structure.

**Darius Star voice example:**
```bash
# Mock (always works, no credentials):
python3 generate_voice_assets.py --mock --all

# Real TTS (needs GCP credentials):
python3 generate_voice_assets.py --all
```

**Benefits of mock-first:**
- Establishes the full directory structure and file naming
- Proves the pipeline works end-to-end
- Creates the manifest JSON with all expected entries
- Real API generation can replace files in-place later
- Progress is visible (files on disk, manifest updated)

### Phase 4: Verify & Commit
- Count output files against manifest summary
- Check directory size is reasonable (not all 0-byte stubs)
- Verify manifest JSON is updated
- Commit everything together: master doc + generated files + manifest
- The commit message should reference the Linear issue and include counts

## Real Example: Darius Star Voice Pipeline (GRO-1010)

**Input:** Issue asking for "New recordings for all 8 fighters + pull-out lines (80), retreat/checkpoint (100), bonding moments"

**Phase 1 — Verification:**
- `banter_engine.js` had 80 pull_out lines (verified by `verify_pull_out.js` → 10 biomes × 8 chars = pass)
- `BANTER-AND-SYSTEMS.md` had 535 banter lines (parsed by `generate_voice_assets.py`)
- `mission-briefings.json` had 176 briefing lines
- `assets/audio/voice/` did NOT exist → artifacts needed generation
- Source content was complete → skip Phase 2 content writing for banter/briefing
- But: checkpoint restart lines (100) and bonding moments (47) did NOT exist in any doc → needed writing

**Phase 2 — Created `docs/voice-lines-master.md`:**
- Cataloged all 80 pull_out lines from banter_engine.js (extracted via Python regex)
- Wrote 100 new checkpoint restart lines (10 biomes × 10 sub-levels, solo/duo/4P variants)
- Wrote 47 new bonding moments (6 character pairs)
- Wrote 16 Lyra emotional core highlights
- Ended with manifest summary table and generation command

**Phase 3 — Mock generation:**
```bash
python3 generate_voice_assets.py --mock --all
# Output: 711 files, 53 MB, ~8 minutes
```

**Phase 4 — Commit:**
- 714 files changed (711 .ogg + manifest + sfx + master doc)
- Pushed to `master`
- Issue moved to Done with label swap `agent:fred` → `agent:done`

## Anti-Patterns to Avoid
- **Don't skip Phase 1 and redo work.** The banter content was ALREADY done. Generating it again would have been wasted effort.
- **Don't generate without a master doc.** The doc is the validation checklist. Without it, you can't verify completeness.
- **Don't wait for real API credentials.** Mock generation establishes the structure and proves the pipeline. Real API can fill in later.
- **Don't commit generated files without the source doc.** The doc explains WHY each file exists and what content it contains.
