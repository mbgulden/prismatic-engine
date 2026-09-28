# Batch SEO Fixes for Static Mirrors

Proven patterns for mass-fixing SEO issues across an existing static WordPress mirror (200+ pages). All patterns use `execute_code` for safety — avoids shell escaping issues with ampersands, smart quotes, and em-dashes in HTML. Validated on the 238-page Active Oahu Tours mirror (June 2026).

## Quick Reference: Which Fix, Which Tool

| Fix | Pattern | Key Pitfall |
|-----|---------|-------------|
| Meta descriptions | Dict-based batch: `DESCRIPTIONS[url] = text` | Use `html.escape(text, quote=True)` for HTML attributes |
| OG/Twitter tags | Conditional injection: check what's missing, inject only those | Use site's homepage `og:image` as default |
| Lazy loading | Regex on `<img>` tags: add `loading="lazy"` to tags without `loading=` | Use list wrapper for counter in nested function (no `nonlocal` in execute_code) |
| Preconnect hints | String replace before third-party `<script>` tags | Only inject on pages that actually load the script |
| FareHarbor audit | Check API script presence + shortname consistency | Correct regex: `shortname':'([^']+)'` NOT `shortname: '...'` |
| Image compression | Pillow: resize to 1920px → quality-step JPEG 85→65→55→45 | Second pass at 1600px for stubborn drone shots |

## Meta Description Batch Injection

### Pattern: Dictionary-Based Batch

Two modes in one loop — **replace** existing descriptions or **inject** before `</head>`:

```python
import os, re, html

SITE = "/path/to/mirror/site"
DESCRIPTIONS = {
    "/page-slug/": "Your unique 150-160 char description here.",
    "/ja/page-slug/": "日本語のメタディスクリプション。",
}

for root, dirs, files in os.walk(SITE):
    for f in files:
        if f != "index.html": continue
        fp = os.path.join(root, f)
        rel = os.path.relpath(root, SITE).replace("\\", "/")
        if rel == ".": rel = "/"
        else: rel = "/" + rel + "/"
        
        if rel not in DESCRIPTIONS: continue
        
        with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
            content = fh.read()
        
        new_desc = DESCRIPTIONS[rel]
        escaped = html.escape(new_desc, quote=True)
        
        if '<meta name="description"' in content:
            content = re.sub(
                r'<meta\s+name="description"\s+content="[^"]*"',
                f'<meta name="description" content="{escaped}"',
                content, count=1
            )
        else:
            meta_tag = f'<meta name="description" content="{escaped}" />'
            content = content.replace('</head>', f'\t{meta_tag}\n</head>', 1)
        
        with open(fp, 'w', encoding='utf-8') as fh:
            fh.write(content)
```

### Verification

```python
from collections import Counter

missing = []
descs = []
for root, dirs, files in os.walk(SITE):
    for f in files:
        if f != "index.html": continue
        fp = os.path.join(root, f)
        with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
            content = fh.read()
        m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', content)
        if not m: missing.append(os.path.relpath(root, SITE))
        else: descs.append(m.group(1))

dupes = {d: c for d, c in Counter(descs).items() if c > 1}
print(f"Missing: {len(missing)}, Duplicate groups: {len(dupes)}")
```

## OG/Twitter Tag Batch Injection

Use existing meta description and title — don't duplicate effort:

```python
import os, re, html

DEFAULT_OG_IMAGE = "/wp-content/uploads/2021/06/hero-image.jpg"

for root, dirs, files in os.walk(SITE):
    for f in files:
        if f != "index.html": continue
        fp = os.path.join(root, f)
        
        with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
            content = fh.read()
        
        if all(x in content for x in ['og:title', 'og:description', 'og:image', 'twitter:card']):
            continue
        
        meta_desc = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', content)
        title_tag = re.search(r'<title>([^<]+)</title>', content)
        desc_text = meta_desc.group(1) if meta_desc else ""
        title_text = title_tag.group(1) if title_tag else ""
        
        new_tags = []
        if 'og:title' not in content and title_text:
            new_tags.append(f'<meta property="og:title" content="{html.escape(title_text, quote=True)}" />')
        if 'og:description' not in content and desc_text:
            new_tags.append(f'<meta property="og:description" content="{html.escape(desc_text, quote=True)}" />')
        if 'og:image' not in content:
            new_tags.append(f'<meta property="og:image" content="{DEFAULT_OG_IMAGE}" />')
        if 'twitter:card' not in content:
            new_tags.append('<meta name="twitter:card" content="summary_large_image" />')
            if desc_text:
                new_tags.append(f'<meta name="twitter:description" content="{html.escape(desc_text, quote=True)}" />')
            if title_text:
                new_tags.append(f'<meta name="twitter:title" content="{html.escape(title_text, quote=True)}" />')
            new_tags.append(f'<meta name="twitter:image" content="{DEFAULT_OG_IMAGE}" />')
        
        if new_tags:
            tag_block = '\n'.join('\t' + t for t in new_tags)
            content = content.replace('</head>', tag_block + '\n</head>', 1)
            with open(fp, 'w', encoding='utf-8') as fh:
                fh.write(content)
```

