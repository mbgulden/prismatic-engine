# AOT Static Mirror — Batch Operations

Two most common task types Ned handles on Active Oahu Tours static mirror (`/home/ubuntu/work/active-oahu-tours-mirror`). Both follow the same workflow: script → run → verify → commit → push.

## Task Type 1: Schema Injection (Product, Review, FAQ, BreadcrumbList)

**Typical trigger:** Issue like GRO-1297 "Deploy Product + Review + FAQ schema sitewide."

### Pre-flight
```bash
cd /home/ubuntu/work/active-oahu-tours-mirror
git branch --show-current  # Must be master
git status                  # Check for pre-existing dirty files
wc -l inject-all-schema.py  # Current version: ~533 lines (comprehensive), old: ~304 lines
```

### The Script
`inject-all-schema.py` handles all schema types:
- **Product** with `offers` (price, availability, currency) — for tour/rental pages
- **AggregateRating** nested in Product (extracted from testimonial HTML)
- **FAQPage** (extracted from H2/H3 headings on guide pages)
- **BreadcrumbList** (EN + JA paths, every non-home page)
- **Article** fallback for blog/guide pages
- **JA bilingual** support throughout

### Run
```bash
python3 inject-all-schema.py
# Output: "Found 257 HTML pages. Successfully injected: 255. Skipped: 2 (homepages)."
```

### Verify
```bash
# Product schema on tour page
grep -c '"@type": "Product"' site/activities/chinamans-hat-oahu-kayak-tours/index.html
# FAQ schema on guide page
grep -c '"@type": "FAQPage"' site/guides/ocean-kayaking-beginners-oahu/index.html
# AggregateRating on a review-heavy page  
grep -c 'aggregateRating' site/activities/chinamans-hat-kayak-rentals/index.html
# BreadcrumbList everywhere
grep -c '"@type": "BreadcrumbList"' site/activities/chinamans-hat-oahu-kayak-tours/index.html
# JA pages also covered
grep -c '"@type": "Product"' site/ja/activities/kailua-bay-mokulua-island-self-guided-kayak-tour/index.html
```

### Commit & Push
```bash
git add inject-all-schema.py site/
git commit -m "[Ned] GRO-XXXX: Inject Product, Review, FAQ, BreadcrumbList schemas sitewide — 255 pages (EN + JA)"
git push origin master  # Deploys via Cloudflare Pages
```

### When Work Already Exists on Feature Branch
If `git log --oneline --all --grep="GRO-XXXX"` finds the work on a non-master branch (e.g., `audit/agy-GRO-1297`):
1. Extract the updated script: `git show audit/agy-GRO-1297:inject-all-schema.py > /tmp/new-script.py`
2. Checkout master, replace old script with new: `cp /tmp/new-script.py inject-all-schema.py`
3. Run on master's site directory (not the feature branch's)
4. Commit and push to master directly

**No cherry-pick needed** — the script is self-contained and page injection is idempotent.

---

## Task Type 2: CTA Deep-Linking (Header "Book Online" → FH.open)

**Typical trigger:** Issue like GRO-1299 "Improve global header CTA — open specific item instead of full catalog."

### Step 1: Extract FH Item Codes from Existing Deep-Links
```python
import re, json
from pathlib import Path

site_dir = Path('/home/ubuntu/work/active-oahu-tours-mirror/site')
page_items = {}

for f in sorted(site_dir.rglob('*.html')):
    if '_templates' in str(f):
        continue
    html = f.read_text(encoding='utf-8')
    items = re.findall(r"FH\.open\(\{'shortname':'activeoahutours','view':\{'item':'(\d+)'\}", html)
    if items:
        page_items[str(f.relative_to(site_dir))] = items[0]

# Save mapping
with open('/tmp/fh_item_mapping.json', 'w') as f:
    json.dump(page_items, f, indent=2)
```

Typical result: 129 pages → 18 unique item codes. Largest: `115595` (48 pages — general tours/rentals CTA).

### Step 2: Add `data-fh-item` to Body Tags
```python
for rel_path, item_code in page_items.items():
    path = site_dir / rel_path
    html = path.read_text(encoding='utf-8')
    new_html = re.sub(r'(<body\b[^>]*)>', rf'\1 data-fh-item="{item_code}">', html, count=1)
    if new_html != html:
        path.write_text(new_html, encoding='utf-8')
```

