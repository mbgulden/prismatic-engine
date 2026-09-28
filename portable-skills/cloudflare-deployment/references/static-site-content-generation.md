---
name: static-site-content-generation
description: >
  Batch-generate static HTML pages for websites using extracted templates
  from a WordPress mirror. Covers: extracting head/header/footer from
  a homepage, customizing per-page meta/schema/body, reassembling complete
  pages, and deploying. Use when building many content pages at once for
  a static site under active development.
triggers:
  - create pages or build pages or generate pages
  - batch pages or content cluster or content pages
  - static site or mirror or WordPress mirror
  - template injection or page generation or bulk content
  - generate HTML or assemble pages or build out site
always-delegate: false
---

# Static Site Content Generation

## Purpose
Batch-generate dozens of static HTML pages using a WordPress mirror's existing
theme template. Extract the head, header, navigation, and footer from the
homepage, then inject unique per-page meta tags, schema, and body content while
preserving the identical site chrome (logo, nav, footer, CSS).

## When to Use
- Building out a content cluster (8-30 pages at once)
- Creating landing pages for SEO keywords
- Adding pages to an existing static mirror of a WordPress site
- Need consistent site chrome (nav/header/footer) across all new pages

## Template Extraction

### Step 1: Extract Templates from Homepage
```bash
python3 << 'PYEOF'
SITE = "/path/to/site"
with open(f"{SITE}/index.html", 'r') as f:
    lines = f.read().split('\n')

# Find boundary lines
head_end = body_start = content_start = content_end = footer_start = None
for i, line in enumerate(lines):
    ln = i + 1
    if '</head>' in line and head_end is None: head_end = ln
    if '<body' in line and body_start is None: body_start = ln
    if 'class="entry-content"' in line and content_start is None: content_start = ln
    if '<!-- .entry-content -->' in line and content_end is None: content_end = ln
    if line.strip() == '<footer>' and footer_start is None: footer_start = ln

# Save templates
head = '\n'.join(lines[:head_end])
body_top = '\n'.join(lines[body_start-1:content_start-1])
body_bottom = '\n'.join(lines[content_end:])

os.makedirs(f"{SITE}/_templates", exist_ok=True)
with open(f"{SITE}/_templates/head.html", 'w') as f: f.write(head)
with open(f"{SITE}/_templates/body_top.html", 'w') as f: f.write(body_top)
with open(f"{SITE}/_templates/body_bottom.html", 'w') as f: f.write(body_bottom)
```

Templates saved: `head.html`, `body_top.html`, `body_bottom.html`.

### Step 2: Page Generation Script

For each page, customize the head template (title, meta, og, twitter, canonical),
inject JSON-LD schema before `</head>`, add unique body content, and reassemble:

```python
import os, json, re

with open(f"{SITE}/_templates/head.html") as f: head_template = f.read()
with open(f"{SITE}/_templates/body_top.html") as f: body_top = f.read()
with open(f"{SITE}/_templates/body_bottom.html") as f: body_bottom = f.read()

for page in pages:
    os.makedirs(f"{SITE}/{page['slug']}", exist_ok=True)
    
    # Customize head
    head = head_template
    head = re.sub(r'<title>[^<]+</title>', f"<title>{page['title']}</title>", head)
    head = re.sub(r'<meta name="description" content="[^"]*"', f'<meta name="description" content="{page["desc"]}"', head)
    head = re.sub(r'<meta property="og:title" content="[^"]*"', f'<meta property="og:title" content="{page["title"]}"', head)
    head = re.sub(r'<meta property="og:description" content="[^"]*"', f'<meta property="og:description" content="{page["desc"]}"', head)
    head = re.sub(r'<meta property="og:url" content="[^"]*"', f'<meta property="og:url" content="https://DOMAIN/{page["slug"]}/"', head)
    head = re.sub(r'<meta name="twitter:title" content="[^"]*"', f'<meta name="twitter:title" content="{page["title"]}"', head)
    head = re.sub(r'<meta name="twitter:description" content="[^"]*"', f'<meta name="twitter:description" content="{page["desc"]}"', head)
    head = re.sub(r'<link rel="canonical" href="[^"]*"', f'<link rel="canonical" href="https://DOMAIN/{page["slug"]}/"', head)
    
    # Inject schema before </head>
    schema_ld = f'<script type="application/ld+json">{json.dumps(schema)}</script>'
    head = head.replace('</head>', f'{schema_ld}\n</head>')
    
    # Assemble
    content_block = f'<div id="content" class="site-content"><div class="entry-content"><h1>{page["h1"]}</h1>{page["body"]}</div></div>'
    page_html = head + '\n' + body_top + '\n' + content_block + '\n' + body_bottom + '\n</body>\n</html>'
    
    with open(f"{SITE}/{page['slug']}/index.html", 'w') as f:
        f.write(page_html)
```