## Lazy Loading Batch Addition

```python
import os, re

for root, dirs, files in os.walk(SITE):
    for f in files:
        if f != "index.html": continue
        fp = os.path.join(root, f)
        
        with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
            content = fh.read()
        
        lazy_count = [0]  # list wrapper — no nonlocal in execute_code
        
        def add_lazy(match):
            tag = match.group(0)
            if 'loading=' not in tag.lower():
                lazy_count[0] += 1
                return tag.replace('<img ', '<img loading="lazy" ', 1)
            return tag
        
        new_content = re.sub(r'<img\s[^>]*>', add_lazy, content, flags=re.IGNORECASE)
        if new_content != content:
            with open(fp, 'w', encoding='utf-8') as fh:
                fh.write(new_content)
```

## Preconnect/DNS-Prefetch

```python
PRECONNECT_TAG = '<link rel="preconnect" href="https://fareharbor.com" crossorigin />\n<link rel="dns-prefetch" href="https://fareharbor.com" />'

if 'fareharbor.com/embeds/api/v1/' in content and 'preconnect' not in content:
    content = content.replace(
        '<script src="https://fareharbor.com/embeds/api/v1/',
        f'{PRECONNECT_TAG}\n<script src="https://fareharbor.com/embeds/api/v1/',
        1
    )
```

## FareHarbor Embed Verification

### Correct Detection Patterns

FareHarbor uses two key markers — getting either regex wrong produces false negatives:

| What | Correct Pattern | Wrong Pattern |
|------|----------------|---------------|
| API script presence | `fareharbor.com/embeds/api/v1/` | N/A — simple substring match |
| Shortname in booking buttons | `shortname':'([^']+)'` | `shortname:\s*'([^']+)'` |
| Booking button presence | `FH.open` | N/A — simple substring match |

### Audit Script

```python
# Check all tour/rental pages
is_tour_rental = any(x in rel for x in [
    '/activities/', '/rentals/', '/tours/', '/oahu-equipment-rentals/',
    '/multi-day-', '/electric-bike-', '/guided-tours/', '/kayak-rentals/'
])

if is_tour_rental:
    has_api = 'fareharbor.com/embeds/api/v1/' in content
    shortnames = set(re.findall(r"shortname':'([^']+)'", content))
    bad_shortnames = shortnames - {'activeoahutours'}
    
    if not has_api or bad_shortnames:
        print(f"ISSUE: {rel}")
```

## Image Compression (Pillow)

See `references/image-compression-pillow.md` for the full script. Quick reference:

- **First pass**: Max 1920px width, quality stepping 85→45, target <500KB
- **Second pass** (stubborn drone shots): Max 1600px width, quality stepping 65→35
- **Always**: `img.save(fp_tmp, 'JPEG', quality=q, optimize=True)` then `os.replace(fp_tmp, fp)`

## Commit Pattern

One clean commit per batch with Linear references:

```bash
git add site/ && git commit -m "feat: SEO sprint — [summary]

GRO-XXX: [specific fix]
GRO-YYY: [specific fix]" && git push origin main
```

## Pitfalls

- **Always `git log --oneline -5` before starting** — other sessions may have already applied fixes
- **`html.escape(text, quote=True)`** for all user-written text going into HTML attributes
- **`count=1` on regex replacements** — head templates contain duplicate patterns (og:desc in both OG block and JSON-LD)
- **`nonlocal` fails in `execute_code`** — use list wrapper: `counter = [0]; counter[0] += 1`
- **FareHarbor shortname regex is `shortname':'([^']+)'`** — colon-quote, not colon-space
- **Some pages legitimately lack booking** — informational guides, partner pages, redirects are correct without FH API
- **Verify after injecting**: always re-scan for missing/duplicates after batch operations
