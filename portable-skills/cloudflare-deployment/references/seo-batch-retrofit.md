# SEO Batch Retrofit for Static Mirrors

Inject meta descriptions, OG/Twitter tags, lazy loading, and preconnect hints
across hundreds of existing HTML pages. Proven on 238-page Active Oahu mirror.

## Meta Description Audit + Injection

### Scan Phase
```python
import os, re
from collections import Counter

SITE = "~/work/project/site"
pages = []
for root, dirs, files in os.walk(SITE):
    for f in files:
        if f != "index.html": continue
        fp = os.path.join(root, f)
        rel = os.path.relpath(root, SITE)
        if rel == ".": rel = "/"
        else: rel = "/" + rel + "/"
        with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
            content = fh.read()
        m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', content)
        desc = m.group(1) if m else "MISSING"
        pages.append({"url": rel, "desc": desc})

missing = [p for p in pages if p["desc"] == "MISSING"]
desc_counts = Counter(p["desc"] for p in pages if p["desc"] != "MISSING")
duplicates = {d: c for d, c in desc_counts.items() if c > 1}
```

### Generation Strategy
- **English pages**: Write unique 150-160 char descriptions based on page title + purpose
- **Japanese pages**: Use the Japanese `<title>` text as description base (titles are already SEO-optimized)
- **Pagination/duplicates**: Differentiate with "Page 2", "Page 3" etc. plus category context
- **Use a dict**: `{"/url/": "description text", ...}` then iterate to inject

### Injection (Two Cases)
```python
import html
escaped = html.escape(new_desc, quote=True)

if '<meta name="description"' in content:
    # Replace existing
    content = re.sub(
        r'<meta\s+name="description"\s+content="[^"]*"',
        f'<meta name="description" content="{escaped}"',
        content, count=1
    )
else:
    # Insert before </head>
    meta_tag = f'<meta name="description" content="{escaped}" />'
    content = content.replace('</head>', f'\t{meta_tag}\n</head>', 1)
```

## OG / Twitter Card Injection

For pages with meta descriptions already set, copy them into OG tags.
Use a site-default image (`og:image`) for pages without specific photos.

```python
DEFAULT_OG_IMAGE = "/wp-content/uploads/2021/06/site-hero.jpg"

# Check what's missing
has_og_title = 'property="og:title"' in content
has_og_desc = 'property="og:description"' in content
has_og_image = 'property="og:image"' in content
has_twitter = 'twitter:card' in content

# Build missing tags
new_tags = []
meta_desc = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', content)
title_tag = re.search(r'<title>([^<]+)</title>', content)

if not has_og_title and title_tag:
    new_tags.append(f'<meta property="og:title" content="{html.escape(title_tag.group(1), quote=True)}" />')
if not has_og_desc and meta_desc:
    new_tags.append(f'<meta property="og:description" content="{html.escape(meta_desc.group(1), quote=True)}" />')
if not has_og_image:
    new_tags.append(f'<meta property="og:image" content="{DEFAULT_OG_IMAGE}" />')
if not has_twitter:
    new_tags.append('<meta name="twitter:card" content="summary_large_image" />')
    new_tags.append(f'<meta name="twitter:title" content="{title_esc}" />')
    new_tags.append(f'<meta name="twitter:description" content="{desc_esc}" />')
    new_tags.append(f'<meta name="twitter:image" content="{DEFAULT_OG_IMAGE}" />')

# Inject before </head>
tag_block = '\n'.join('\t' + t for t in new_tags)
content = content.replace('</head>', tag_block + '\n</head>', 1)
```

## Lazy Loading Injection

Add `loading="lazy"` to all `<img>` tags that don't already have a `loading` attribute.
Regex-based, safe for batch application across all pages.

```python
def add_lazy(match):
    tag = match.group(0)
    if 'loading=' not in tag.lower():
        return tag.replace('<img ', '<img loading="lazy" ', 1)
    return tag

content = re.sub(r'<img\s[^>]*>', add_lazy, content, flags=re.IGNORECASE)
```

**Pitfall**: `nonlocal` doesn't work in nested functions inside `execute_code`.
Use a list wrapper for mutable counters: `count = [0]` then `count[0] += 1`.

## Preconnect / DNS-Prefetch Injection

For third-party scripts that are render-blocking (FareHarbor, analytics, fonts),
inject preconnect + dns-prefetch hints before the script tag:

```python
PRECONNECT = '<link rel="preconnect" href="https://fareharbor.com" crossorigin />\n<link rel="dns-prefetch" href="https://fareharbor.com" />'

if 'fareharbor.com/embeds/api/v1/' in content and 'preconnect' not in content:
    content = content.replace(
        '<script src="https://fareharbor.com/embeds/api/v1/',
        f'{PRECONNECT}\n<script src="https://fareharbor.com/embeds/api/v1/',
        1  # count=1 — only the first occurrence
    )
```

## FareHarbor Verification

When auditing FareHarbor embeds:
- **Shortname format**: `FH.open({'shortname':'activeoahutours',...})` in onclick handlers — NOT `shortname:\s*'([^']+)'`
- **API script**: `<script src="https://fareharbor.com/embeds/api/v1/?autolightframe=yes"></script>` — should be on all tour/rental pages
- **Consistency check**: Extract all shortname values across the site and verify they match
- **Pages without FH**: Informational guides, partner pages, and redirect pages legitimately lack the embed — don't force-add

```python
# Correct shortname extraction:
shortnames = set(re.findall(r"shortname':'([^']+)'", content))
```

## Full Workflow Order

For an SEO retrofitting sprint:
1. **Meta descriptions** first — they feed into OG tags
2. **OG/Twitter tags** second — copy meta descriptions
3. **Lazy loading** third — mechanical regex, no dependencies
4. **Preconnect** fourth — only pages with third-party scripts
5. **Image compression** separate (Pillow, see `image-compression-pillow.md`)
6. **Verify zero missing**: re-scan for `<meta name="description"` and `og:image`
7. **Single commit**: one commit for all SEO work with GRO-issue references