### Step 3: Verification

```bash
for slug in page1 page2 page3; do
  title=$(grep -oP '<title>\K[^<]+' site/$slug/index.html)
  schema=$(grep -c '@type' site/$slug/index.html)
  size=$(wc -c < site/$slug/index.html)
  echo "  /$slug/ — $title — $schema schemas — $size chars"
done
```

## Page Assembly Pattern

Every generated page follows this structure:
```
[head.html — customized per page]
[body_top.html — identical header/nav/logo from homepage]
[unique content — h1 + body HTML]
[body_bottom.html — identical footer/scripts from homepage]
</body>
</html>
```

## Recommended Assembly Method: `execute_code` (not terminal heredocs)

The `execute_code` Python sandbox is the RIGHT tool for page assembly. It avoids all shell escaping issues (ampersands interpreted as backgrounding, quote nesting, em-dash encoding). Pattern:

```python
import os, re, json

# 1. Read templates
with open(f"{SITE}/_templates/head.html") as f: head = f.read()
with open(f"{SITE}/_templates/body_top.html") as f: body_top = f.read()
with open(f"{SITE}/_templates/body_bottom.html") as f: body_bottom = f.read()

# 2. Regex-replace 8 SEO metadata fields in head
head = re.sub(r'<title>.*?</title>', f'<title>{title}</title>', head, count=1)
head = re.sub(r'<meta name="description" content="[^"]*"/>', f'<meta name="description" content="{desc}"/>', head, count=1)
head = re.sub(r'<link rel="canonical" href="[^"]*" />', f'<link rel="canonical" href="{url}" />', head, count=1)
head = re.sub(r'<meta property="og:title" content="[^"]*" />', f'<meta property="og:title" content="{title}" />', head, count=1)
head = re.sub(r'<meta property="og:description" content="[^"]*" />', f'<meta property="og:description" content="{desc}" />', head, count=1)
head = re.sub(r'<meta property="og:url" content="[^"]*" />', f'<meta property="og:url" content="{url}" />', head, count=1)
head = re.sub(r'<meta name="twitter:title" content="[^"]*" />', f'<meta name="twitter:title" content="{title}" />', head, count=1)
head = re.sub(r'<meta name="twitter:description" content="[^"]*" />', f'<meta name="twitter:description" content="{desc}" />', head, count=1)

# 3. Inject schemas before </head>
head = head.replace('</head>', article_schema + '\n' + faq_schema + '\n</head>')

# 4. Write entry-content (the unique body for this page)
entry = f'<div class="entry-content">\n...page-specific HTML...\n</div><!-- .entry-content -->'

# 5. Handle body_bottom closure — some templates already include </body></html>
close_pos = body_bottom.rfind('</html>')
if close_pos > 0:
    body_bottom = body_bottom[:close_pos] + '\n</div><!-- #page -->\n</body>\n</html>'

# 6. Assemble and write
full = head + '\n' + body_top + '\n' + entry + '\n' + body_bottom
os.makedirs(f"{SITE}/guides/{slug}", exist_ok=True)
with open(f"{SITE}/guides/{slug}/index.html", "w") as f:
    f.write(full)
```

## Schema Selection by Page Type

| Page Type | Schema | Notes |
|-----------|--------|-------|
| Tour/activity page | TouristTrip | trip starts at shop, not destination |
| Rental/product page | Product | AggregateOffer with lowPrice/highPrice |
| Informational guide | Article + FAQPage | **Always combine.** Article for the content body, FAQPage for 4-5 Q&As that target PAA (People Also Ask) snippets. Inject both before `</head>`. |
| FAQ page | FAQPage | Question/Answer pairs |
| Comparison page | Article + FAQPage | comparison Q&As as FAQ schema |
| Hub/listing page | ItemList | itemListElement with URLs |
| Contact page | ContactPage | about links to TravelAgency |
| About/brand page | Organization | PostalAddress, sameAs, awards |

## Conversion CTA Block Pattern

Every informational guide page should include a styled CTA box linking to the commercial product the guide supports. Proven template:

