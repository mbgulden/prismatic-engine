# WordPress to Static Mirror (Lift-and-Shift)

When the priority is getting off slow WordPress hosting FAST with zero content changes. This is Phase 1a — get the site on fast infrastructure first, then rebuild.

## Why This Approach

Building Astro pages one at a time is slow. A 70+ page WordPress site takes days to rebuild piecemeal. The user explicitly asked for this approach after seeing the piecemeal pace.

**User directive:** "What if we first did a version of a lift and shift and only just copied everything over just how it is currently on the website and not add or subtract any pages and keep everything the same, functionality, images and everything and move it to lightning fast Astro and Cloudflare Pages. That way I get that change done and don't have to worry about a giant shift in everything."

## wget Mirror Command

```bash
# Clean mirror — avoids nested hostname directories
mkdir -p ~/work/site-mirror
cd ~/work/site-mirror
mkdir -p site

wget \
  --mirror \
  --page-requisites \
  --adjust-extension \
  --convert-links \
  --span-hosts \
  --domains=activeoahutours.com,fareharbor.com \
  --exclude-directories=/wp-json,/wp-admin,/cdn-cgi \
  --wait=0.3 \
  --limit-rate=1m \
  --no-parent \
  --no-verbose \
  --no-host-directories \
  --directory-prefix=site \
  https://activeoahutours.com/
```

Key flags:
- `--mirror`: recursive, timestamp-based, infinite depth
- `--page-requisites`: download CSS, JS, images needed by pages
- `--adjust-extension`: add .html to files that are HTML
- `--convert-links`: make links relative for local browsing AND deployment
- `--span-hosts --domains=X,Y`: follow links to FareHarbor (for embeds) but not external sites
- `--exclude-directories`: skip REST API, admin, Cloudflare endpoints
- `--no-host-directories`: **CRITICAL** — prevents nested `activeoahutours.com/activeoahutours.com/` directory structure. Without this flag, wget creates a hostname subdirectory inside the download prefix, resulting in doubled paths.
- `--directory-prefix=site`: all files land in `site/` (clean, flat, ready to commit to repo root)
- `--wait=0.3 --limit-rate=1m`: be polite to the server; 1m rate is safe for most shared hosts

## What You Get

A complete static copy of the WordPress site:
- Every HTML page (including `/activities/`, `/rentals/`, `/blog/`, `/ja/` pages)
- All images from `/wp-content/uploads/`
- All CSS and JS
- FareHarbor embed pages (partial — dynamic booking still loads from FH CDN)
- Internal links converted to relative paths (works on any domain)

## Deployment to Cloudflare Pages

### Option A: Dashboard (Recommended — Always Works)

1. CF Dashboard → Workers & Pages → Create → Pages → Connect to Git
2. Select the mirror repo → set build command empty, output dir `/`
3. Deploy. GitHub pushes auto-deploy.

### Option B: REST API (When wrangler Fails)

`npx wrangler pages deploy` can fail with error 7003 (`Could not route to /client/v4/accounts/.../pages/projects`) even with a valid API token. The REST API works reliably as a fallback:

```bash
# 1. Create the project
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/pages/projects" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-project", "production_branch": "main"}'

# Returns: { "result": { "subdomain": "my-project.pages.dev", ... } }

# 2. Connect GitHub (one-time)
curl -s -X PATCH "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/pages/projects/my-project" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "github",
      "config": {
        "owner": "mbgulden",
        "repo_name": "my-project",
        "production_branch": "main",
        "deployments_enabled": true
      }
    },
    "deployment_configs": {
      "production": {
        "build_command": "",
        "destination_dir": "/",
        "root_dir": "/"
      }
    }
  }'
```

**Token requirements:** Cloudflare API token with `Cloudflare Pages — Edit` permission. Created at `dash.cloudflare.com/profile/api-tokens`.

**Account ID:** Found at `dash.cloudflare.com` → select account → URL shows the ID. Also retrievable via `curl https://api.cloudflare.com/client/v4/accounts -H "Authorization: Bearer ${TOKEN}"`.

**Note:** The GitHub connection through the API may not trigger an immediate deploy. If 0 deployments appear, connect Git through the dashboard (Settings → Build & Deploy → Git Repository). This is a one-click operation.

## What Breaks (and Acceptable Tradeoffs)

| Breaks | Acceptable? |
|---|---|
| WordPress search | Yes — the site doesn't use it heavily |
| WordPress comments | Yes — no comments on this site |
| WP admin/backend | By design — this is the point |
| Dynamic forms (Contact Form 7) | Maybe — check if form is critical. Replace with Formspree or similar. |
| FareHarbor booking | Works — embeds are JS-based and load from FH CDN |

