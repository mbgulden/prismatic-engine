# AEO + HowTo + FAQPage Schema Injection Reference

Reference script: `inject_seo_schema.py` in the active-oahu-tours-mirror repo root.

## Script Structure

Three-phase approach executed sequentially:

### Phase 1: HowTo Schema
- Target: instructional/guide pages with clear step-by-step content
- Schema: `@type: HowTo` with `step` array of `HowToStep` objects
- Each step has: position, name (one-liner), text (full instruction)
- Add `totalTime` in ISO 8601 format (PT4H, PT30M, etc.)
- Pages identified by: H2 headings that read like sequential instructions

### Phase 2: FAQPage Schema
- Target: pages with existing FAQ sections or Q&A content
- Schema: `@type: FAQPage` with `mainEntity` array of `Question`/`Answer` pairs
- Extract questions from `<strong>` or `<h3>` tags in FAQ sections
- Skip pages that already have FAQPage schema (idempotent)

### Phase 3: AEO Quick Answer Blocks
- Target: top-20 pages by traffic/revenue potential
- TWO injections per page:
  1. FAQPage JSON-LD before `</head>` (single Q&A)
  2. Visible HTML div after `<h1>` (styled Quick Answer box)
- 50-75 word answers targeting Google AI Overviews snippets
- Skip pages with no `<h1>` (redirect pages, thin pages) — inject schema only
- Skip pages that already have `aeo-quick-answer` class

## Verification

After injection, validate all JSON-LD blocks are well-formed:
```bash
python3 -c "
import re, json
with open('page.html', 'r') as f:
    content = f.read()
blocks = re.findall(r'<script type=\"application/ld\+json\">(.*?)</script>', content, re.DOTALL)
for i, block in enumerate(blocks):
    try:
        data = json.loads(block)
        print(f'Block {i+1}: @type={data.get(\"@type\")} OK')
    except json.JSONDecodeError as e:
        print(f'Block {i+1}: INVALID — {e}')
"
```

## Counting affected pages
```bash
# Pages with schema.org references
grep -rl "schema.org" --include="*.html" site/ | grep -v "/ja/" | wc -l

# Pages with AEO blocks
grep -rl "aeo-quick-answer" --include="*.html" site/ | wc -l
```