```html
<div class="cta-box" style="background:#e8f4f8; padding:20px; border-radius:8px; margin:30px 0;">
  <h3 style="margin-top:0;">[Action-Oriented Headline]</h3>
  <p>[One-sentence value prop connecting the guide topic to the product.]</p>
  <a href="/[product-page]/" class="btn-primary" style="display:inline-block; padding:12px 24px; background:#006699; color:#fff; text-decoration:none; border-radius:4px; font-weight:bold;">[Button CTA Text]</a>
</div>
```

Place CTA blocks after the first content section and again before the final paragraph. Two CTAs per page minimum — guides exist to drive commercial conversions, not just traffic.

## Full Workflow: Generate → Commit → Push → Update Linear

For each page batch:
1. Generate page(s) with `execute_code` (see Recommended Assembly Method above)
2. Verify with `head -10 site/guides/<slug>/index.html` and `grep -c 'ld+json' site/guides/<slug>/index.html`
3. Commit: `git add site/guides/<slug>/ && git commit -m "feat(GRO-XXX): [page title] — [key features]" && git push origin main`
4. Mark issue Done in Linear with inline-ID mutation (see golden-thread skill for Linear mutation patterns)

Never batch-commit unrelated pages together — one commit per guide page keeps history clean and revert-safe.

## Git Pre-Work Check

Before generating pages for a shared mirror repo: `git log --oneline -5` to verify prior sessions haven't already built the same pages. The AOT mirror had schema injection committed upstream before a full injection script was written — a 2-second log check saves 10+ minutes of duplicate work.

## Pitfalls

- **Template wrapping of full wget pages creates nested `#content` divs (CRITICAL):** When the mirror contains full HTML pages from `wget` (with their own `<html>`, `<head>`, `<body>`, `<div id="content">`, header, nav, and footer), assembling them via `head + body_top + page_html + body_bottom` creates DOUBLE or TRIPLE wrappers. The `body_top.html` opens `<div id="content">`, and the page HTML also contains its own `<div id="content">` — resulting in nested `#content → #content → #content → actual content`. This breaks all CSS layout because Kadence/theme selectors expect a single `#content` parent. **Detection:** `grep -c 'id="content"' page.html` — if count > 1, the page has nested content divs. **Fix:** Two approaches: (A) For individual pages: download fresh from Flywheel, extract only the content area (`<div id="content" class="site-content">...</div><!-- #content -->`), wrap with nav template + minimal footer. (B) For site-wide fix: change `body_top.html` to NOT open `#content` (let the page HTML provide it) and change `body_bottom.html` to be a minimal footer (not the home page CTA blocks). The AOT mirror (June 2026) had 3 nested `#content` divs on every inner page because `body_bottom.html` contained the home page's entire features/testimonials/newsletter section — 1,255 lines of home-page-specific content appended to every page.

- **`body_bottom.html` may include `</body></html>`**: The WordPress mirror's footer template often includes the closing tags. Before appending, check: `close_pos = body_bottom.rfind('</html>')`. If found, truncate and re-add the proper closure. Skipping this produces pages with double `</html>` tags.
- **Use `count=1` on regex replacements**: The head template may contain multiple matching patterns (e.g., `og:description` appears in both the `og:` block and the JSON-LD schema). Without `count=1`, every match is replaced — corrupting the JSON-LD.
- **`#page` div closure**: The `body_top.html` opens `<div id="page" class="hfeed site">`. The `body_bottom.html` closes it. If you're injecting entry-content between them, make sure your content doesn't break this nesting.
- **Always verify template boundaries**: Check that `content_start`, `content_end`, and `footer_start` line numbers match the actual site structure before generating pages. Different WordPress themes have different boundary markers.
- **CSS path issues**: WordPress mirrors may use relative CSS paths (`wp-content/themes/...`). Root-level pages resolve these correctly, but deep pages may need `../../wp-content/...` paths. Check an existing deep page to confirm.
- **Character encoding**: Use Unicode escapes in Python strings meant for HTML output. Shell heredocs with `&` in HTML content will break — write generation scripts to `.py` files and execute them instead.
- **Don't generate pages with terminal heredocs containing HTML**: The shell interprets `&` as backgrounding. Write generation scripts to `.py` files and run with `python3 script.py`. Prefer `execute_code` over terminal for page assembly.
- **Git commit after every batch**: Generate → verify → commit → push. Keeps the history clean and lets you revert individual batches.
- **Always use the full WordPress template** (header, nav, footer from the mirror homepage) — never inject custom navigation. The existing site navigation is the only navigation that should appear on any page.