### Step 3: Replace Static Catalog CTA with JavaScript Deep-Link
The old CTA pattern (consistent across all 247+ pages):
```html
<div class="social-links"> <a class="pull-right btn btn-small btn-primary" href="https://fareharbor.com/embeds/book/activeoahutours/?u=f9b48d18-715e-4919-9c8e-077c045cf4bf&amp;from-ssl=yes"> <strong> <span class="glyphicon glyphicon-calendar"></span> Book Online </strong></a>
```

New CTA:
```html
<div class="social-links"> <a class="pull-right btn btn-small btn-primary" href="https://fareharbor.com/embeds/book/activeoahutours/?u=f9b48d18-715e-4919-9c8e-077c045cf4bf&amp;from-ssl=yes" id="header-book-online" onclick="(function(){var el=document.body;var item=el.getAttribute('data-fh-item');if(item&&typeof FH!=='undefined'){FH.open({'shortname':'activeoahutours','view':{'item':item},'fallback':'simple'});return false;}return true;})()"> <strong> <span class="glyphicon glyphicon-calendar"></span> Book Online </strong></a>
```

Replacement regex (handles class-before-href order in actual HTML):
```python
old_cta = re.compile(
    r'<div class="social-links">\s*'
    r'<a class="[^"]*"\s+href="https://fareharbor\.com/embeds/book/activeoahutours/\?u=[^"]*"[^>]*>'
    r'\s*<strong>\s*<span class="glyphicon glyphicon-calendar"></span>\s*Book Online\s*</strong>\s*</a>',
    re.DOTALL
)
```

Run on all pages:
```python
for f in sorted(site_dir.rglob('*.html')):
    if '_templates' in str(f) or 'wp-content' in str(f):
        continue
    html = f.read_text(encoding='utf-8')
    new_html = old_cta.sub(new_cta, html)
    if new_html != html:
        f.write_text(new_html, encoding='utf-8')
```

Also update `site/_templates/body_top.html` (template used during generation).

### Step 4: Verify
```bash
# Tour page should have data-fh-item AND new CTA
grep -o 'data-fh-item="[^"]*"' site/activities/chinamans-hat-oahu-kayak-tours/index.html
grep -c 'header-book-online' site/activities/chinamans-hat-oahu-kayak-tours/index.html

# Guide page should NOT have data-fh-item but SHOULD have new CTA
grep -c 'data-fh-item' site/guides/ocean-kayaking-beginners-oahu/index.html  # 0 or 1 (header comment)

# Count coverage
grep -rl 'header-book-online' site/ --include="*.html" | wc -l  # ~247
grep -rl 'data-fh-item="' site/ --include="*.html" | wc -l     # ~129 (exact pages with FH codes)
```

### How the JavaScript Works
- **Has `data-fh-item`**: Opens specific item via `FH.open({'item':'XXXXX'})` — no catalog friction
- **No `data-fh-item`**: Falls through to normal `href` link — opens full FH catalog
- **FH not loaded**: Normal link works as before (graceful degradation)

### Commit & Push
```bash
git add site/
git commit -m "[Ned] GRO-XXXX: Replace global header CTA with FH item deep-link — onclick reads data-fh-item from body (247 pages)"
git push origin master
```

---

## Combined Batch (Schema + CTA in One Run)

When both task types appear together (GRO-1297 + GRO-1299), execute in order:
1. Run schema injection first (touches all pages, injects JSON-LD)
2. Run CTA deep-linking second (modifies header, adds data attributes)
3. Commit as **two separate commits** for review clarity

The schema injection modifies `<head>` content; the CTA update modifies `<body>` header. No merge conflicts.

---

## Linear Comment Template

For completion posts, use `json.dumps(body)` inside the GraphQL mutation to handle all escaping:

```python
body = """## ✅ Complete — Summary

**Executed by:** Ned (date)

### What Was Done
...

### Commit
`COMMIT_SHA` — message
Pushed to `master` (deploys via Cloudflare Pages)

Status: Keep `agent:fred` — ready for Fred's review."""

query = '''mutation { commentCreate(input: {issueId: "''' + ISSUE_ID + '''", body: ''' + json.dumps(body) + '''}) { success } }'''
result = gql(query)
```

Move to In Progress after comment:
```python
IN_PROGRESS = '734901ee-58f0-457c-b9a0-f911c0da13a4'
m2 = f'mutation {{ issueUpdate(id: "{ISSUE_ID}", input: {{ stateId: "{IN_PROGRESS}" }}) {{ success }} }}'
```
