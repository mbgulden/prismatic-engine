# WordPress → Static Mirror Migration Pattern

## When to Use
When you need to move a WordPress site off its current host FAST (days, not weeks), with zero visual or content changes. This is a lift-and-shift — you capture the entire site as static HTML and deploy to Cloudflare Pages. Perfect as a baseline before iterative improvements.

## Why Not Astro Rebuild?
An Astro rebuild is better long-term (faster, cleaner, programmatic SEO). But it takes 2-5 weeks and requires content extraction, component rebuilding, and visual matching. The static mirror takes 2-4 hours and produces an exact 1:1 copy.

## Full Workflow

### Step 1: Spider + Mirror
```bash
wget --mirror --page-requisites --adjust-extension \
     --convert-links --restrict-file-names=windows \
     --no-parent --wait=1 --random-wait \
     --user-agent="Mozilla/5.0" \
     https://activeoahutours.com/
```
**Caution:** wget may miss pages not linked from the homepage. After the mirror, fetch sitemaps and crawl every URL to catch orphans.

### Step 2: Clean WordPress Artifacts
- Strip `wp-admin` references, REST API links, admin bar CSS
- Convert all external image references to local relative paths
- Remove Yoast/plugin meta comments that add noise
- Run: `grep -r "wp-content" --include="*.html" | wc -l` to verify zero external references remain

### Step 3: Compress Images
CF Pages has a 25MB file size limit. Any image over 25MB will fail the build. Use:
```bash
find . -name "*.jpg" -size +10M | while read f; do
  convert "$f" -quality 85 -resize "1600x1600>" "${f}.tmp" && mv "${f}.tmp" "$f"
done
```
Same for PNGs. Target: total repo under 500MB for fast pushes.

### Step 4: Push to GitHub + Deploy to CF Pages
- Create GitHub repo, push the static files
- Create CF Pages project: Framework Preset = "None" (no build step needed)
- Connect to GitHub — auto-deploys on push
- CF Pages serves directory-based routing: `/page/index.html` → `/page/`

### Step 5: Audit for 404s and Redirects
This is the critical step that catches problems BEFORE DNS switch.

```bash
# Pull all URLs from sitemaps
for sm in page-sitemap.xml activities-sitemap.xml rentals-sitemap.xml; do
  curl -s "https://OLDSITE.com/$sm" | grep -oP '<loc>\K[^<]+' >> all_urls.txt
done

# Check each URL against live site AND mirror
while read url; do
  path=$(echo "$url" | sed 's|https://OLDSITE.com||')
  live_code=$(curl -s -o /dev/null -w "%{http_code}" "$url/")
  mirror_code=$(curl -s -o /dev/null -w "%{http_code}" "https://MIRROR.pages.dev${path}/")
  echo "$live_code $mirror_code $path"
done < all_urls.txt
```

**Interpreting results:**
- `404 200` → Broken on live, fixed in mirror — RECOVERABLE TRAFFIC
- `301 200` → Redirect on live, direct on mirror — CLEANER URL STRUCTURE
- `200 200` → Works on both

### Step 6: Detect Duplicate Content
WordPress static dumps often serve the same template at multiple URLs. Detect with:
```python
HP_TITLE = "expected homepage title"
for path in pages:
    html = fetch(f"{base}{path}")
    title = re.search(r'<title>(.*?)</title>', html)
    if HP_TITLE in title:
        print(f"DUPLICATE: {path}")
```

Pages sharing the homepage title are serving identical template content — they exist at the URL but have no unique content. Replace these with unique SEO-optimized pages before DNS switch.

### Step 6b: Template-Based Page Generation (Replace Duplicates)

When dozens of pages need unique content but must share the WordPress theme's header/nav/footer, don't write each page from scratch. Extract the template once, then batch-generate:

```python
import re, json, os

SITE = "/path/to/mirror/site"
homepage = f"{SITE}/index.html"

# Read homepage and find boundary lines
with open(homepage) as f:
    lines = f.readlines()

# Find boundaries (1-indexed)
head_end = body_start = content_start = content_end = footer_end = None
for i, line in enumerate(lines, 1):
    if '</head>' in line and not head_end: head_end = i
    if '<body' in line and not body_start: body_start = i
    if 'class="entry-content"' in line and not content_start: content_start = i
    if '<!-- .entry-content -->' in line and not content_end: content_end = i
    if '</footer>' in line and not footer_end: footer_end = i

# Extract template sections
head_template = ''.join(lines[:head_end])           # <head> with all CSS/JS/meta
body_top = ''.join(lines[body_start-1:content_start-1])  # logo + nav
body_bottom = ''.join(lines[content_end:footer_end])     # footer + widgets + scripts

# For each duplicate page, substitute unique content:
for page in pages:
    head = head_template
    head = re.sub(r'<title>[^<]+</title>', f"<title>{page['title']}</title>", head)
    head = re.sub(r'<meta name="description" content="[^"]*"', 
                  f'<meta name="description" content="{page["desc"]}"', head)
    head = re.sub(r'<link rel="canonical" href="[^"]*"',
                  f'<link rel="canonical" href="https://domain.com/{page["slug"]}/"', head)
    
    # Inject JSON-LD schema before </head>
    schema_ld = f"<script type='application/ld+json'>{json.dumps(page['schema'])}</script>"
    head = head.replace('</head>', f'{schema_ld}\n</head>')
    
    # Assemble: head + nav + unique content + footer
    page_html = head + body_top + page['body_html'] + body_bottom + '\n</body>\n</html>'
    
    os.makedirs(f"{SITE}/{page['slug']}", exist_ok=True)
    with open(f"{SITE}/{page['slug']}/index.html", 'w') as f:
        f.write(page_html)
```

This produces pages with identical visual chrome (logo, nav, footer, fonts, colors) but unique titles, meta descriptions, canonical URLs, schema, and body content. One template, infinite pages.

**CSS path pitfall**: WordPress themes often use relative CSS paths like `wp-content/themes/name/style.css`. On root-level pages this resolves correctly. On deep pages (e.g., `/activities/foo/`), the browser looks for `/activities/wp-content/...` which 404s. Existing WordPress pages handle this with depth-relative paths (`../../wp-content/...`), but newly generated root-level pages use the root-relative form. **Always verify CSS loads** on generated pages before deploying — a page with broken CSS looks unstyled but doesn't throw errors you'll catch in a static audit.

**Schema injection**: When generating pages, add structured data (JSON-LD) during the head customization pass. For tourism sites, inject TouristTrip/Product/FAQPage schema alongside the template substitution. See `references/tourism-schema-implementation.md` for the full schema hierarchy and injection pattern.

### Step 7: Custom Domain + DNS Switch
- Add custom domain in CF Pages dashboard: `activeoahutours.com`
- CF Pages auto-provisions SSL (same-account zones verify automatically)
- Update DNS: CNAME `@` → `project-name.pages.dev`
- Switch is instant once DNS propagates (typically <5 min on Cloudflare)

## Pitfalls
- **wget misses pages**: Always cross-reference against sitemaps after mirror
- **25MB file limit**: Large images silently fail CF Pages builds — compress proactively
- **Duplicate titles**: The #1 SEO problem in static mirrors — every page inherits the WP theme's default title. Fix before DNS switch.
- **SPA fallback**: CF Pages may serve `index.html` as a catch-all, making non-existent URLs return 200 with homepage content. Create actual `dir/index.html` files to override.
- **Trailing slash normalization**: CF Pages does this automatically, but WordPress redirects may conflict. Add a `_redirects` file for edge cases.
