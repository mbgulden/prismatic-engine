# Static Mirror Nav Debugging Pattern

When the static mirror nav doesn't match the staging (WordPress) site, follow this diagnostic pipeline in order.

## ⛔ ESCAPE HATCH: After 2 Failed CSS/JS Patch Rounds, STOP PATCHING

**If you've made 2+ rounds of CSS/JS fixes and the nav is still broken, you are in a patch loop.** The static mirror was scraped from a WordPress site with complex JS dependencies (navigation.js, jQuery, Kadence theme hooks, Weglot injection). These were never going to work correctly in static HTML. Continuing to patch is waste.

**Before round 3, check Linear for a properly scoped rebuild plan.** In the Active Oahu Tours case (June 2026), GRO-712 ("Full nav re-vamp: desktop + mobile redesign") had been sitting in Todo for weeks with a complete 7-step AGY-driven pipeline: AGY review → mockups → draft HTML/CSS/JS → Kai integrate → Jules review → AGY vision test → Michael approval. A month of patching was spent on a nav that had a proper rebuild plan already scoped.

**If no rebuild plan exists, create one.** The approach: extract the Flywheel/WordPress nav as a standalone HTML/CSS/JS component with ZERO WordPress dependencies. Use AGY to drive visual QA across all breakpoints (10+ rounds minimum). See `agy-vision-pipeline` → "Nav Rebuild with AGY Visual QA Loop."

**Signs you're in a patch loop:**
- Same component broken in a different way after each fix
- Adding `!important` to override previous `!important` rules
- WordPress theme JS (`navigation.js`) and custom mirror JS canceling each other
- The user expresses frustration about the same component across multiple sessions

## Root Cause Hierarchy (most common first)

1. **Missing CSS files** — staging has CSS that the mirror doesn't
2. **Conflicting CSS rules** — theme CSS and brand overrides fight each other
3. **JS-driven behavior gaps** — WP theme JS not present in static mirror
4. **DUPLICATE JS EVENT HANDLERS** — static mirror adds custom toggle scripts that cancel out WordPress theme JS (BOTH fire on click → class toggled twice = net zero)

## Diagnostic Pipeline

### Step 0: Extract Computed Styles from Reference (BEFORE Any Fixes)

Before guessing at colors, padding, or font sizes, extract the LIVE computed styles from the reference site. CSS files lie — WordPress themes apply inline overrides, JS-applied styles, and inherited properties not obvious from stylesheets. Guessing wrong wastes rounds.

```javascript
// extractComputedStyles.js — save to /tmp and run
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('https://reference-site.com', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);

  const data = await page.evaluate(() => {
    const getStyles = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      return {
        backgroundColor: cs.backgroundColor,
        color: cs.color,
        fontFamily: cs.fontFamily,
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
        padding: cs.padding,
        margin: cs.margin,
        display: cs.display,
        width: cs.width,
        height: cs.height,
        textTransform: cs.textTransform,
        rect: el.getBoundingClientRect()
      };
    };
    return {
      nav: getStyles('#site-navigation'),
      menuToggle: getStyles('.menu-toggle'),
      primaryMenu: getStyles('#primary-menu'),
      ctaButton: getStyles('.btn-primary'),
      branding: getStyles('#branding'),
      logoImg: (() => { const img = document.querySelector('header img'); return img ? { src: img.src, w: img.width, h: img.height } : null; })()
    };
  });
  console.log(JSON.stringify(data, null, 2));
  await browser.close();
})();
```

Run: `cd /tmp && LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH node extractComputedStyles.js > /tmp/computed-styles.json`

