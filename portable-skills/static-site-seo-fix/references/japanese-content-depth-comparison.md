# Japanese Content Depth Comparison Methodology

Correct approach for comparing Japanese (JA) page content depth against English (EN) equivalents in static WordPress mirrors.

## The Core Problem

Japanese text does not use spaces between words. Splitting on whitespace (`text.split()`) produces dramatically lower "word counts" for Japanese — typically 8-12× lower than the equivalent English text, even when both pages contain the same translated content.

**Example from GRO-1207 (AOT Mirror, Jun 2026):**
- `east-oahu-self-guided-kayaking-experience` — JA word count: 182, EN word count: 1,696 → **10.7% ratio** by words. Misleading: both pages had identical structure (34 vs 35 `<p>` tags, 20 headings each).
- Same page by character count: JA 22,455 chars, EN 28,077 chars → **80% ratio**. This is normal Japanese compression (Japanese is naturally 20-40% shorter in character length than equivalent English).

## Correct Methodology: Character Count

### Extraction function
```python
import re

def extract_content_chars(html):
    """Extract character count from site-content div (WordPress static export)."""
    match = re.search(
        r'<div class="site-content" id="content">(.*?)</div>\s*<!-- #content -->',
        html, re.DOTALL
    )
    if not match:
        return 0
    
    text = match.group(1)
    # Strip non-content elements
    for tag in ['script', 'style', 'iframe', 'form', 'noscript']:
        text = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<aside[^>]*>.*?</aside>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove all HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove entities
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'&#\d+;', ' ', text)
    # Remove ALL whitespace for char count
    text = re.sub(r'\s+', '', text)
    return len(text)
```

### Ratio interpretation
| Char ratio | Meaning | Action |
|---|---|---|
| <50% | Genuinely thin — missing content or 404 stub | Full recreation from EN template |
| 50-80% | Normal Japanese compression | Accept as-is |
| ≥80% | Fully equivalent depth | Accept as-is |

## 404 Stub Detection

WordPress static exports may capture 404 error templates for non-English pages that were never translated by Weglot (or other WP translation plugins).

### Detection
```python
def is_404_stub(html):
    return 'class="error404"' in html or 'error404' in html
```

### Characteristics of 404 stubs
- `<body class="error404 wp-theme-...">` in the body tag
- ~160-200 chars of content text (all from shared chrome: header, footer, sidebar)
- No real page body content
- Often have bare-minimum `<title>` and no proper meta description

### Fix pattern
Do NOT try to "expand" a 404 stub — it has no content scaffold. Instead:
1. Clone the EN equivalent page
2. Replace: `lang="en-US"` → `lang="ja"`, title, meta description, OG tags, twitter tags, hreflang alternates, canonical URL
3. Add a translation-needed notice banner at the top of `.entry-content`
4. Save to the JA directory path
5. The FareHarbor booking widgets and other functional elements remain intact

## Gap Analysis Workflow

1. **Collect all JA and EN pages** (skip `_templates/`, `_includes/`, `wp-content/`, `404.html`)
2. **Map JA→EN** by stripping the `ja/` prefix and finding the matching file
3. **Extract character counts** from `site-content` div for each pair
4. **Classify** into thin (<50%), moderate (50-80%), healthy (≥80%)
5. **Check for 404 class** on thin pages — treat differently from content-light pages
6. **Prioritize** by page type: activities/tours > rentals > guides > reviews > admin pages
7. **Save gap report** as JSON with per-page data for the next agent

## Proven results (GRO-1207, Jun 2026)
- 85 JA pages analyzed
- By word count: 68 "thin" (false alarm)
- By char count: **6 truly thin**, including 2 WordPress 404 stubs
- **71 of 85 pages at 50-80% char ratio** — normal, correctly translated
- The 2 404 stubs (`sharks-cove-snorkeling`, `kayak-kailua`) were recreated from EN templates
