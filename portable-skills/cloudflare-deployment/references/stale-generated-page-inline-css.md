# Stale Inline CSS in Generated Pages

## The Pattern

Template files (`_templates/head.html`) are edited to fix CSS issues. The templates are clean. But the **generated HTML pages** (e.g., `site/index.html`, `site/activities.html`) still contain **stale inline `<style>` blocks** from a previous build. These stale blocks load AFTER external stylesheets (`nav-fix.css`) and override them by cascade priority.

## How It Happens

1. An earlier build injected mobile nav CSS directly into `head.html` as an inline `<style>` block (between `<!-- fp:...:_templates/head.html -->` and `</style>`)
2. The template was later cleaned — the CSS was removed or trimmed from `head.html`
3. But the **generated HTML pages were never rebuilt** — they retain the old inline `<style>` block
4. New external CSS files (`nav-fix.css`) are added to the `<head>` to fix the nav
5. The stale inline block loads AFTER the external file and overrides it

## Detection — Live Page vs Template Diff

**Rule: Always `curl | grep` the live page BEFORE editing local templates.**

```bash
# Check what's actually on the live page
curl -sL 'https://example.com/?nocache=99999' | grep 'menu-toggle::after'

# Check if the same rule exists in the local template
grep -rn 'menu-toggle::after' site/_templates/
```

If the live page has CSS rules NOT present in the local templates, the generated pages are stale.

**Confirmed example (June 2026):** Right hamburger icon persisted across 5+ rounds of CSS fixes because a stale inline block in 197 HTML pages injected `.menu-toggle::after { content: " ☰"; float: right; }` — loaded AFTER `nav-fix.css` and overriding all our rules.

## Fix — Strip Stale Blocks from All Generated Pages

```python
import re, glob

# Match and remove stale inline nav CSS block
pattern = re.compile(
    r'<!-- fp:[^>]+_templates/head\.html -->\s*<style>\s*/\*[^*]*Navigation Styles[^*]*\*/.*?</style>',
    re.DOTALL
)

for filepath in glob.glob('site/**/*.html', recursive=True):
    if '_templates' in filepath:
        continue
    with open(filepath, 'r') as f:
        content = f.read()
    if 'Navigation Styles' not in content:
        continue
    new_content = pattern.sub('', content)
    with open(filepath, 'w') as f:
        f.write(new_content)
```

In the June 2026 session: 23,406 lines removed from 197 files.

## Prevention

After editing templates, **rebuild all generated pages**. If the build pipeline doesn't auto-rebuild, script it:

```bash
# For static mirror sites: rebuild from templates
python3 rebuild_all.py

# Verify no stale CSS remains
grep -rl 'Navigation Styles' site/ --include='*.html' | grep -v _templates | wc -l
# Should return 0
```

## Why Inline Blocks Win (CSS Cascade)

Inline `<style>` blocks in `<head>` load AFTER `<link>` stylesheets. Even when both have `!important`, the later-loaded rule wins. A stale inline block from a previous build will silently override carefully crafted external CSS with no visible error — just "looks wrong."

## Related

- `references/nav-audit-and-fix-workflow.md` — step-by-step nav debugging
- `references/cf-external-asset-cache-staleness.md` — CDN edge cache diagnostics
- `references/static-site-content-generation.md` — template-based page generation
