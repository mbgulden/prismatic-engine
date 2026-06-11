---
name: static-site-seo-fix
description: Audit and fix SEO issues on static HTML sites — meta descriptions, canonical tags, OG tags. Covers safe HTML attribute extraction, WP/WooCommerce artifact detection, and batch-fix workflows. Trigger when asked to fix missing/broken meta tags, canonical URLs, or OG tags on static HTML exports.
---

# Static Site SEO Fix

Audit and repair meta descriptions, canonical tags, and OG tags across static HTML site exports. Works with WordPress static exports, SSG output, and hand-built static sites.

## Triggers

- "Fix missing meta descriptions on [site]"
- "Add canonical tags to [N] pages"
- "Audit SEO tags on static HTML site"
- AGY-created Linear issues referencing `agent:fred` + meta/canonical/SEO

## Scanning Pattern

Use `find` to collect all `.html` files, exclude templates and 404 pages:

```bash
find <site_dir> -name "*.html" \
  -not -name "404.html" \
  -not -path "*/_templates/*" \
  -not -path "*/template-parts/*"
```

## Safe HTML Attribute Extraction

### THE PITFALL — Do NOT use `[^"\']` in regex character classes

When extracting attribute values from HTML, match ONLY the delimiter character. Using `[^"\']*` (stop at either quote) breaks on content containing apostrophes:

```python
# WRONG — breaks on Oahu's, Chinaman's Hat, etc.
re.search(r'content="([^"\']*)"', tag)  # Matches "Oahu" only, leaves "'s best..."

# CORRECT — only stop at the actual delimiter
re.search(r'content="([^"]*)"', tag)    # Matches full "Oahu's best kayaking..."
```

### Preferred patterns (order-agnostic where needed)

```python
# Description: content before name
re.search(r'<meta\s+content="([^"]*)"\s+name="description"', content)

# Description: name before content  
re.search(r'<meta\s+name="description"\s+content="([^"]*)"', content)

# Canonical: href before rel (common in WordPress exports)
re.search(r'<link\s[^>]*rel="canonical"', content)  # order-agnostic, just check presence
```

## WordPress/WooCommerce Static Export Artifacts

WP static exports (Simply Static, WP2Static, etc.) can produce corrupted `<meta>` tags with injected attributes:

```html
<!-- BROKEN — WooCommerce/plugin attributes mangled into meta tag -->
<meta add="" at="" checkout." code"="" content="カイルアのショップからビーチまで..." discount="" name="description" or="" promo=""/>
```

### Detection regex

```python
re.search(r'<meta\s+add=""[^>]*name="description"[^>]*>', content)
```

### Fix pattern

Extract the content value, discard the broken tag, rewrite a clean one:

```python
broken_match = re.search(r'<meta\s+add=""[^>]*name="description"[^>]*>', content, re.IGNORECASE)
if broken_match:
    desc = re.search(r'content="([^"]*)"', broken_match.group(0)).group(1)
    clean_tag = f'<meta content="{desc}" name="description"/>'
    content = content.replace(broken_match.group(0), clean_tag)
```

## Batch Fix Workflow

1. **Scan first** — collect all files, check each for missing/broken tags
2. **Categorize issues** — missing vs. broken vs. too-short vs. too-long
3. **Fix in passes** — one pass per issue type, verify after each pass
4. **Verify with the CORRECT regex** — the verification scan must use the same safe pattern as the fix
5. **Commit to `deploy-fresh`** (or whatever staging branch is active)

### Description quality target
- **120-160 characters** (the SEO sweet spot)
- Acceptable range: 50-200 chars
- Derive from page `<title>` when the existing description is broken/truncated
- Include key terms from the page's topic and location

## AGY-Issue Scope Pattern

AGY-generated Linear issues often say "2 pages" in the title but "All pages" in the deliverables. The **deliverables** section is the authoritative scope. Always scan the full site and fix everything that's broken, not just the N pages mentioned in the title.

## JSON-LD Schema Injection

