# Surgical Header/Nav Revert (Preserve All Other Work)

Restore ONLY header and navigation to their original state while keeping every other fix — SEO, schema, links, content, sitemap.

## When to Use
- User says "only the header and nav" or "just the nav"
- User wants the original WordPress header/nav back but all other work preserved
- Multiple CSS override iterations have gone sideways

## Workflow (5 Steps)

### 1. Find the original commit
```bash
cd /path/to/mirror
# The very first commit is usually the pure WordPress scrape
git log --oneline --all | tail -5
# Look for: "Active Oahu Tours — static mirror" or similar
```

### 2. Checkout files from original commit
```bash
# Restore ONLY header/nav templates from original
git checkout <original-commit> -- site/_templates/head.html site/_templates/body_top.html
```

### 3. Delete added CSS files
```bash
rm -f site/wp-content/themes/activeoahu/css/nav-fix.css
rm -f site/wp-content/themes/activeoahu/css/brand-overrides.css
```

### 4. Strip CSS link references from all baked pages
Static mirrors have the templates BAKE INTO every HTML page. Changing `_templates/` alone doesn't affect existing pages.
```bash
# Remove nav-fix.css links from all baked pages
find site -name '*.html' -not -path '*/_templates/*' -exec sed -i "/nav-fix\.css/d" {} +
# Remove brand-overrides.css links
find site -name '*.html' -not -path '*/_templates/*' -exec sed -i "/brand-overrides\.css/d" {} +
# Verify: should return 0
grep -rl 'nav-fix.css' site --include='*.html' | grep -v _templates | wc -l
```

### 5. Commit and push
```bash
git add -A && git commit -m "revert: original header and nav, remove nav CSS overrides" && git push origin main
```

## What This Preserves
- All SEO fixes (meta descriptions, OG tags, schema markup)
- All content pages (guides, rentals, tours, activities)
- All broken link fixes
- Sitemap, robots.txt, hreflang tags
- Japanese translations
- Image assets and paths

## What This Changes
- `head.html` — restored to original (original CSS links, original inline styles)
- `body_top.html` — restored to original (original nav HTML structure)
- `nav-fix.css` — deleted
- `brand-overrides.css` — deleted
- All baked page `<link>` references to the above — stripped

## Pitfalls
- **Baked pages need explicit link stripping.** Editing `_templates/` files does NOT update the 200+ generated HTML pages. Always run the `find -exec sed` step.
- **style.css may not be in the original commit.** The stylesheet was often added post-scrape. Don't try to restore it from the original — leave it alone. The header/nav is controlled by `head.html` + `body_top.html`.
- **Verify with the hash URL, not the custom domain.** The custom domain has CDN caching. Use the deployment hash URL from the Pages API to verify before telling the user.
- **Don't touch anything else.** When the user says "only the header and nav," that means: don't change CSS, don't touch page content, don't add middleware. Just the header and nav.
