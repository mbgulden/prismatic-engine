# Staging CSS Matching for Static Mirror Nav Fixes

When the live static mirror nav doesn't match the staging WordPress site, the fastest path is to copy the staging site's compiled `style.css` directly.

## The Pattern

1. **Diff the CSS files** — the staging site often has brand overrides, nav fixes, or mobile styles baked into its `style.css` that the mirror is missing
2. **Copy staging `style.css` → mirror** — `cp` the entire file rather than trying to selectively apply fixes
3. **Remove separate override files** — if the mirror was loading `brand-overrides.css` separately, remove it; it's now baked into `style.css`
4. **Version-bust** — bump the `?v=N` query parameter on the CSS link
5. **Rebuild all pages** — `sed` the new version across all generated HTML files
6. **Push + deploy**

## Why This Works

The staging WordPress site compiles its theme CSS (base + child theme + customizer overrides + plugins) into a single minified file. The static mirror, generated via wget, captures a snapshot of that CSS at mirror-time. When nav fixes are made on staging AFTER the mirror, the mirror's CSS goes stale.

Rather than reverse-engineering which specific rules changed, copying the entire compiled `style.css` from staging ensures every rule matches exactly.

## Commands

```bash
# 1. Copy staging CSS to mirror
cp /path/to/staging-site/wp-content/themes/activeoahu/css/style.css \
   /path/to/mirror/site/wp-content/themes/activeoahu/css/style.css

# 2. Update version in template
sed -i 's/style.css?v=N/style.css?v=N+1/g' site/_templates/head.html

# 3. Rebuild all generated pages
find site -name "*.html" -not -path "*/_templates/*" | while read f; do
  sed -i 's/style.css?v=OLD/style.css?v=NEW/g' "$f"
done

# 4. Push
git add -A && git commit -m "fix: use staging style.css with brand overrides" && git push origin main
```

## Pitfalls

- **Check the mobile breakpoint** — staging may use 549px, mirror may need 1024px for tablets. Update the `@media (max-width: Npx)` in the copied CSS if needed.
- **Remove conflicting inline styles** — earlier attempts may have added `<style>` blocks in `head.html` that now conflict with the staging CSS. Trim them.
- **Cache busting is mandatory** — CF Pages caches CSS aggressively. Without `?v=N` bump, changes won't appear for hours.
- **Verify with Playwright screenshots** — after deploy, take screenshots at desktop, tablet, and mobile to confirm the nav matches staging.