**Example findings (AOT Flywheel staging, Jun 2026):**
- CTA button: `rgb(255, 102, 0)` = #ff6600 (not #ff7f00 as guessed)
- Link padding: `9px 15px` (not `14px 18px`)
- Link text color: `#fdf5e3` cream (not `#fff` white)
- Text transform: `none` (not `uppercase`)
- Sub-menu background: `#fff` white (not dark blue)
- Navbar gradient: `linear-gradient(to bottom, #0099a7 0, #007090 81%, #006986 100%)` (not flat #006699)
- Breakpoint: 600px/37.5em (not 1024px)
- All sub-menus visible on mobile open (no accordion collapse)

Without this step, 5+ rounds would have been spent on wrong color/padding guesses.

### Step 1: Diff CSS directories

```bash
diff staging-css-dir/ mirror-css-dir/
```

The #1 cause: a CSS file exists in staging but not in the mirror. In the Active Oahu case, `brand-overrides.css` (GRO-751 Brand Design System) was in `active-oahu-static` but missing from `active-oahu-tours-mirror`.

### Step 2: If staging CSS is a single combined file, use it directly

```bash
cp staging-site/wp-content/themes/activeoahu/css/style.css mirror-site/wp-content/themes/activeoahu/css/style.css
```

The staging may have brand overrides appended directly to `style.css` rather than as a separate file. Using it directly is safer than trying to merge.

### Step 3: Version-bust the CSS link

```bash
# In head.html template
style.css?v=2 → style.css?v=4  # skip numbers to avoid CF edge cache
```

CF Pages CDN caches CSS aggressively (8+ hour TTL). Always bump the version on every CSS change.

### Step 4: Update the mobile breakpoint

If the staging breakpoint is different from what you need:
```bash
# In style.css
@media (max-width: 549px) → @media (max-width: 1023px)
```

### Step 5: Rebuild all pages

Since templates are baked into generated HTML, update all pages:
```bash
find site -name "*.html" -not -path "*/_templates/*" -exec sed -i 's/style.css?v=OLD/style.css?v=NEW/g' {} \;
```

Also remove any conflicting inline CSS and obsolete file links.

### Step 6: Deploy and verify

```bash
git add -A && git commit -m "fix: staging CSS + nav fixes" && git push origin main
# Wait 30s for CF Pages deploy
curl -sL "https://site.com/?nocache=$(date +%s)" | grep -c "v=NEW"
```

## Mobile Nav Conflicts

### Duplicate JS Toggle (CRITICAL — Check This First on Mobile Menu Bugs)

When the hamburger menu doesn't open despite correct CSS, the #1 root cause is duplicate JavaScript event handlers canceling each other out. The static mirror often has BOTH:

1. **WordPress `navigation.js`** — the original theme script that uses `onclick` to toggle `.toggled` class
2. **Custom mirror script** — added during static build, uses `addEventListener('click', ...)` + `classList.toggle('toggled')` 

Both fire on the same click event. The first toggles the class ON, the second toggles it right back OFF — net zero change. The menu never opens.

**Detection:**
```bash
grep -n 'toggled\|menu-toggle\|toggleBtn\|classList.toggle' site/index.html
```
If you see BOTH `var toggleBtn = document.querySelector('.menu-toggle')` (custom) AND `container.className.indexOf('toggled')` (WordPress), you have the duplicate bug.

**Fix:** Remove the CUSTOM script (the `addEventListener` + `classList.toggle` block). Keep the WordPress `navigation.js` — that's what the Flywheel/WordPress staging site uses. The custom script was added as a workaround during mirroring but conflicts with the real navigation.js.

**Apply across all pages:**
```python
import re
for f in html_files:
    content = open(f).read()
    content = re.sub(
        r"\s+var toggleBtn = document\.querySelector\('\.menu-toggle'\).*?toggleBtn\.setAttribute\('aria-expanded', expanded\);\s+\}\);?\s+\}",
        '', content, flags=re.DOTALL
    )
    with open(f, 'w') as fh: fh.write(content)
```

### Original WordPress hamburger conflicts

The original WordPress theme CSS often has `::before` pseudo-elements on `.menu-toggle` that create hamburger icons (`content: "☰"`). When brand overrides also style `.menu-toggle` as a full-width button, you get a "double hamburger" visual.

**Fix:**
```css
.menu-toggle::before { content: none !important; }
.menu-toggle { text-transform: none !important; }  /* "Main Menu" not "MAIN MENU" */
```

## Mobile Header Layout

User preference: logo flush left, phone number right-aligned, compact single-row header.

```css
@media (max-width: 1023px) {
  #branding {
    display: flex !important;
    align-items: center !important;
    justify-content: space-between !important;
  }
  #branding .social-header {
    display: flex !important;
    flex-flow: column !important;
    align-items: flex-end !important;
  }
  #branding .lang-switcher { display: none !important; }
}
```

## AGY Code-Level Audit (Reliable)

AGY is reliable for **code comparison**, not screenshot comparison. To find header/nav differences:

```bash
# Fetch Flywheel HTML, then have AGY compare code
curl -s "https://staging.flywheel.com/" -o /tmp/flywheel.html
agy --dangerously-skip-permissions --print-timeout 300s --add-dir /path/to/mirror --print "
/goal Compare the header and navigation code between Flywheel staging (/tmp/flywheel.html) and the static mirror at /path/to/mirror.
Browse the live Flywheel site, inspect the DOM, and compare against the mirror's site/index.html and site/_templates/.
Look for: duplicate JS handlers, missing CSS files, inline style differences, nav HTML structure mismatches.
Write findings to NAV-AUDIT.md. Reply DONE when finished."
```

AGY browses the live Flywheel site, inspects the DOM, and cross-references against local files. It reliably catches duplicate event handlers, missing CSS, and structural differences that visual comparison misses.

**Do NOT use AGY for screenshot/image comparison** — it times out with zero output. Use code-level comparison instead.

## Template Update Pattern

When templates change (`head.html`, `body_top.html`), use regex replacement on generated files rather than regenerating from scratch:

```python
import re
for f in html_files:
    with open(f) as fh: c = fh.read()
    c = re.sub(r'OLD_PATTERN', 'NEW_CONTENT', c, flags=re.DOTALL)
    with open(f, 'w') as fh: fh.write(c)
```

This is faster than running multiple generator scripts and ensures consistency.

## Git Workflow: Force-Push Template Regression (CRITICAL)

The pattern `git push origin main:staging --force && git reset --hard HEAD~1` causes SILENT template regression. After the first successful commit that includes both `head.html` AND `body_top.html` changes, every subsequent force-push cycle:

1. `git reset --hard HEAD~1` reverts ALL files (including `body_top.html`) to the old main baseline
2. The next `git add -A` only stages files modified in that round (usually just `head.html`)
3. The force-push overwrites staging with a commit where `body_top.html` is the OLD version
4. The language switcher, nav HTML, and any other template changes silently disappear

**Detection:** After 2+ force-push rounds, `git show <staging-commit>:site/_templates/body_top.html` shows old WordPress HTML instead of the new nav template. The production site works but is missing elements (language switcher, proper nav structure).

**Fix — two options:**

**Option A (safer):** Checkout staging branch directly, commit there, don't touch main:
```bash
git checkout staging
# Make template changes
git add -A && git commit -m "fix: templates" && git push origin staging
```

**Option B (current workflow):** After the FIRST commit (which has all template changes), use `git commit --amend` instead of reset+recommit:
```bash
# First commit — has everything
git add -A && git commit -m "feat: v1 nav component" && git push origin main:staging --force

# Subsequent fixes — amend, don't reset
# (edit templates)
git add -A && git commit --amend --no-edit && git push origin main:staging --force
```

**Option C (post-hoc fix):** If templates already regressed, cherry-pick the working commit:
```bash
git show <good-commit>:site/_templates/body_top.html > site/_templates/body_top.html
git add -A && git commit -m "fix: restore body_top.html from <good-commit>" && git push origin main:staging --force
```