## What to Check After Mirror

### 1. The Spider Misses Pages — Use WP REST API Discovery

wget only follows links it finds. Pages not linked from navigation, sitemaps, or other crawled pages will be missed. **After every mirror, run a REST API discovery pass to find the gaps:**

```python
# Pull ALL published pages, posts, and custom post types from WP REST API
import urllib.request, json

BASE = "https://site.com"
all_urls = set()

# Pages
for page in range(1, 10):
    url = f"{BASE}/wp-json/wp/v2/pages?per_page=100&page={page}&status=publish&_fields=link"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            pages = json.loads(resp.read())
            if not pages: break
            for p in pages:
                if p.get('link'): all_urls.add(p['link'])
    except Exception as e: break

# Repeat for posts (/wp/v2/posts), rentals (/wp/v2/rentals), etc.
# Custom post types found via: curl -sL https://site.com/wp-json/wp/v2/types
```

Then diff REST API URLs against mirrored files. For each missing page, fetch individually:

```bash
mkdir -p <path>
wget -q --adjust-extension --convert-links \
  --domains=site.com -O <path>/index.html <url>
```

### 2. `--convert-links` Is Not Perfect — Post-Mirror Cleanup Pipeline Required

wget's `--convert-links` catches most but not all absolute URLs. After mirroring, run this cleanup pipeline in order:

#### Step A: Strip remaining absolute domain references

```bash
cd mirror-dir
# Convert ALL remaining absolute references to relative
find . -name "*.html" -exec sed -i 's|https://site.com/|/|g; s|http://site.com/|/|g' {} \;
# Verify
grep -r "https://site.com" --include="*.html" -l . | wc -l  # should be 0
```

#### Step B: Fix Cloudflare CDN Image Proxy URLs

WordPress sites behind Cloudflare use `//cdn-cgi/image/quality=80,format=auto,onerror=redirect,metadata=none/wp-content/uploads/...` URLs for optimized images. These are CF-specific and won't work on CF Pages. Strip the CDN proxy prefix:

```bash
find . -name "*.html" -exec sed -i 's|//cdn-cgi/image/[^/]*/wp-content/|/wp-content/|g' {} \;
```

After stripping, download the actual images from the live WP site if they're not already in the mirror.

#### Step C: Fix Cloudflare Email Obfuscation

CF's email protection encodes emails as `//cdn-cgi/l/email-protection#<hex>`. On static hosting, this JS doesn't run. Replace with the real email:

```bash
# Get the real email from the WP site's contact page
# Then replace all obfuscated references
find . -name "*.html" -exec sed -i 's|//cdn-cgi/l/email-protection[^"]*|mailto:aloha@activeoahutours.com|g' {} \;
```

#### Step D: Normalize mangled relative paths

Pages in deep subdirectories sometimes reference assets with `../../../../wp-content/plugins/...` paths. These don't resolve correctly after mirroring. Normalize to root-relative:

```bash
find . -name "*.html" -exec sed -i \
  -e 's|\.\./\.\./\.\./\.\./wp-content/|/wp-content/|g' \
  -e 's|\.\./\.\./\.\./wp-content/|/wp-content/|g' \
  -e 's|\.\./\.\./wp-content/|/wp-content/|g' \
  -e 's|\.\./wp-content/|/wp-content/|g' {} \;
```

#### Step E: Download missing WP plugin assets

Pages may reference plugin JS/CSS that wget didn't download (Gravity Forms, Weglot, SVG Support). These are needed for contact forms and language switchers to work:

```bash
# Find all wp-content asset references and download what's missing
find . -name "*.html" -exec grep -oP 'src="([^"]*wp-content/[^"]*)"' {} \; | \
  sed 's/src="//;s/"//' | sort -u | while read src; do
  path=$(echo "$src" | grep -oP 'wp-content/[^?"'\'']*')
  if [ -n "$path" ] && [ ! -f "./$path" ]; then
    mkdir -p "$(dirname "./$path")"
    wget -q -O "./$path" "https://site.com/$path" 2>/dev/null
  fi
done
```

#### Step F: Remove dead WP metadata links

WP header metadata (`wp-json`, `shortlink`, `wlwmanifest`, `rsd`, RSS/Atom feeds) are dead on static hosting. Strip them:

```bash
find . -name "*.html" -exec sed -i \
  -e 's|<link[^>]*wp-json/oembed[^>]*>||g' \
  -e 's|<link[^>]*rest_output_link[^>]*>||g' \
  -e 's|<link[^>]*shortlink[^>]*>||g' \
  -e 's|<link[^>]*wlwmanifest[^>]*>||g' \
  -e 's|<link[^>]*rsd[^>]*>||g' \
  -e 's|<link[^>]*alternate.*feed[^>]*>||g' {} \;
```

#### Step G: Download missing WP core JS

WordPress core JavaScript (jQuery, i18n, a11y, hooks) may not be downloaded by wget. Check and fetch:

```bash
for js in wp-includes/js/jquery/jquery.min.js \
          wp-includes/js/jquery/jquery-migrate.min.js \
          wp-includes/js/dist/a11y.min.js \
          wp-includes/js/dist/hooks.min.js \
          wp-includes/js/dist/i18n.min.js \
          wp-includes/js/dist/dom-ready.min.js \
          wp-includes/js/imagesloaded.min.js \
          wp-includes/js/masonry.min.js; do
  mkdir -p "$(dirname "$js")"
  [ ! -f "$js" ] && wget -q -O "$js" "https://site.com/$js" 2>/dev/null
done
```

#### Step H: Verify ZERO missing assets

```python
import os, re
missing = set()
for root, dirs, files in os.walk('.'):
    for f in files:
        if f.endswith('.html') and 'wp-' not in root.split('/')[1:2]:
            with open(os.path.join(root, f), 'r', errors='ignore') as fh:
                content = fh.read()
            for src in re.findall(r'src="(/[^"]*)"', content):
                if not src.startswith(('http', 'data:')):
                    clean = src.split('?')[0].split('#')[0]
                    if not os.path.exists('.' + clean):
                        missing.add(clean)
print(f"Missing assets: {len(missing)}")  # Must be 0 before deploying
```

**Why this matters:** If the DNS switches before all cleanup is done, images break, emails don't work, forms fail, and JS errors appear. The cleanup pipeline is NOT optional — a raw wget mirror always has these issues.

### 3. Verify High-Traffic Pages Are Present

From Search Console data, identify the top 10 pages by clicks. Verify every one exists in the mirror before deploying. If any are missing, fetch them manually. Do NOT deploy with missing revenue pages.

### 4. GTM/GA4 — Single Source of Truth

Check if the WP site loads GA4 via GTM or directly. If GTM manages it, remove any direct GA4 gtag snippets from the mirror. Double-firing inflates metrics. The GTM container should be the only tag manager:

```html
<!-- GTM (manages GA4 + Ads conversions) -->
<script>(function(w,d,s,l,i){...})(window,document,'script','dataLayer','GTM-XXXXX');</script>
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-XXXXX" ...></iframe></noscript>
```

### 5. WP-Specific Cruft

Remove WP admin bar CSS, REST API `<link>` tags, shortlink tags, and oembed references. These are dead weight on a static site but don't break anything if left in. The sed pass from step 2 handles most of them naturally.

### 6. Compress Oversized Images for CF Pages 25MB Limit

**This is NOT optional.** Cloudflare Pages rejects files over 25 MiB. WordPress sites with high-res photography (especially from NAS uploads or professional cameras) routinely have images at 30-40MB+. Mirror → compress → THEN deploy.

**Detection:** If the CF Pages build fails with `Error: Pages only supports files up to 25 MiB in size` and lists specific files, those are the culprits. Check build logs via the deployment history logs endpoint.

**Michael's preference:** Images should be resized to **max 1920px wide** and compressed to **~300KB** per image, not just CF-compliant. Use binary search on quality to hit the target size exactly — don't just pick a fixed quality value. Web images at 300KB load fast on mobile while retaining quality for hero/feature images.

**Fix with Pillow (Python) — binary search quality for 300KB target:**

```python
from PIL import Image
import os

TARGET_KB = 300
MAX_WIDTH = 1920

for root, dirs, files in os.walk('.'):
    for f in files:
        if f.lower().endswith(('.jpg', '.jpeg', '.png')) and '.git' not in root:
            path = os.path.join(root, f)
            img = Image.open(path)
            w, h = img.size
            
            # Resize to max width if needed
            if w > MAX_WIDTH:
                ratio = MAX_WIDTH / w
                img = img.resize((MAX_WIDTH, int(h * ratio)), Image.LANCZOS)
                print(f"  Resized to {img.size}")
            
            # Binary search for quality hitting target KB
            lo, hi = 10, 95
            best_q, best_size = 75, None
            while lo <= hi:
                q = (lo + hi) // 2
                tmp = path + ".tmp"
                img.save(tmp, "JPEG", quality=q, optimize=True)
                kb = os.path.getsize(tmp) / 1024
                os.remove(tmp)
                if kb <= TARGET_KB:
                    best_q, best_size = q, kb
                    lo = q + 1
                else:
                    hi = q - 1
            
            if best_size:
                img.save(path, "JPEG", quality=best_q, optimize=True)
                print(f"{f}: {w}x{h} → quality={best_q}, {best_size:.0f}KB")
```