When injecting structured data into static HTML pages, prefer adding before `</head>` rather than replacing existing blocks. This avoids merge conflicts and works on branches that haven't had schema injected yet.

### Injection pattern (head)
```python
if '</head>' in content and '<script type="application/ld+json">' not in content:
    content = content.replace('</head>', schema_json + '\n</head>', 1)
```

### Schema types per page type
| Page type | Schema `@type` | Key fields |
|---|---|---|
| Tour/activity | `TouristTrip` | name, description, tourOperator, touristType, offers |
| Equipment rental | `Product` | name, description, brand, offers |
| Homepage | `TravelAgency` | name, description, address, openingHours, sameAs |
| FAQ | `FAQPage` | mainEntity (Question/Answer pairs) |
| How-to guide | `HowTo` | step (HowToStep with name + text), totalTime |
| Blog/article | `Article` | headline, author, datePublished |

### HowTo schema construction

Extract the H1/H2 structure from the page to build realistic HowTo steps. Each step gets a `position`, `name`, and `text`. The `totalTime` uses ISO 8601 duration format (PT4H, PT30M, etc.).

```python
def build_howto_schema(name, description, steps, total_time, url):
    """steps = list of {name, text} dicts"""
    schema = {
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": name,
        "description": description,
        "step": [{"@type": "HowToStep", "position": i+1, "name": s["name"], "text": s["text"]}
                 for i, s in enumerate(steps)],
        "totalTime": total_time,
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)
```

### FAQPage schema construction

Use `build_faq_schema(questions)` where `questions` is a list of `{q, a}` dicts. **Extraction:** For WordPress-style FAQ pages, use the `<p><strong>Q?</strong> — A</p>` pattern with false-positive filters — see `references/wp-faq-extraction-patterns.md` for the full extraction recipe and anti-patterns.

```python
def build_faq_schema(questions):
    """questions = list of {q, a} dicts"""
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": qa["q"],
                         "acceptedAnswer": {"@type": "Answer", "text": qa["a"]}}
                        for qa in questions],
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)
```

## AEO Quick Answer Blocks (Google AI Overviews)

For AEO (Answer Engine Optimization) targeting Google AI Overviews, add TWO things to each page:

1. **Visible Quick Answer block** — a styled HTML div injected after `<h1>`. 50–75 words answering the page's primary question. Blue left-border, light blue background, clearly labeled "Quick Answer."

2. **FAQPage schema** — a single-Question JSON-LD block with the same Q&A, injected before `</head>`.

### AEO block HTML pattern
```html
<!-- AEO Quick Answer Block -->
<div class="aeo-quick-answer" style="background:#f0f7fb; border-left:4px solid #006699; padding:16px 20px; margin:20px 0; border-radius:4px;">
  <h2 style="margin:0 0 8px 0; font-size:1.1em; color:#006699;">Quick Answer</h2>
  <p style="margin:0; font-size:0.95em; line-height:1.5;">[ANSWER TEXT — 50-75 words]</p>
</div>
```

### AEO injection logic
```python
# Inject FAQPage schema before </head>
content = content.replace('</head>', schema_block + '\n</head>', 1)

# Inject AEO HTML block after <h1>
import re
match = re.search(r'<h1[^>]*>.*?</h1>', content)
if match:
    insert_pos = match.end()
    content = content[:insert_pos] + '\n' + aeo_html + '\n' + content[insert_pos:]

# Skip AEO block if no <h1> exists (redirect pages, thin pages) — inject schema only
```

### Multi-phase schema campaign

For a comprehensive AI Overviews push, run three phases:

| Phase | Schema type | Injection location | Priority pages |
|---|---|---|---|
| 1. HowTo | JSON-LD before `</head>` | Instructional/guide pages with clear steps | Ocean kayaking guide, safety guide, snorkeling guide |
| 2. FAQPage | JSON-LD before `</head>` | Pages with existing FAQ sections | FAQ pages, rental pages with Q&As |
| 3. AEO Blocks | Visible HTML after `<h1>` + FAQPage JSON-LD | Top-20 traffic/revenue pages | Homepage, tour pages, guide pages |

