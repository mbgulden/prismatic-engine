# Static Mirror Link Cleanup — Fine-Toothed Comb Method

## When to Use
After creating a WordPress static mirror (wget → CF Pages), broken internal links are inevitable. This reference covers the surgical approach to fixing them, as dictated by Michael's correction: **"Do a fine toothed comb on the link cleanup."**

## The Correction
Batch regex operations (e.g., `re.sub` across 240 pages in one pass) introduce more problems than they solve. Michael explicitly rejected this approach. The right method is category-by-category, with verification between each step.

## Method

### Step 1: Categorize All Broken Links
Before touching any files, run a categorization pass. Group every broken link by pattern:

```python
import os, re
from collections import Counter

patterns = Counter()
for root, dirs, files in os.walk(SITE):
    for fname in files:
        if not fname.endswith('.html'): continue
        path = os.path.join(root, fname)
        with open(path, 'r') as f: content = f.read()
        for href in re.findall(r'href="([^"]*)"', content):
            # Skip external, anchors, mailto, tel
            if href.startswith(('http', '#', 'mailto:', 'tel:')): continue
            # Resolve and check existence
            ...
            if not exists:
                # Categorize
                if '/ja/' in href: cat = 'ja-pages'
                elif '/reviews/' in href: cat = 'review-pages'
                elif '/wp-' in href: cat = 'wp-artifacts'
                elif '.html' in href and href.endswith('/'): cat = 'trailing-html'
                elif '#' in href: cat = 'anchor-encoding'
                else: cat = 'other-missing'
                patterns[cat] += 1
```

Output a frequency table. This tells you WHERE to focus.

### Step 2: Fix One Category at a Time
Process categories from highest count to lowest. For each:

1. **Understand the pattern** — sample 5-10 instances. What's the root cause?
2. **Write a targeted fix** — narrow regex or string replacement, scoped to that one pattern
3. **Apply to all pages** — single category only
4. **Verify** — re-run the categorization. That category should drop to near-zero.
5. **Commit** — one commit per category so failures are bisectable

### Step 3: Know What's a False Positive
Anchor links (`href="/page#fragment"`) are **false positives** in filesystem link checking. Browsers resolve `%20` → space and `&amp;` → `&` at runtime. The checker can't verify fragments because they're client-side. If the target page exists and the anchor ID matches after browser decoding, the link is fine.

Don't spend time "fixing" anchor links unless the target page genuinely has a different ID.

### Step 4: Redirects Cover the Rest
For genuinely missing pages that are linked from navigation or high-traffic areas, add Cloudflare Pages `_redirects` entries:
```
/old-wp-slug/ /new-target/ 301
```
This is cheaper than creating stub pages for every missing WordPress URL.

## Common Static Mirror Link Patterns

| Pattern | Cause | Fix |
|---|---|---|
| `/activities/post-name/.html` | WP permalink with trailing `.html` on directory | Strip `.html`: `/activities/post-name/` |
| `/wp-content/plugins/gravityforms/*.js` | GF scripts not downloaded in mirror | Remove `<script>` tags referencing them |
| `/wp-json/` | WordPress REST API link in head | Remove the `<link>` tag |
| `/ja/author/mbgulden/` | Japanese author archive page, no mirror equivalent | Remove links or add redirect |
| `/reviews/individual-post/` | Individual WP review posts, only listing page exists | Add `_redirects` entry → `/reviews/` |
| `/page/2/`, `/page/3/` | WP pagination, mirror may only have limited pages | Redirect to main listing page |
| `#Anchor-With-Spaces` | WP anchor with spaces → hyphens mismatch | Match the target page's actual `id=` attribute |

## Pitfalls

- ❌ **Batch regex across all pages in one pass.** This is what Michael corrected. One pattern, one pass, one verification.
- ❌ **Fixing anchor links without checking the target page's actual IDs.** The checker sees `#my-anchor` as broken because no file `/page#my-anchor` exists. But browsers resolve fragments client-side. Check the target page for `id="my-anchor"` before touching anything.
- ❌ **Over-fixing.** 100% clean links on a 200+ page static mirror is not the goal. The goal is: core navigation works, no 404s on key user paths (tours, rentals, contact, booking).
- ✅ **72 redirects covering WP taxonomy pages is better than creating 72 stub HTML files.**
