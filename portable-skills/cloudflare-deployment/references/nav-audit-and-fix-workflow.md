# Nav Audit & Fix Workflow (Static Mirror)

Proven pattern for auditing and fixing navigation on a static mirror site deployed to Cloudflare Pages.

## When to Use

- User says "the nav on X needs to match the nav on Y"
- Mobile nav is jumbled/overlapping when expanded
- Tablet widths show cramped desktop nav
- Dropdown arrows/sub-menus aren't rendering correctly

## Workflow (7 Steps)

### 0. MATCH THE SOURCE CSS FIRST (CRITICAL — before ANY fixes)

**The WordPress staging site (Flywheel) is the source of truth for CSS.** When the mirror nav doesn't match, the first step is NOT to add overrides, NOT to iterate through git commits, NOT to have AGY audit. It's to fetch the CSS directly from the live WordPress staging site and make the mirror match it EXACTLY.

```bash
# Step 0a: Fetch the staging site source
curl -s "https://STAGING.flywheelstaging.com/" -o /tmp/staging-source.html

# Step 0b: Extract ALL CSS links from staging
grep -oP "<link[^>]*stylesheet[^>]*>" /tmp/staging-source.html

# Step 0c: Download each CSS file into the mirror
curl -s "STAGING_URL/wp-content/themes/theme/css/style.css?ver=XXXX" \
  -o site/wp-content/themes/theme/css/style.css
curl -s "STAGING_URL/wp-content/plugins/weglot/dist/css/front-css.css?ver=X.X" \
  -o site/wp-content/plugins/weglot/dist/css/front-css.css
# ... repeat for each CSS file found in step 0b

# Step 0d: Match CSS links in templates EXACTLY (same order, same versions, same IDs)
# Step 0e: Match JS files too — Weglot front-js.js, SVG support, etc.
```

**Why this matters:** In June 2026, 11+ commits of nav-fix.css and AGY audits failed because the mirror was missing 152KB of Weglot CSS files (`front-css.css`, `new-flags.css`), Google Fonts, and had different Kadence CSS versions than the staging site. The fix took 2 minutes once the source CSS was fetched directly from Flywheel. Every previous attempt — git reverts, AGY surgical audits, inline style stripping — was solving the wrong problem.

**Key files commonly missing from static mirrors:**
- Weglot CSS (`front-css.css`, `new-flags.css`) — 152KB total, handles language switcher
- Google Fonts links (`Lato`, `Open Sans Condensed`)
- Kadence block CSS with correct version suffixes (`?ver=3.7.5`)
- WordPress plugin CSS (`svg-support`, `job-board-manager`, etc.)
- The main `style.css` file itself (mirror may have a different version than staging)

**Also check JS files:** Weglot's `front-js.js` handles the language switcher rendering. Without it, any manual `<span class="lang-switcher">` HTML added to the template won't match the staging site behavior.

**Pitfall — manual lang-switcher HTML:** When Weglot JS is missing, the temptation is to add manual language switcher HTML. DON'T. This creates a different structure than the original site. If Weglot CSS/JS are present, the JS renders the switcher automatically via the Weglot data in the `<head>`. Match the source, don't recreate it.

### 1. Playwright Screenshots (5 states)

**Quick local preview (no deploy needed):** Use `wkhtmltoimage` against a local Python HTTP server for fast iteration before pushing:
```bash
# Start local server (background)
cd /path/to/site && python3 -m http.server 8091 --bind 127.0.0.1 &
# Take screenshots
wkhtmltoimage --width 1200 --height 900 http://127.0.0.1:8091/index.html /tmp/desktop.png
wkhtmltoimage --width 390 --height 844 http://127.0.0.1:8091/index.html /tmp/mobile.png
```
This avoids waiting for CF Pages deploy + CDN cache propagation between each CSS iteration. For interactive testing (clicking hamburger, dropdowns), use Playwright.

**Full Playwright capture:**

```bash
cd /home/ubuntu/work/hd-bodygraph && \
LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  // Desktop 1280px, Mobile 390px (collapsed + expanded), Tablet 768px
  // Capture each at the live URL with waitForTimeout(1500-2000)
})();
"
```

Capture: desktop (1280×900), mobile collapsed (390×844), mobile expanded (click `.menu-toggle`), tablet collapsed (768×1024), tablet expanded. The SIGABRT on cleanup is expected — check files exist, not exit code.

### 2. AGY Visual Audit

Send 3-4 screenshots to AGY with a specific prompt listing what to check: dropdown arrows, menu labels, sub-menu indentation, spacing, colors, mobile layout jumble. AGY will produce a report with findings.

### 3. Filter AGY Hallucinations (CRITICAL)

AGY reliably identifies layout/spacing/color issues but UNRELIABLY reads text from screenshots. Verify EVERY text claim:

```bash
# AGY says nav has label X → verify with curl
curl -sL https://activeoahutours.com | grep -o "actual label"
# AGY says character `▾` is hardcoded → grep the template
grep "▾" site/_templates/body_top.html
```

