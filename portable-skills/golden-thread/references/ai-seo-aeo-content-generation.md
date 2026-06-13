# AI SEO / AEO Content Generation Workflow

## When to Use
For any venture needing to optimize content for AI citation engines (Google AI Overviews, ChatGPT, Perplexity):
- Active Oahu Tours: tour pages, location guides, rental pages
- HD Growth Engine: Type pages, Profile pages, gate/channel/center pages
- Your Hawaii Guide: beach guides, tour comparisons, regional guides
- Any content site targeting zero-click/answer-engine visibility

## Core Pattern

### Phase 1: Gather Intelligence (parallel)
```bash
# A. Load Google Drive strategy docs
mcp_gdrive_drive_read_file on each doc

# B. Parallel research via delegate_task (3 tasks max)
# Task 1: Competitive entity gap analysis
# Task 2: Statistical density research (verifiable stats w/ sources)
# Task 3: [Optional] Site crawl for gap audit
```

**Subagent specs:**
- Competitor analysis: `toolsets: ['terminal', 'web']` — curl/wget competitor sites, analyze content patterns
- Statistics research: `toolsets: ['terminal', 'web']` — query government DBs (DBEDT, NOAA, HTA), Wikipedia API, verify sources
- Site audit: `toolsets: ['terminal', 'web']` — curl site URLs, check for 404s, thin content, duplicate titles

### Phase 2: Synthesize Deliverables
Combine research outputs into:

1. **AEO Quick Answer Blocks** (50-75 words each):
   - H2 formatted as exact-match user question
   - First sentence answers the question directly — no intros
   - 2+ quantifiable data points per block
   - Every sentence is standalone-extractable (LLMs lift them verbatim)
   - Place immediately below H1 for each page

2. **FAQPage JSON-LD Schema**:
   - 3-6 Q&A pairs per page
   - Questions mirror AEO block H2s
   - Answers are condensed 1-2 sentence versions of AEO blocks
   - **For programmatic injection into existing pages** (extract visible FAQ → generate matching JSON-LD → insert in `<head>`), see `references/faqpage-schema-injection.md` — handles `<h3>`/`<h4>` variation, insertion point detection, and idempotent re-runs.

3. **Statistical Density Table**:
   - 15-20 verifiable statistics with sources
   - "AEO Quote-Ready Sentence" column — how to frame the stat
   - Map each stat to specific page URLs where it should be injected

4. **Gap Map**:
   - Page-by-page audit: what's missing, priority level
   - Fan-out queries competitors don't answer (26+ is a good target)
   - Quantitative data voids (vague language → hard stats)

### Phase 3: Inject & Track
1. Write deliverable to `~/work/alignment-deliverables/<venture>-aeo-blocks.md`
2. Post progress comment to Linear issue with: completed items, critical findings, next actions
3. If pages need rebuilding (404 recovery), prioritize those first — 404 pages with Search Console traffic are immediate revenue recovery
4. If the gap map phase uncovers broken pages (404s) or redirect chains (301s), run the full URL audit pattern in `references/site-migration-url-audit.md` to quantify total traffic loss across all sitemap URLs — the damage is usually larger than it first appears

## Key Principles (from Google Drive Strategy Docs)

### Information Gain
AI engines bypass content that regurgitates competitor consensus. Every block must contain data competitors don't have — exact distances, verified temperatures, specific regulations, operational statistics.

### Stat Density Rule
Pages with 5+ specific data points per 1,000 words get ~3x more AI citations. Replace all vague language:
- "short hike" → "0.15-mile scramble gaining 200 feet"
- "warm water" → "75°F in March to 82°F in September"
- "popular beach" → "2.5-mile crescent with 50-150ft width"

### Quote-Ready Format
AI models pull sentences word-for-word. Every key sentence must:
- Be grammatically standalone (no "it depends" or context-reliant pronouns)
- Contain a complete fact (who, what, where, when, how much)
- Be under 25 words for extractability

### Fan-Out Coverage
AIs expand user queries into multiple sub-queries. Cover those sub-queries with dedicated sections:
- Weather/conditions by month → "Kailua Bay wind conditions in November"
- Equipment specs → "Tandem kayak weight capacity"
- Logistics → "Parking at Kailua Beach before 8am"
- Comparisons → "Kailua vs Kaneohe sandbar: which to choose"

## Verification Checklist
- [ ] Every AEO block is 50-75 words
- [ ] Every block has 2+ hard numbers
- [ ] No block starts with fluff ("It depends", "Many people ask")
- [ ] Every block can be copy-pasted into an AI answer verbatim
- [ ] FAQPage schema validates at schema.org validator
- [ ] All statistics have verifiable sources
- [ ] 404 pages identified and flagged as priority fixes
- [ ] Linear issue updated with deliverable paths

## Common Deliverable Paths
- `~/work/alignment-deliverables/<venture>-aeo-blocks.md` — AEO blocks + FAQ schema
- `~/entity_gap_analysis/entity_gap_report.md` — Competitor analysis
- `~/oahu_tourism_statistics.md` — Statistical density research (venture-specific)
