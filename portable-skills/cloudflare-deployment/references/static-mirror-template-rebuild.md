# Static Mirror Template Rebuild Pattern

## When to Use
After modifying `head.html` or `body_top.html` templates in a WordPress static mirror (wget → CF Pages), you must rebuild ALL HTML pages so they pick up the template changes. This is the fast rebuild pattern that preserves each page's unique content.

## The Content Sandwich
Each page in the mirror has this structure:
```
<head>...</head>          ← from head.html template (shared)
<body>                     ← from body_top.html template (shared, includes nav/branding/breadcrumbs)
<div id="content" ...>    ← UNIQUE per page (the actual page content)
  ...
</div><!-- #content -->
<footer>...</footer>       ← from body_bottom.html template (shared)
</body>
```

## Rebuild Script

```python
import os

with open('site/_templates/head.html') as f: head = f.read()
with open('site/_templates/body_top.html') as f: body_top = f.read()
with open('site/_templates/body_bottom.html') as f: body_bottom = f.read()

count = 0
for root, dirs, files in os.walk('site'):
    dirs[:] = [d for d in dirs if d != '_templates']  # skip template dir
    for fname in files:
        if not fname.endswith('.html'):
            continue
        filepath = os.path.join(root, fname)
        try:
            with open(filepath) as f:
                html = f.read()
            # Find the content section boundaries
            cs = html.find('<div id="content" class="site-content">')
            ce = html.find('</div><!-- #content -->')
            if cs >= 0 and ce >= 0:
                content = html[cs:ce + len('</div><!-- #content -->')]
                # Sandwich: head + body_top + content + body_bottom
                new_page = head + '\n' + body_top + '\n' + content + '\n' + body_bottom
                with open(filepath, 'w') as f:
                    f.write(new_page)
                count += 1
        except Exception:
            pass

print(f'Rebuilt {count} pages')
```

## Key Points
- Only pages with both markers (`<div id="content" class="site-content">` and `</div><!-- #content -->`) are rebuilt
- Japanese pages (`site/ja/`) and template partials (e.g., kayak animation) have different structures and won't match
- After rebuild, commit and push to the staging branch: `git push origin main:staging --force`
- Always `git reset --hard HEAD~1` after pushing to keep main clean for production
- The staging branch auto-deploys to CF Pages preview URL

## Pitfalls
- **Main branch contamination:** After committing template changes, main now has the new nav. Always force-push to staging first, then revert main: `git push origin main:staging --force && git reset --hard HEAD~1`
- **Non-fast-forward push:** Staging may reject a regular push if main was reverted since last staging push. Use `--force`.
- **Old Kadence CSS conflicts:** When adding new nav CSS to `head.html`, old WordPress theme CSS (Kadence blocks, style.css) may override your rules. Use `!important` on critical properties (color, background, display) for mobile sub-menus.
- **Japanese pages ignored:** Japanese translations in `site/ja/` have a different content wrapper structure and are not rebuilt by this pattern. They need separate treatment.
