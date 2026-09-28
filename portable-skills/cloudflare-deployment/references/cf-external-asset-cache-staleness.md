# CF Pages External Asset Cache-Staleness

When a CSS/JS fix is deployed to CF Pages but the live site still shows broken behavior, the CDN edge cache is serving stale external assets. This is the #1 time-sink in CSS debugging on CF Pages.

## The Problem

CF Pages deploys HTML pages AND external assets (CSS, JS, fonts) together. The CDN edge cache can hold BOTH:

- **HTML pages**: May serve stale after successful deploys — `cf-cache-status: HIT` on `.html` with `age: 30000+` (8+ hours). Deploy succeeds, cache doesn't invalidate.
- **External assets** (`style.css`, `script.js`): Cached aggressively because they're the same URL across many pages.

Two distinct failure modes:

### Mode 1: CSS fix doesn't take effect
```
curl with cache-bust: shows new CSS ✓
Browser loads page: gets fresh HTML ✓
Browser loads style.css: gets CACHED old CSS ✗  ← cf-cache-status: HIT
```
Result: page loads but with wrong styles.

### Mode 2: HTML page itself is stale (THIS SESSION)
```
curl with cache-bust: shows fixed HTML ✓
Browser loads page: gets CACHED old HTML ✗  ← cf-cache-status: HIT, age: 34777
curl without bust: gets CACHED old HTML ✗
```

**Symptoms:** Key elements missing from page source (e.g., `<link>` to `nav-fix.css` absent entirely). Deployment API says latest commit deployed successfully. The page IS deploying — the cache just won't release the old version.

**Real-world example (June 2026):** Production `activeoahutours.com` deployed commit `d158dd5` (v=11 fixes including `nav-fix.css`). CF Pages API confirmed deployment success with correct commit SHA. Live site was missing `nav-fix.css` entirely — `cf-cache-status: HIT` with `age: 34777` (~9.6 hours). CDN was serving HTML from a PREVIOUS deployment.

## Detection

```bash
# Check if CSS is cached
curl -sI "https://site.com/wp-content/themes/name/css/style.css" | grep cf-cache-status
# Returns: cf-cache-status: HIT

# Compare cached vs origin
curl -s "https://site.com/wp-content/themes/name/css/style.css" | grep 'new-rule'
# Returns nothing — old cached version

curl -s "https://site.com/wp-content/themes/name/css/style.css?nocache=$(date +%s)" | grep 'new-rule'
# Returns match — origin has the fix
```

## Fix: Version-Bust the CSS Link

Add a version query parameter to the `<link>` tag in every HTML page:

```html
<!-- Before -->
<link href="/wp-content/themes/name/css/style.css" rel="stylesheet">

<!-- After -->
<link href="/wp-content/themes/name/css/style.css?v=2" rel="stylesheet">
```

Then bulk-replace across all pages:

```bash
cd /path/to/repo
find site -name '*.html' -exec sed -i 's|style.css'\''|style.css?v=2'\''|g' {} \;
# Also update the template
sed -i 's|style.css'\''|style.css?v=2'\''|g' site/_templates/head.html
```

Commit, push, and CF Pages deploys every HTML file with the new URL. The CDN treats `style.css?v=2` as a new resource, bypassing the stale cache.

## When to Use This vs Cache Purge

| Method | When | Cost |
|--------|------|------|
| Version-bust | No Cloudflare API access, need fast fix | Modifies all HTML, must rebuild |
| CF cache purge | Have API token with zone perms | 1 API call, no code changes |
| Trigger commit | Have git push access but NO zone purge perms | Empty commit, ~30s deploy |

**Trigger commit (for Pages-only tokens):** CF API tokens scoped to Pages
(`cfat_...` with account-level Pages:Edit) can manage deployments but cannot
purge zone cache — cache purge requires zone-level permissions. When you
can't purge the CDN edge cache directly, push an empty commit to the
production branch:

```bash
git checkout main
git commit --allow-empty -m "chore: trigger deploy to bust CDN cache"
git push origin main
```

CF Pages auto-builds and deploys — the new deployment invalidates the edge
cache for all assets including HTML. Wait for deploy to reach `stage=success`
before verifying.

## Pitfall: Forgetting the Template

If you version-bust all generated HTML but forget `site/_templates/head.html`, the NEXT site rebuild will revert to the unversioned URL and bring back the stale cache. Always update both.

## Pitfall: Playing CSS Whack-a-Mole

When `cf-cache-status: HIT` is the root cause, every CSS edit appears to not work. The debugging sequence becomes:

1. Write CSS fix → push → test → still broken
2. Think the fix was wrong → write different fix → push → test → still broken
3. Revert everything → push → test → still broken (because cache still has even older version)
4. Get frustrated → try GRO-751 CSS, revert it, try inline styles...

All of this is wasted effort. The signal: if multiple DIFFERENT CSS fixes all produce the SAME broken result, it's a cache problem, not a CSS problem. Check `cf-cache-status` FIRST before writing a second CSS fix.