In the June 2026 session, AGY hallucinated: hardcoded unicode arrows, "Book Now" menu item, and different desktop/mobile taxonomy — none of which existed. But AGY correctly identified: cramped tablet layout at 768px, mobile header clutter, missing Japanese font support.

### 4. User Review → EXPLICIT APPROVAL BEFORE PRODUCTION (CRITICAL)

Present filtered findings to the user. Separate confirmed issues from AGY hallucinations. **Get explicit approval before pushing any changes to production.** This is non-negotiable:

- Show staging screenshots first. Let the user compare against production visually.
- If the user says staging looks wrong or the old version was better, **STOP.** Revert staging to the pre-fix baseline. Do not iterate — the user is telling you the original nav was preferred.
- Never use a cache-busting trigger commit to push unapproved nav changes to production. A CDN cache HIT on production may be the USER'S INTENDED STATE — not a bug to fix.
- When the user says "don't push without permission," treat it as a permanent workflow rule, not a one-time ask.

**Real-world failure (June 2026):** CDN cache was serving old nav on production. Hermes triggered a fresh deploy to bust the cache, which pushed 11+ commits of nav-fix.css changes the user had never approved. User had to request a full revert to the cached baseline. The CDN cache was the correct version — not a problem to solve.

### 5. Apply Fixes

**FIRST — Check for missing CSS from the staging static output.** This is the #1 cause of nav mismatches between the mirror and staging. The staging site (`active-oahu-static`) may have CSS files that were generated or added post-scrape and never copied to the mirror repo:

```bash
# Compare CSS files between staging and mirror
diff <(ls /home/ubuntu/work/active-oahu-static/site/wp-content/themes/activeoahu/css/) \
     <(ls /home/ubuntu/work/active-oahu-tours-mirror/site/wp-content/themes/activeoahu/css/)
```

In the June 2026 session, `brand-overrides.css` (GRO-751 Brand Design System, 316 lines) was present in staging but completely missing from the mirror. This file handled: desktop dropdown hover behavior (`left:-999em` → `left:0`), orange accent borders on sub-menus, mobile menu styling at 549px, brand CSS custom properties, and CTA button theming. Without it, the mirror nav looked broken despite having identical HTML structure.

**If missing CSS is found:**
1. Copy the missing file(s) to the mirror at the same path
2. Add the `<link>` tag in `head.html` (after `style.css` so it overrides)
3. Update the breakpoint in the imported CSS if needed (staging may use 549px, mirror should use 1024px)
4. **Remove conflicting inline nav CSS** from `head.html` that you added earlier — let the staging CSS handle it

**Inline CSS conflict pitfall:** When you add inline nav styles to `head.html` (mobile padding, colors, sub-menu display), they will conflict with the staging CSS if it gets added later. The staging CSS uses `!important` specificity. Fix: after copying staging CSS, trim inline nav overrides down to just layout toggle rules (breakpoint, show/hide) + font fixes. Remove any style rules that duplicate what the staging CSS provides.

Typical remaining fixes for static mirror nav issues:
- **Breakpoint**: Change `767px` → `1024px` in JS (`var isDesktop = window.innerWidth > 1023`) AND CSS media queries
- **Mobile header consolidation**: Hide promo banner, phone, book button on mobile. Logo left, hamburger right.
- **Mobile menu polish**: `min-height: 48px`, `padding: 14px 20px`, divider lines, sub-menu indentation (35px/50px)
- **Font fixes**: Add Japanese font stack for language switcher elements
- **Language switcher**: Move into mobile nav drawer with `display:none` default → `display:block !important` on mobile

Template files to modify: `site/_templates/head.html` (CSS + JS), `site/_templates/body_top.html` (nav HTML structure).

### 6. Rebuild & Deploy

Regenerate all HTML pages from templates, version-bust the CSS link (`style.css?v=3`), commit, and push. CF Pages auto-deploys from `main`. The CDN cache (~8.7h TTL) means existing visitors may see stale content — verify with `?nocache=` parameter, not by checking cache status.

```bash
# Rebuild pages
python3 /path/to/generate_pages.py  # or batch template replacement

# Version-bust
find site -name "*.html" -not -path "*/_templates/*" -exec sed -i 's/style.css?v=2/style.css?v=3/g' {} \;

# Deploy
git add -A && git commit -m "nav fix + cache-bust v3" && git push origin main
```

### 6a. Standalone CSS Override Strategy (for minified stylesheets)

When the mirror's main stylesheet is minified (50KB+, single line) and you can't reasonably edit it without introducing regressions:

**Do NOT append patches to the minified file.** Earlier rules buried deep in the file will override your additions via equal or higher specificity, and debugging is nearly impossible without un-minifying the entire stylesheet.

**Instead: create a standalone CSS override file loaded AFTER the main stylesheet.** CSS cascade gives identical-specificity rules in later-loaded files priority, so your rules win without fighting specificity wars.

