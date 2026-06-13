# Programmatic HD SEO Page Generation

Pattern for generating Human Design reference pages at scale. Used to create gate, channel, center, and type pages — each self-contained HTML with full SEO metadata.

## Page Structure Template

Every SEO page follows this skeleton:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>[Topic] — [Subtitle] | Human Design Engine</title>
  <meta name="description" content="[160-char description with keywords]">
  <meta name="keywords" content="[comma-separated keywords]">
  <link rel="canonical" href="https://humandesignengine.com/human-design/[category]/[slug]/">
  
  <!-- Open Graph -->
  <meta property="og:title" content="...">
  <meta property="og:description" content="...">
  <meta property="og:type" content="website|article">
  <meta property="og:url" content="https://humandesignengine.com/...">
  
  <!-- Twitter -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="...">
  <meta name="twitter:description" content="...">
  
  <!-- Structured Data -->
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Article|CollectionPage",
    "headline": "...",
    "description": "...",
    "publisher": { "@type": "Organization", "name": "Human Design Engine", "url": "https://humandesignengine.com" }
  }
  </script>
  
  <style>/* Navy/gold design system inline */</style>
</head>
<body>
  <!-- Navigation breadcrumb -->
  <!-- Hero section with h1 -->
  <!-- Content sections -->
  <!-- CTA: "Get your full report → /buy-report.html" -->
</body>
</html>
```

## Design System (Navy/Gold)

```css
:root {
  --navy-deep: #060d1a;
  --navy: #0a1628;
  --navy-mid: #0f1d36;
  --gold: #c9a84c;
  --gold-light: #e0c468;
  --text-primary: #e8e6e3;
  --text-secondary: #8899aa;
  --card-bg: rgba(15, 29, 54, 0.7);
  --radius: 12px;
}
```

## Content Guidelines

- **Target 400-600 lines per page** — substantive enough for SEO, not bloated
- **Include practical content**: experiments, real-world examples, how-to's
- **Cross-link**: every page links to related centers/channels/gates/types
- **CTA on every page**: "Get your full personalized report →" linking to `/buy-report.html`
- **Breadcrumb nav**: Home → Category → Page

## Generator Script Pattern

Use a Python generator script for batch creation:

```python
#!/usr/bin/env python3
"""Generate N SEO pages from a data dictionary."""
import os

DATA = {
    "slug": {
        "title": "...",
        "description": "...",
        "function": "...",
        "related": [...],
        # ... page-specific fields
    },
}

for slug, info in DATA.items():
    page = f"""..."""  # HTML template with f-string interpolation
    with open(f"{slug}.html", "w") as f:
        f.write(page)
```

Generator scripts at:
- `/home/ubuntu/work/hd-platform/docs/human-design/centers/generate.py`
- `/home/ubuntu/work/hd-platform/docs/human-design/types/generate.py`

## Existing Page Inventories

| Category | Pages | Location |
|---|---|---|
| Gates | 64 | `docs/human-design/gates/` |
| Channels | 36 | `docs/human-design/channels/` |
| Centers | 9 + index | `docs/human-design/centers/` |
| Types | 5 + index | `docs/human-design/types/` |

## Lessons Learned

- Jules (autonomous agent) successfully generated the 9 center pages with full schema.org markup and brand-consistent design
- Each page should be self-contained (inline CSS) — no external stylesheet dependencies in SEO pages
- The generator script doubles as documentation — anyone can see how each page was built
- Schema.org `Article` type for individual pages, `CollectionPage` for index pages
