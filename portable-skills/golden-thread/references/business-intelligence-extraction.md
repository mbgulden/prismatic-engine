# General Business Intelligence Extraction from Google Drive

## When to Use This Pattern

Use this when a task asks you to:
- "Confirm Google Drive access and extract business notes"
- "Find all the research docs on [topic]"
- "Pull together what's in Drive about [project/industry]"
- "Read through Michael's GDrive and summarize findings"

This complements the **Alignment Document Processing** pattern (which processes known-alignment deliverables). This pattern is for **prospecting** — you don't know exactly what's there, so you search broadly and triage.

## Worked Example: GRO-27 (Jun 6, 2026)

**Task**: "Confirm Google Drive/Gemini docs access and extract business notes"

### Step 1: Confirm Access
```
mcp_gdrive_drive_about()
→ user: Michael Gulden (mbgulden@gmail.com)
→ storage: 30TB limit, ~14TB used → access confirmed
```

### Step 2: Multi-topic Search
Run 3-4 parallel searches with different lenses:
```
mcp_gdrive_drive_search(query="business notes")      → interview notes, legal docs, pivot strategy
mcp_gdrive_drive_search(query="Gemini")               → research folder, toolkits, AI workflows
mcp_gdrive_drive_search(query="consulting")           → bootcamp doc, alignment docs, lead-gen strategy
mcp_gdrive_drive_search(query="<project name>")       → any project-specific content
```

### Step 3: Priority by Recency
Sort results by `modifiedTime`. Docs modified in the last 7-14 days are highest priority — they reflect current strategic thinking. For GRO-27, the top docs were:
- "AI consulting Bootcamp" (Jun 4) — wanted course structure
- "Ai consulting ideas" (Jun 4) — BeyondSaaS philosophy
- "Growth web dev Ai sites" (Jun 4) — brand rename, site build
- "North Shore & Sharks Cove Interview" (Jun 4) — tour content

### Step 4: Check Dedicated Research Folders
Search for folders, not just loose docs:
```
mcp_gdrive_drive_search(query="Michael's Research with Gemini", mimeType="application/vnd.google-apps.folder")
→ folder ID: 11SdJHwbt5Ohlpx6Yj5YMpcgrPCCPyx9b
```
Then search inside the folder to discover content that broad searches miss. For GRO-27, this revealed 21 docs including Micron AI roles, Becca's AI Ultra Toolkit, Asset Forge 3D, and earlier research.

### Step 5: Breadth-First Reading
Read 6-10 docs at 3000-5000 chars each. Don't deep-read — you're looking for themes, not full comprehension:

| Doc | Recency | Signal | Read Depth |
|-----|---------|--------|------------|
| Consulting Bootcamp | Jun 4 | High (active project) | 5000 chars |
| AI Consulting Ideas | Jun 4 | High (core philosophy) | 5000 chars |
| GrowthWebDev Sites | Jun 4 | High (brand rename ask) | 5000 chars |
| AI Transformation Plan | May 24 | Medium (strategy doc) | 5000 chars |
| Kaneohe Bay Pivot | May 31 | Medium (legal pivot) | 5000 chars |
| Alignment 7: Lead-Gen | May 25 | High (actionable) | Full |
| Alignment 2: Outreach | May 25 | High (templates ready) | Full |
| Hermes Capability Pkg | May 27 | Medium (reference) | 5000 chars |

### Step 6: Compile Structured Notes
Save to `~/work/research/<domain>-notes-<YYYYMMDD>.md`. Structure:

```markdown
# Business Notes Extracted from Google Drive — YYYYMMDD

## Source: GRO-XX (archived) — "Original task title"
## Status: ✅ Complete. Drive access confirmed. Docs readable. Notes saved.

---

## 1. [Doc Title — Most Recent First]
**Doc:** "Original name" (updated Mon DD)
**Key Notes:**
- Bullet one
- Bullet two
- ...

## 2. [Next Doc]
...

---

## Next Actions Suggested by These Notes
1. Action one — implied by doc findings
2. Action two
3. ...
```

### Step 7: Register Findings
- If a new strategic direction or venture emerges → update `project-registry.json`
- If research that feeds the `research/queue.json` → update queue
- Otherwise → the markdown file itself is the deliverable

## Signal Categories to Watch For

When scanning, flag these as high-priority signal:

| Signal | What it means | Action |
|--------|---------------|--------|
| "Change the name from X" | Brand pivot needed | Create Linear issue for rename |
| "I want to build a [course/product]" | New venture scope | Add to golden thread, create issues |
| "70% of AI initiatives fail" | Consulting positioning | Extract as sales/marketing content |
| "Pivot to comply with regulations" | Legal risk | Prioritize compliance work |
| "Queue this on Linear" | Direct instruction | Create the issues immediately |
| Doc contains email/LinkedIn templates | Ready-to-use assets | Save as templates in consulting repo |

## Output Quality Checklist

- [ ] Drive access confirmed (account name, email, storage status)
- [ ] Searched with 3+ diverse query terms
- [ ] Checked for dedicated research folders
- [ ] Read docs breadth-first (6-10 at summary depth)
- [ ] Saved structured markdown with source attribution
- [ ] Included "Next actions suggested by these notes" section
- [ ] Cleaned up trigger file (if nudge executor run)