```
<!-- In head.html, AFTER style.css: -->
<link rel='stylesheet' id='style-style-css' href='/wp-content/themes/theme/css/style.css?v=N' />
<link rel='stylesheet' id='nav-fix-css' href='/wp-content/themes/theme/css/nav-fix.css?v=1' />
```

**Override file structure:** Write clean, readable CSS organized by breakpoint. Group rules:
1. Header/branding area
2. Desktop nav (horizontal bar, dropdowns, hover states)
3. Mobile nav (`@media (max-width: Npx)` — single column, dividers, hamburger)
4. Extra small (`@media (max-width: Npx)` — further compacted)

Use `!important` sparingly — only when the original minified file has `!important` on a conflicting rule. Version-bump both files on deploy to break CDN cache.

**Pitfall — baked pages vs templates:** If pages are fully-generated HTML (not SSI/PHP), updating `_templates/head.html` will NOT rebuild existing pages. You must inject the new `<link>` tag into every generated HTML file. See "Batch Link Injection" below.

### 6b. Batch CSS/JS Link Injection into Baked Pages

When 200+ static HTML pages already exist and you need to add a new CSS or JS `<link>` to all of them:

```bash
# Step 1: Update the version parameter on existing link (cache bust)
cd /path/to/site && find . -name '*.html' -not -path '*/_templates/*' \
  -exec sed -i "s|id='style-style-css' href='...style.css?v=[0-9]*'|id='style-style-css' href='...style.css?v=N+1'|g" {} +

# Step 2: Inject new link AFTER the anchor line (forward slashes must be escaped: \/)
cd /path/to/site && find . -name '*.html' -not -path '*/_templates/*' \
  -exec sed -i "/id='style-style-css' href='\/path\/to\/style.css?v=N+1'/a NEW_LINK_LINE" {} +
```

**Key details:**
- Always exclude `_templates/` from the `find` — those are source templates, not deployable pages
- Verify count: `grep -rl 'new-link.css' . | wc -l` should match the expected page count
- Some pages may lack the anchor line (different page types) — they'll be silently skipped, which is fine
- Always `git log --oneline -3` before injecting to avoid re-applying a fix already committed

## Pitfalls

- **Double pseudo-element conflict (original + override both rendering `☰`):** When the minified stylesheet has `.menu-toggle:before{content:"☰"}` and your override CSS ALSO sets `::before{content:"☰ "}` inside a media query, you might worry about two icons. But the cascade handles this: your override file loads AFTER the original stylesheet, so your `::before` rule (in the mobile media query) naturally overrides the original's `:before` rule. **Do NOT add a global `content: none !important` kill rule** — that kills BOTH hamburgers, leaving only "Main Menu" text with no icon. The user will report "wrong hamburger removed." Trust the cascade; only one hamburger will render.
- **Logo/image squashing from aggressive `max-height`:** When overriding image dimensions, don't set `max-height` smaller than the image's natural height — it'll squash. Keep proportional (e.g., 232×65 logo → use `max-height: 52px` not `35px` on desktop, `max-height: 45px` not `25px` on mobile). Test with actual screenshots.
- **Never trust AGY's text claims from screenshots.** Always grep/curl the actual HTML.
- **Don't rely on `cf-cache-status` header** to verify deploy — use `?nocache=` query param.
- **Playwright SIGABRT** (exit 134) after saving screenshots is expected — check file sizes, not exit codes.
- **The `body_bottom.html` JS** may add dropdown toggles that conflict with mobile CSS. Guard with `isDesktop` check.
- **Japanese text renders as tofu boxes** when font stack lacks CJK fonts. Add `Hiragino Sans, Noto Sans CJK JP` to `.lang-switcher`.
- **Appending CSS to a minified file is a losing battle.** Earlier rules override your additions via specificity. Create a standalone override file loaded after the main stylesheet instead. See "Standalone CSS Override Strategy" above.
- **Template-only edits don't rebuild baked pages.** If pages are fully-generated HTML, updating `_templates/head.html` won't affect existing pages — you must inject changes into every `.html` file. See "Batch Link Injection" above.
- **CDN cache persists across deploys — verify with hash URLs:** The custom domain CDN edge cache can persist for 37+ hours across multiple CF Pages deployments. `cf-cache-status: HIT` with `age: 133274` means you're seeing a version from before the latest deploy. Use the direct deployment hash URL (e.g., `https://<hash>.active-oahu-tours-mirror.pages.dev`) to verify. Query the CF Pages API for the latest deployment URL. Pages API tokens (cfat_) cannot purge zone cache.
- **The CDN cache may be the CORRECT version:** When a user says production looks right, the CDN-cached version IS the intended state. Cache-busting to push unapproved nav changes deploys commits the user never saw. If the user hasn't asked for nav changes, LEAVE THE CACHE ALONE. See step 4.
