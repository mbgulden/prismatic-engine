# Static Mirror Lift-and-Shift (wget dump → Cloudflare Pages)

End-to-end pattern for creating an exact static copy of an existing WordPress site and deploying it to Cloudflare Pages as a 1:1 mirror — no rebuild, no redesign. Used when the goal is to escape WordPress hosting costs while preserving all content, URLs, and functionality.

## When to Use

- WordPress site is breaking (404s, redirect chains, permalink corruption) and needs immediate stabilization
- Want to move off WordPress hosting (Flywheel, WP Engine) to free Cloudflare Pages
- No design or content changes in Phase 1 — pure lift-and-shift
- Astro rebuild is Phase 2 (later)

## Phase 1: Spider Crawl

```bash
# Crawl the entire site, save all assets
wget --mirror --page-requisites --adjust-extension \
     --convert-links --restrict-file-names=windows \
     --no-parent --wait=1 --limit-rate=500k \
     --directory-prefix=site/ \
     --user-agent="Mozilla/5.0" \
     https://example.com/
```

## Phase 2: Clean WordPress Artifacts

```bash
# Remove WP admin bar, REST API links, external image references
find site/ -name "*.html" -exec sed -i '/wp-admin-bar/d' {} \;
find site/ -name "*.html" -exec sed -i 's|https://example.com/wp-content/uploads/|/wp-content/uploads/|g' {} \;
```

## Phase 3: Deploy to Cloudflare Pages

1. Create GitHub repo, push mirror files
2. Create CF Pages project → connect to repo
3. Framework preset: **None** (this is a static HTML dump, not a framework)
4. No build command, no build output directory
5. Deploy → verify all pages return 200

## Phase 4: Fix URL Issues

WordPress static dumps often have broken permalink structures. Common issues:

### Hard 404s
Some clean URLs return 404 because the WordPress rewrite rules don't survive the static dump. These URLs need new `index.html` files created in matching directories.

### 301 Redirect Chains
WordPress may redirect clean URLs (`/tours/`) to nested permalinks (`/oahu-equipment-rentals/tours/`). The mirror serves clean URLs directly — no redirects needed. The mirror actually IMPROVES the URL structure.

### Duplicate Homepage Content
Pages that don't have corresponding files in the mirror will serve the homepage via CF Pages SPA fallback. This means `/sharks-cove-snorkeling/` (no `/sharks-cove-snorkeling/index.html` file) silently shows homepage content with a 200 status. Fix: create proper `index.html` files.

### Orphan Pages
WordPress static dumps lose the dynamic WordPress menu system. Pages may have no internal navigation links. **Do NOT inject a second navigation** — see Design Cohesion rule below.

## ⚠️ CRITICAL: Design Cohesion Rule

**When creating new pages for an existing static mirror, always extract and reuse the site's actual header, footer, and CSS.** Never create a parallel design system with custom minimal CSS.

### Wrong Approach (Rookie Mistake)
```html
<!-- Creating custom minimal pages with their own CSS/nav -->
<style>body{font-family:sans-serif;...}</style>
<div class="nav"><a href="/">Home</a></div>
<h1>Page Content</h1>
<footer>Custom footer</footer>
```
This creates TWO design systems — the original WordPress theme on old pages and custom minimal CSS on new pages. They clash visually and have different navigation structures.

### Right Approach
1. Pick an existing page from the mirror as a template (e.g., `contact-us/index.html`)
2. Extract the header section (everything before `<header class="entry-header">`)
3. Extract the footer section (everything from the site `<footer>` tag)
4. Read the existing custom page's body content (between nav and footer)
5. Rebuild: `wp_header + custom_body + wp_footer`
6. Replace title, meta description, canonical URL, and OG tags in the header

```python
# Extract WordPress header/footer from template
header_end = template.find('<header class="entry-header">')
footer_start = template.find('\n  <footer>\n')  # site footer, not form footer
wp_header = template[:header_end]
wp_footer = template[footer_start:]

# Build new page
new_page = wp_header.replace(old_title, new_title) \
                    .replace(old_desc, new_desc) \
                    .replace(old_canonical, new_canonical) \
           + custom_body_html \
           + wp_footer
```

### Verification Checklist
- [ ] WordPress nav menu present (exactly 1 instance of `class="main-navigation"`)
- [ ] Site footer present with `footer-links` class
- [ ] Original phone number, logo, and social links present
- [ ] No raw `@tailwind` or custom CSS — uses the theme's existing styles
- [ ] GTM/GA4 firing
- [ ] FareHarbor embeds working
- [ ] Unique title, meta description, and canonical URL per page

## Schema Strategy for Tour/Activity Operators

For tour booking websites like Active Oahu Tours:

| Page Type | Schema.org Type | Notes |
|-----------|----------------|-------|
| Homepage, About, Contact | `TravelAgency` | Best fit for a business that books/arranges tours. Inherits from `LocalBusiness`. Includes NAP data, geo coordinates, areaServed, hours, priceRange. |
| Individual tour/activity pages | `TouristTrip` | More specific than `Product` or `Service`. Has `itinerary`, `touristType` (Guided/Self-Guided), `provider`, `offers`. |
| Individual rental items | `Product` | For equipment rentals (single kayak, tandem, etc.) with `offers` and `category`. |
| FAQ sections | `FAQPage` | Gets FAQ rich results in Google. Place on every service page. |
| Tour listing/hub pages | `ItemList` | Lists multiple `TouristTrip` items. |

**Avoid:** `Event` schema for recurring daily tours — it confuses AI parsers since they're not chronological events.

## Orphan Page Audit

Script to find pages with zero internal incoming links:

```python
import os, re
from collections import defaultdict
from urllib.parse import urljoin

# Find all HTML files
all_pages = set()
for root, dirs, files in os.walk(mirror_dir):
    for f in files:
        if f.endswith('.html') and not f.startswith('.'):
            path = "/" + os.path.relpath(os.path.join(root, f), mirror_dir)
            if path.endswith('/index.html'):
                path = path[:-10] + '/'
            all_pages.add(path)

# Extract internal links
links_to = defaultdict(set)
for path in all_pages:
    base_url = f"https://example.com{path}"
    hrefs = re.findall(r'''href=["']([^"']+)["']''', html)
    for href in hrefs:
        resolved = urljoin(base_url, href)
        if 'example.com' in resolved:
            resolved_path = resolved.replace('https://example.com', '')
            if resolved_path in all_pages:
                links_to[resolved_path].add(path)

# Find orphans (no incoming links from other pages)
for page in sorted(all_pages):
    linkers = {l for l in links_to.get(page, set()) if l != page}
    if len(linkers) == 0:
        print(f"ORPHAN: {page}")
```

## Common Pitfalls

- **Don't inject second navigation**: WordPress pages already have their theme's nav. Adding another nav creates a clash. Fix orphan pages by ensuring they're linked FROM the existing nav structure, not by adding a parallel nav.
- **CF Pages SPA fallback**: If a URL has no matching file, CF Pages serves the root `index.html` with a 200 status. This creates invisible duplicate content. Always verify new pages are serving from their own files, not the SPA fallback.
- **Space-named files**: WordPress sometimes creates files with leading/trailing spaces in names. These cause URL errors. Find and fix with `find . -name "* *.html"`.
- **Image size limits**: CF Pages has a 25MB per-file limit. Compress oversized images before pushing.
- **Cloudflare bot detection JS**: The wget dump may include CF's bot-detection JavaScript at the end of pages. This is harmless but means `tail` of any page shows CF JS, not footer content.