## Performance

- 8-page batch: ~10 seconds generation + verification
- 12-page batch: ~15 seconds
- Use subagent delegation for 3+ batches in parallel

## Site-Specific Recipes

- **Active Oahu Tours mirror**: `references/aot-mirror-guide-generation.md` — proven template paths, schema templates, CTA box HTML, verification commands, and page generation checklist. Validated across 3 guide pages (Jun 2026).

## Upstream Content Preparation

## Nav/Header Rebuild: Template Edit → Rebuild → Staging Deploy

When injecting a new nav CSS/JS block into an existing mirror whose pages were previously generated:

### The Rebuild Script
```python
import os
with open('site/_templates/head.html') as f: head = f.read()
with open('site/_templates/body_top.html') as f: bt = f.read()
with open('site/_templates/body_bottom.html') as f: bb = f.read()
count = 0
for root, dirs, files in os.walk('site'):
    dirs[:] = [d for d in dirs if d != '_templates']
    for fn in files:
        if not fn.endswith('.html'): continue
        fp = os.path.join(root, fn)
        try:
            with open(fp) as f: html = f.read()
            cs = html.find('<div id="content" class="site-content">')
            ce = html.find('</div><!-- #content -->')
            if cs >= 0 and ce >= 0:
                content = html[cs:ce + len('</div><!-- #content -->')]
                with open(fp, 'w') as f: f.write(head + '\n' + bt + '\n' + content + '\n' + bb)
                count += 1
        except: pass
print(f'Rebuilt {count} pages')
```

### Deploy to Staging Only (Protect Production)
```bash
git add -A
git commit -m "fix: nav update"
git push origin main:staging --force    # deploy to staging branch
git reset --hard HEAD~1                 # revert main — production stays clean
```

### Pitfall: Template Reversion After Reset
After `git reset --hard HEAD~1`, the head.html template reverts with main. Each new fix round requires re-injecting the CSS block. Store it as `/tmp/nav-css-block.html` and inject with `patch` tool each round.

### Pitfall: Japanese Pages Skip Rebuild
Japanese pages (ja/) use different content markers. The rebuild script will report ~89 errors for ja/ pages — acceptable for initial deploy. Fix Japanese pages separately with their own content markers.

- **Takeout document conversion**: `references/takeout-document-conversion.md` — batch convert Google Takeout .docx/.xlsx archives into AI-readable .md/.csv for content gap analysis. Covers pandoc + openpyxl pipeline, execute_code sandbox pitfall, multi-sheet workbook handling, and the 4-phase Convert→Map→Analyze→Generate workflow. Validated on 207-file, 5.9 GB Active Oahu takeout (Jun 2026).

## Deployment

- Push to GitHub (CF Pages auto-deploys if connected)
- Or `wrangler pages deploy site --project-name PROJECT --branch main`
- Verify with `curl -sI https://DEPLOY_URL/page-name/ | head -3`

## Mobile Nav JS/CSS Conflict Fix (Safari vs Chromium)

When mobile sub-menu items are invisible on real Safari but visible in Playwright (Chromium), the root cause is often a JS/CSS `!important` conflict. Safari may not honor CSS `!important` over inline `element.style.display` set by JS the way Chromium does.

**Symptom**: Sub-menu items are invisible on iPhone Safari, but Playwright shows them with correct computed styles (`display:block`, `color:white`).

**Root cause**: JavaScript sets `subs[i].style.display = 'none'` on all sub-menus at page load. CSS tries to override with `.main-navigation.toggled .sub-menu{display:block!important}` but Safari doesn't consistently honor `!important` over inline styles.

**Reliable fix — guard JS to desktop only**:
```javascript
var isDesktop = window.innerWidth > 767;
var subs = document.querySelectorAll('.sub-menu');
if (isDesktop) {
    for (var i = 0; i < subs.length; i++) { subs[i].style.display = 'none'; }
}
// ... also guard click handlers and document click with `if (!isDesktop) return;`
```

This prevents the JS from ever setting inline `display:none` on mobile, letting CSS handle visibility without any conflict. On desktop, JS continues to manage hover/click dropdowns normally.

**Pitfall**: `!important` in CSS stylesheets should theoretically override non-important inline styles in all browsers per the CSS spec. In practice, Safari WebKit handles this edge case unreliably. The JS guard approach avoids the conflict entirely.

## Bulk HTML String Replacement for Static Sites

