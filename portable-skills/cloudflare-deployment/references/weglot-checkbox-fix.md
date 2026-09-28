# Weglot Language Switcher Checkbox Fix

## Problem

When a WordPress site using the Weglot translation plugin is scraped to static HTML (wget mirror or similar), the language switcher renders as a broken `<input type="checkbox">` on every page. The checkbox is normally hidden by Weglot's JavaScript — but on a static site, the JS isn't running and users see a raw checkbox instead of a clean "English | 日本語" link.

## Affected markup

Every scraped page contains this block (IDs vary per page):

```html
<aside data-wg-notranslate="" class="country-selector weglot-inline weglot-shortcode wg-" tabindex="0" aria-expanded="false" aria-label="Language selected: English">
  <input id="wg6a0b659fb8fbf8.241742951779131807617" class="weglot_choice" type="checkbox" name="menu"/>
  <label data-l="en" tabindex="-1" for="wg6a0b659fb8fbf8.241742951779131807617" class="wgcurrent wg-li weglot-lang weglot-language weglot-flags flag-0 wg-en" data-code-language="wg-en" data-name-language="English">
    <span class="wglanguage-name">English</span>
  </label>
  <ul role="none">
    <li data-l="ja" class="wg-li weglot-lang weglot-language weglot-flags flag-0 wg-ja" data-code-language="ja" role="option">
      <a title="Language switcher : Japanese" class="weglot-language-ja" role="option" data-wg-notranslate="" href="ja/index.html">日本語</a>
    </li>
  </ul>
</aside>
```

The `<input id="wg...">` is the visible checkbox.

## Fix

Replace the entire `<aside>` block with simple styled links using root-relative paths:

```python
import re, os

base = "site"
# IMPORTANT: Use the site's brand color, NOT white (#fff). White text disappears on light header backgrounds.
# Active Oahu uses #006699. Adjust to match the site's actual brand color.
brand_color = "#006699"
replacement = f'<span class="lang-switcher" style="font-size:14px;color:{brand_color};margin-left:15px;vertical-align:middle;"><a href="/" style="color:{brand_color};text-decoration:none;">English</a> <span style="color:{brand_color};">|</span> <a href="/ja/" style="color:{brand_color};text-decoration:none;">日本語</a></span>'
pattern = re.compile(r'<aside data-wg-notranslate="".*?</aside>', re.DOTALL)

for root, dirs, files in os.walk(base):
    for f in files:
        if f.endswith('.html'):
            path = os.path.join(root, f)
            with open(path, 'r') as fh:
                content = fh.read()
            new_content = pattern.sub(replacement, content)
            if new_content != content:
                with open(path, 'w') as fh:
                    fh.write(new_content)
```

## Key decisions

- **Root-relative paths** (`/` and `/ja/`): Works from any page depth on Cloudflare Pages. The original Weglot used relative paths like `ja/index.html` which break on subdirectory pages.
- **Brand color, NOT white (#fff)**: White links disappear on light header backgrounds. Use the site's actual brand color (e.g., `#006699` for Active Oahu). Extract the brand color from the site's CSS beforehand.
- **Inline styles**: The static site has no active CSS pipeline. Inline styles guarantee the links match the header appearance and are visible regardless of background.
- **`re.DOTALL`**: The Weglot `<aside>` block spans multiple lines — DOTALL makes `.` match newlines.

## Verification

```bash
# Check the fix was applied
grep -r "weglot_choice" site/ | wc -l
# Should be 0

# Check the replacement exists
grep -r 'lang-switcher' site/ | wc -l
# Should match the number of HTML files (typically 196-200)

# Spot-check a page
grep -o 'lang-switcher[^<]*<' site/index.html
```

## Japanese / Translated Pages: Content Not Captured

Weglot translates content dynamically on the WordPress server. When you scrape to static HTML, the translated content is NOT in the exported files — `/ja/` pages have English body text despite `lang="ja"` on the `<html>` tag. Only the language switcher label ("日本語") has actual Japanese characters.

**What IS captured:** Page structure, header, footer, navigation — all correct. The pages exist and render.

**What's missing:** All translated body content. Titles, headings, paragraphs, CTAs are English.

**Quick fix — enable browser translation:** The scraped pages have `translate="no"` on the `<html>` tag (a Weglot directive that blocks browser auto-translation). Remove it from all `/ja/` pages so Chrome can translate on the fly:

```python
import os
base = "site/ja"
for root, dirs, files in os.walk(base):
    for f in files:
        if f.endswith('.html'):
            path = os.path.join(root, f)
            with open(path, 'r') as fh:
                content = fh.read()
            if 'translate="no"' in content:
                content = content.replace(' translate="no"', '')
                with open(path, 'w') as fh:
                    fh.write(content)
```

**Full fix:** Generate actual Japanese translations via DeepL or Google Translate API and inject into each page. This is a separate, heavier effort — browser translation is the 30-second stand-in.

## Related

- See `wp-mirror-filename-cleanup.md` for other WordPress→static scrape issues (query-string artifacts, unicode thin spaces, CSS deletion risk)
- See the main cloudflare-deployment SKILL.md pitfall "Silent build failure: HTTP 200 but wrong content"
