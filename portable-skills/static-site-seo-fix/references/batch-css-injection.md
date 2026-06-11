# Batch CSS Injection — Static Site Mirror Pattern

## When to Use
You need to apply the same CSS rule across hundreds of static HTML pages that all share a common inline `<style>` block structure. This is common with WordPress static exports (Simply Static, WP2Static) where every page has its own copy of the theme's CSS.

## Pattern

1. **Find a unique anchor string** in the CSS that exists in every page — something after the injection point
2. **Write the CSS rule** with the anchor as a suffix
3. **Use `str.replace()`** to inject the CSS before the anchor
4. **Check for already-patched pages** to avoid duplicate injection

## Proven Example (GRO-1197, June 2026)

```python
from pathlib import Path

css_rule = '''              /* Mobile header optimization */
              @media (max-width: 767px) {
                .social-header {
                  display: flex !important;
                  flex-direction: row !important;
                  ...
                }
              }
'''

anchor = '              /* Close button styling */'
patched = 0

for f in site_dir.rglob('*.html'):
    if '_templates' in str(f):
        continue
    content = f.read_text(encoding='utf-8', errors='replace')
    if 'social-header' not in content:
        continue  # skip pages without the target element
    if 'Mobile header optimization' in content:
        continue  # already patched
    content = content.replace(anchor, css_rule + '\n' + anchor, 1)
    f.write_text(content, encoding='utf-8')
    patched += 1
```

Result: 236 pages patched in one pass, zero errors.

## Anchor Selection Guidelines

- Pick text that is **unique** to the CSS block (not HTML content)
- Prefer **comments** over code strings — comments are less likely to change
- Verify the anchor exists in ALL target pages before running the batch
- Test on 1-2 pages first, verify rendering, then run the full batch

## Pitfalls

- **`read_file` from `execute_code` sandbox returns LINE_NUM|CONTENT format** — never use it for file mutations. Use `terminal()` with Python's built-in `open()`. The line-number prefix corrupts files when written back.
- **Encoding**: Always use `encoding='utf-8', errors='replace'` for WordPress static exports — they often contain mixed encodings
- **Skip templates**: Always skip `_templates/` directory — those are source files, not deployed pages
- **Idempotency**: Always check if the injection already exists before patching (the `'Mobile header optimization' in content` guard above)
- **Single replacement**: Use `str.replace(old, new, 1)` to only replace the first occurrence — the anchor may appear in multiple locations