When a template change needs to propagate to 200+ already-generated static HTML pages, don't re-run generation scripts. Use exact string replacement:

```python
import glob, os

old_block = "..."  # exact multi-line string to find
new_block = "..."  # exact multi-line replacement

for f in glob.glob('site/**/*.html', recursive=True):
    with open(f, 'r') as fh:
        content = fh.read()
    if old_block not in content:
        continue
    new_content = content.replace(old_block, new_block)
    with open(f, 'w') as fh:
        fh.write(new_content)
```

This is O(n) per file (single pass string search) vs O(n²) for regex, and avoids regeneration script complexity (template paths, schema injection, file I/O per page). For the 244-page AOT mirror, this replaced a JS block across all EN + JP pages in ~2 seconds.

Pitfall: the old block must be an EXACT match including whitespace and indentation. Copy-paste directly from the template file — don't retype.

## Standalone CSS Override Pattern (Minified Stylesheet Workaround)

When a static mirror's main CSS file is **minified** (single-line, 50KB+), **never** append patches to it. Every appended rule fights earlier specificity and produces unpredictable results. Instead, create a standalone override file loaded AFTER the main stylesheet — the cascade guarantees your rules win.

### Pattern

1. **Create** `site/wp-content/themes/<theme>/css/nav-fix.css` (clean, readable CSS with all overrides)
2. **Link it** in `_templates/head.html` immediately after the main stylesheet:
   ```html
   <link rel='stylesheet' href='/wp-content/themes/activeoahu/css/style.css?v=7' type='text/css' media='all' />
   <link rel='stylesheet' href='/wp-content/themes/activeoahu/css/nav-fix.css?v=1' type='text/css' media='all' />
   ```
3. **Inject into all existing pages** (if templates don't auto-rebuild — find + sed is fastest for single-line injection):
   ```bash
   find site -name '*.html' -not -path '*/_templates/*' -exec sed -i "/style.css?v=[0-9]*'/a <link rel='stylesheet' href='/wp-content/themes/activeoahu/css/nav-fix.css?v=1' type='text/css' media='all' />" {} +
   ```
4. **Bump the CSS version** on both files with each deploy to break CDN cache.

### Why This Wins

The CSS cascade: later-loaded stylesheets win at equal specificity. A clean 340-line `nav-fix.css` loaded after a 52KB minified `style.css` wins every conflict — no `!important` wars, no fighting invisible earlier rules.

### Cascade Pitfalls

- **Don't globally kill pseudo-elements**: If the original CSS sets `content` on `::before` (e.g., a hamburger icon) and you want your own version, do NOT add a global `content: none !important` rule. That kills ALL `::before` content — yours included. Instead, trust the cascade: your override file loads after the original, so your own `::before` rule (inside the mobile media query) naturally overrides the original's. The only thing you should kill globally is `display: none` on the element itself for desktop.
- **Double-hamburger debugging**: If you see two hamburger icons, the original minified CSS is setting `content` on `.menu-toggle:before` OUTSIDE any media query. Your override CSS (loaded later) sets it inside `@media (max-width: 1023px)`. Since both match on mobile and yours comes later in cascade order, yours wins — no kill rule needed. If you add a global kill rule, you remove yours too, leaving only the original's `☰` plus your "Main Menu" text — which the user will report as "wrong hamburger removed."
- **Don't recreate the original stylesheet** — only write rules for what you're changing.
- **Use the same selectors** as the original CSS for equal-specificity overrides.
- **Version-bump both files** on every deploy or CDN will serve stale CSS.
- **Inline `<style>` blocks beat external CSS**: When generated HTML pages contain `<style>` blocks (common in WordPress mirrors where templates bake in per-page CSS), those inline rules have higher specificity than ANY external stylesheet — including your override file. If your CSS fixes aren't sticking despite correct cascade order, grep the generated HTML for `<style>` blocks targeting the same elements. Fix: upgrade your external CSS selectors to include a parent context (e.g., `.aot-logo img` → `body #branding .aot-logo img`) to beat inline specificity. This is why AGY's full-codebase reads succeed where manual CSS iteration fails — AGY reads the generated HTML files, not just templates and stylesheets.

## Site-Wide Batch SEO Fixes

For mass fixes across existing pages (meta descriptions, OG tags, lazy loading, preconnect, FareHarbor verification, image compression), see `references/batch-seo-fixes-for-static-mirrors.md` — patterns proven on the 238-page AOT mirror.