**CRITICAL — Clean up `.tmp` files:** The binary search creates temporary files. Always delete them before committing:
```bash
find . -name "*.tmp" -delete
```
Accidentally committing `.tmp` files to the repo may cause CF Pages deployment failures or bloat.

**Real example:** Two e-bike images at 8192x5400px (33-39MB) → resized to 1920px, quality 53-59 → 299-300KB each. Total savings: ~70MB → ~600KB.

**Note:** `convert` (ImageMagick) sometimes fails to reduce JPEG file sizes even with `-quality`. Pillow's `save(optimize=True, quality=N)` is reliable. Always verify with a post-compression size check.

**Proactive approach:** After the mirror and before deploying, run compression on ALL images >1MB to the 300KB/1920px target. CF Pages checks every file on every deploy, and smaller images improve PageSpeed scores.

### 6b. Fix Corrupted Filenames from WordPress Scrape (MANDATORY)

The wget mirror produces three categories of corrupted filenames that cause CF Pages builds to fail silently. See `references/wp-mirror-filename-cleanup.md` for the full fix scripts.

**Quick detection + fix:**
```bash
# 1. Delete query-string artifact files (?ver= duplicates)
find . -type f -name '*\?*' -delete

# 2. Rename unicode thin-space files (macOS screenshots)
python3 -c "
import os
for r,d,f in os.walk('.'):
    for n in f:
        if '\u202f' in n:
            os.rename(os.path.join(r,n), os.path.join(r,n.replace('\u202f','-')))
"

# 3. Fix leading/trailing spaces in filenames
python3 -c "
import os
for r,d,f in os.walk('.'):
    for n in f:
        s = n.strip()
        if s != n:
            os.rename(os.path.join(r,n), os.path.join(r,s))
"

# 4. Verify clean
find . -type f -name '*[^a-zA-Z0-9._/-]*'
# Should return nothing
```

**Symptom if skipped:** CF Pages deploys return HTTP 200 but serve stale content (wrong page titles, old canonical URLs). The build fails asset validation silently and falls back to the last successful deploy.

### 7. Placeholder Data

Check for placeholder phone numbers (`555-1234`), masked emails, fake addresses. Get the real values from the live WP site's HTML or the user:

```bash
# Extract real phone from WP site
curl -sL https://site.com/contact-us/ | grep -oiP "tel:[\+\d\-\(\) ]{7,20}" | head -3
```

### 7. CF Pages Configuration Files

Add `_headers` for caching and security, and `_redirects` for trailing-slash normalization:

```bash
# _headers
cat > _headers << 'EOF'
/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
/*.html
  Cache-Control: public, max-age=3600
/*.jpg
  Cache-Control: public, max-age=604800, immutable
EOF

# _redirects (trailing slash normalization)
cat > _redirects << 'EOF'
/activities  /activities/  301
/rentals    /rentals/    301
/contact-us  /contact-us/  301
EOF
```

### 8. Phone Number — Get It Right First Time

The user WILL correct you if the phone number is wrong. Don't guess. Extract from the live site or ask. Today's correction: `808-724-1218` (wrong, from outdated WP meta) → `808-498-1894` (correct). The placeholder `(808) 555-1234` is always wrong — never ship it.

## Migration Tracking

Always create a Linear project for the static mirror with these tasks:
1. Mirror WP site with wget
2. Clean up static HTML
3. Create CF Pages project
4. Deploy to CF Pages
5. Test pages end-to-end
6. Verify GA4/GTM tracking
7. Verify Search Console ownership
8. DNS switch
9. Post-migration audit (crawl, compare against sitemap)

## When to Use This vs. Astro Rebuild

- **Static mirror first** when: the site has 50+ pages, user wants off WP this week, content won't change during the switch.
- **Astro rebuild** when: major content/design changes planned, need CMS-like editing, want cleaner codebase.
- **Both** (recommended): static mirror as Phase 1a (get fast), Astro rebuild as Phase 1b (get clean). The mirror buys time to do the rebuid properly.
