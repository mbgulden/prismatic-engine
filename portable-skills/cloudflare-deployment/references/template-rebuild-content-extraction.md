# Template Rebuild with Content Extraction

## When to Use
When replacing a section (nav, header, footer) of a static mirror built from templates, and you need to rebuild all pages without losing their unique content.

## The Pattern

The mirror uses 3 templates:
- `head.html` — doctype through `</head>`
- `body_top.html` — `<body>` through the opening of the content div
- `body_bottom.html` — closing of content div through `</html>`

Each rendered page is: `head.html + body_top.html + [page-specific content] + body_bottom.html`

## Rebuilding After Template Changes

When you modify `body_top.html` (to replace the nav) or `head.html` (to add nav CSS/JS), rebuild pages by extracting the content section and sandwiching between the new templates:

```python
import os, re

with open('site/_templates/head.html') as f: head = f.read()
with open('site/_templates/body_top.html') as f: body_top = f.read()
with open('site/_templates/body_bottom.html') as f: body_bottom = f.read()

for root, dirs, files in os.walk('site'):
    dirs[:] = [d for d in dirs if d != '_templates']
    for fname in files:
        if not fname.endswith('.html'): continue
        filepath = os.path.join(root, fname)
        with open(filepath) as f: html = f.read()
        cs = html.find('<div id="content" class="site-content">')
        ce = html.find('</div><!-- #content -->')
        if cs >= 0 and ce >= 0:
            content = html[cs:ce + len('</div><!-- #content -->')]
            new_page = head + '\n' + body_top + '\n' + content + '\n' + body_bottom
            with open(filepath, 'w') as f: f.write(new_page)
```

## Pitfalls

- **Content markers vary across pages.** Some pages (Japanese translations, animation templates, guide pages) may not have the standard `<div id="content">` / `</div><!-- #content -->` markers. Check the error count against total page count.
- **Japanese pages (ja/) use different templates.** They may reference different head/body files. After a nav rebuild, verify Japanese pages haven't broken — they often need separate treatment.
- **Template changes don't auto-propagate.** The `site/` directory contains pre-rendered HTML. Editing templates alone doesn't update existing pages — you MUST run the rebuild script.
- **Always rebuild before deploying to staging.** Committing template changes without rebuilding means the deploy has stale nav HTML with new CSS — visual chaos.

## AOT Mirror Example (June 2026)

GRO-712 nav component v6 rebuild:
- 162 English pages rebuilt successfully
- 89 errors (Japanese pages, animation templates, edge cases) — acceptable for staging QA
- Staging deployed, main reverted to keep production clean
- Post-deploy: AGY QA on staging URL to verify integration didn't break the component