**Skip check:** before injecting, check if the schema type already exists:
```python
if '"@type":"HowTo"' in content or '"@type": "HowTo"' in content:
    continue  # already has it
if "aeo-quick-answer" in content:
    continue  # already has AEO block
```

### Machine-translation cleanup checklist
For non-English pages generated by machine translation:
1. **Geographic names** — never translate location names literally (Three Tables ≠ テーブル3つ; use スリー・テーブルズ)
2. **Keyword stuffing** — remove geo-spam from titles/names (PCC, Laie, Hauula comma chains)
3. **Duplication** — machine translators often repeat terms (ドライバッグ、ドライバッグ)
4. **touristType** — translate audience categories naturally (Families→ファミリー, not left in English)
5. **Tone** — use です/ます調 (polite form) for Japanese; avoid dry literal translations

## Reference Files

- `references/safe-attribute-extraction.md` — Detailed regex patterns with wrong/right examples
- `references/wp-faq-extraction-patterns.md` — Extracting Q&A pairs from WordPress-style FAQ pages for FAQPage schema. Correct `<p><strong>Q?</strong> — A</p>` pattern, false-positive filters (banners, nav, buttons), and replacement strategy for weak prior extractions
- `references/wp-static-export-artifacts.md` — WP/WooCommerce corrupted meta tag patterns and fixes
- `references/static-mirror-link-audit.md` — Systematic broken-link audit + fix workflow for static mirrors
- `references/japanese-seo-schema-localization.md` — Natural Japanese schema templates, machine-translation pitfall examples, and localization patterns
- `references/japanese-content-depth-comparison.md` — Correct methodology for comparing JA↔EN page content depth (character count, not word count), 404-stub detection, and gap analysis workflow
- `references/batch-css-injection.md` — Apply the same CSS rule across hundreds of static HTML pages using anchor-string injection
- `references/aeo-howto-faq-schema-injection.md` — Three-phase HowTo + FAQPage + AEO Quick Answer block injection for Google AI Overviews targeting

## Common Pitfalls

1. **`[^"\']` in regex** — see `references/safe-attribute-extraction.md`. This is the #1 bug.
2. **Canonical tag ordering** — don't assume `rel=` comes before `href=`. Use order-agnostic patterns.
3. **Japanese/UTF-8 pages** — these often have different meta tag formatting. Always check non-English pages separately.
4. **Replacing only part of a tag** — when a description contains the same text as its own substring, naive `str.replace()` can corrupt it. Use regex to match the FULL tag, then replace the entire tag.
5. **Multiple `<meta>` tags with "description"** — OG and Twitter cards also have description properties. Only match `name="description"`, not `property="og:description"` or `name="twitter:description"`.
6. **Cherry-picking across diverged branches** — when `main` and `master` (or any two long-lived branches) have different file content, `git cherry-pick` will produce cascading conflicts. Re-apply fixes fresh against the target branch instead. Use `git cherry-pick --abort` to clean up, then run the fix script directly on the target branch's files.
7. **Japanese word-count is misleading for content depth** — Japanese text doesn't use spaces between words, so `text.split()` gives dramatically lower counts than English. A JA page that appears to be "12% of EN depth" by word count may be 80% by character count — which is normal for Japanese (20-40% more compact than equivalent English). When comparing JA↔EN page depth, use **character count** (stripped of HTML/whitespace), not word count. See `references/japanese-content-depth-comparison.md` for the full methodology.
8. **WordPress 404 stubs in non-English static mirrors** — WP static exports may capture 404 error templates (`<body class="error404">`) for non-English pages that were never translated. These stubs have identical chrome (header/footer/sidebar) but zero real body content and ~160-200 chars of template text. Detect with `'error404' in html` and treat as needing full recreation from the EN template, not content expansion. The fix pattern: clone the EN page, replace lang/title/meta/OG/hreflang, add a translation-needed notice, save with proper JA directory structure.
