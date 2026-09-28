# Batch Template Section Replace — Static Mirror Fixes

When a shared template section (nav bar, header, footer, banner) needs fixing
across ALL pages in a static mirror, don't regenerate pages from scratch.
Find the old section in every page via regex and replace it with the fixed
version from the template.

**Proven:** June 2026 AOT mirror nav fix — 228 pages (139 EN + 89 JP) in under 5 seconds.

## When to Use

- Fixing nav HTML that appears identically in every page
- Updating the header/logo markup site-wide
- Changing the footer or analytics snippet across all pages
- Any change where the template file (`_templates/body_top.html` etc) was
  already updated and pages just need the new version injected

## Pattern

```python
import re
from pathlib import Path

# 1. Extract the fixed section from the updated template
template = Path('site/_templates/body_top.html').read_text()
new_section = re.search(
    r'(<section class="navbar".*?</section><!-- navbar -->)',
    template, re.DOTALL
).group(1)

# 2. Find all HTML pages (skip templates, WP dirs, Japanese if needed)
pages = [p for p in Path('site').rglob('*.html')
         if '_templates' not in str(p)
         and 'wp-content' not in str(p)
         and 'wp-includes' not in str(p)]

# 3. Replace old section with new in each page
updated = 0
for page_path in pages:
    content = page_path.read_text()
    old_section = re.search(
        r'(<section class="navbar".*?</section><!-- navbar -->)',
        content, re.DOTALL
    )
    if old_section and old_section.group(1) != new_section:
        page_path.write_text(content.replace(old_section.group(1), new_section))
        updated += 1

print(f'Updated {updated} pages')
```

## Pitfalls

- **Regex must be unique**: The search pattern must match exactly one section
  per page. Use distinctive markers like `<!-- navbar -->` comments or unique
  class names. Test on 2-3 pages before mass replace.
- **Japanese pages use the same templates**: The AOT mirror has `/ja/`
  subdirectory pages that share the same HTML structure. Include them in the
  replace unless the fix is language-specific.
- **Commit after replace, not before**: `git add -A && git commit -m "fix: [description]" && git push`
  — Cloudflare Pages auto-deploys on push.
- **Check git log first**: Someone else (or another session) may have already
  applied the fix. `git log --oneline -3` before starting.
- **Patch escaping in template files only**: When editing templates with `patch`,
  watch for stray backslash escapes in HTML fragment URLs (`#` escaping to `\\`).
  Verify with `read_file` after patching.
