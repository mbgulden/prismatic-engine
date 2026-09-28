# Flywheel Staging as CSS/JS Source of Truth

When a static mirror doesn't visually match the original WordPress site, the Flywheel staging URL is the authoritative CSS/JS source — NOT git history, NOT the static output directory.

## The Pattern

1. **User points at Flywheel** — They say "compare against this: https://activeoahutours2.flywheelstaging.com"
2. **Fetch the live source** — `curl` the homepage HTML and grep the CSS/JS links
3. **Download every CSS/JS from Flywheel** — `curl` each linked file to the mirror at the same path
4. **Update head.html** — Add the exact same `<link>` tags (use root-relative paths, not absolute Flywheel URLs)
5. **Check for JS-driven features** — Weglot renders the language switcher via JS; don't add manual HTML
6. **Propagate to all baked pages** — Inject CSS links and version suffixes into every HTML file
7. **Verify with Playwright** — Desktop + mobile + mobile-open screenshots of all three: Flywheel, production, staging

## Flywheel CSS/JS Checklist (AOT-specific, adapt for other WP sites)

```bash
# 1. Fetch homepage source
curl -s "https://activeoahutours2.flywheelstaging.com/" -o /tmp/flywheel.html

# 2. Extract all CSS/JS links
grep -oP '<link[^>]*(?:css|stylesheet|font)[^>]*>' /tmp/flywheel.html

# 3. Common missing files on static mirrors:
#    - Weglot CSS: front-css.css + new-flags.css (~152KB)
#    - Google Fonts: Lato + Open Sans Condensed
#    - style.css with WordPress version suffix (?ver=1743021094)
#    - Kadence blocks with version suffixes (?ver=3.7.5)
#    - Weglot JS: front-js.js

# 4. Download each to the mirror at the matching path
for css in front-css.css new-flags.css; do
  curl -s "https://activeoahutours2.flywheelstaging.com/wp-content/plugins/weglot/dist/css/${css}?ver=5.5" \
    -o "site/wp-content/plugins/weglot/dist/css/${css}"
done
```

## Critical Gotcha: Weglot JS vs Manual lang-switcher

The Flywheel site uses Weglot JavaScript to render the language switcher. There is NO manual `<span class="lang-switcher">` in the HTML. Adding one alongside the Weglot JS creates duplicate/conflicting language switchers.

**Check:** `grep 'lang-switcher' /tmp/flywheel.html` — if it returns nothing, the switcher is JS-only. Don't add manual HTML.

**The Weglot JS** (`front-js.js`) reads the `weglot-data` JSON in the `<head>` and injects the switcher into the DOM. Ensure this script is linked and the `weglot-data` block is present.

## Version Suffixes Matter

Flywheel serves CSS with WordPress-generated version suffixes (`?ver=3.7.5`, `?ver=1743021094`). These serve as cache busters. The static mirror should use the same suffixes — they communicate "this is the same CSS as the Flywheel site."

## Verify: Three-Way Playwright Comparison

After matching, capture all three sites at desktop + mobile + mobile-open:

```bash
cd ${PRISMATIC_HOME}/work/hd-bodygraph && \
LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const urls = {
    'flywheel': 'https://activeoahutours2.flywheelstaging.com',
    'production': 'https://activeoahutours.com/?v=N',
    'staging': 'https://staging.active-oahu-tours-mirror.pages.dev/?v=N'
  };
  for (const [name, url] of Object.entries(urls)) {
    const page = await browser.newPage();
    // Desktop
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(2000);
    await page.screenshot({ path: '/tmp/' + name + '-desktop.png' });
    // Mobile collapsed
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: '/tmp/' + name + '-mobile.png' });
    // Mobile expanded
    const toggle = await page.locator('.menu-toggle').first();
    if (await toggle.isVisible().catch(() => false)) {
      await toggle.click().catch(() => {});
      await page.waitForTimeout(1000);
    }
    await page.screenshot({ path: '/tmp/' + name + '-mobile-open.png' });
    await page.close();
  }
  await browser.close();
})().catch(e => console.error(e.message));
"
# SIGABRT on cleanup is expected — check files exist, not exit code
```

## Nav HTML Classes Must Match Flywheel EXACTLY (CRITICAL)

Downloading the CSS files is not enough — the nav HTML structure and class names must also match Flywheel exactly. The theme CSS uses specific WordPress nav walker class selectors. If the mirror nav uses different classes, the CSS rules won't match and the nav will break — especially on mobile.

### Required Nav Classes (WordPress nav walker)

```html
<!-- CORRECT — matches theme CSS selectors -->
<nav id="site-navigation" class="main-navigation" role="navigation" data-instant>
  <button class="menu-toggle" aria-controls="primary-menu" aria-expanded="false">Main Menu</button>
  <div class="menu-menu-1-container">
    <ul id="primary-menu" class="menu">
      <li class="menu-item menu-item-has-children">
        <a href="/activities.html">Activities &amp; Tours</a>
        <ul class="sub-menu">
          <li class="menu-item"><a href="/activities.html">All Tours</a></li>
        </ul>
      </li>
    </ul>
  </div>
</nav>

<!-- WRONG — theme CSS selectors won't match -->
<nav class="navbar" id="navbar-scroll">
  <div class="main-navigation" id="site-navigation">
    <button class="menu-toggle">Main Menu</button>
    <ul id="primary-menu">  <!-- missing class="menu" -->
      <li class="menu-item-has-children">  <!-- missing class="menu-item" -->
```

### Common Mismatches to Check

| Element | Must Use | Common Wrong Value |
|---------|----------|-------------------|
| Nav wrapper | `<nav class="main-navigation">` | `<nav class="navbar">` or extra wrapper divs |
| Menu container | `<div class="menu-menu-1-container">` | Missing entirely |
| UL element | `class="menu"` on `<ul>` | Bare `<ul>` without `menu` class |
| LI elements | `class="menu-item menu-item-has-children"` | Bare `<li>` or just `menu-item-has-children` |
| Sub-menus | `<ul class="sub-menu">` | Correct but needs parent `menu-item-has-children` |
| Book button | Inside `<div class="social-links">` | Custom `<a class="btn-book">` without wrapper |
| Header wrapper | `<header class="clearfix">` | Bare `<section>` without `<header>` |

### Detection

```bash
# On the mirror page — do WordPress classes exist?
curl -s 'https://staging.../page/?key=...' | grep -c 'menu-menu-1-container'
# Should return 1. If 0, the nav HTML doesn't match Flywheel.

# Compare against Flywheel source of truth:
curl -s 'https://REFERENCE.flywheelstaging.com/page/' | grep -c 'menu-menu-1-container'
```

### The Social-Links Wrapper

Flywheel wraps the book button in `<div class="social-links">`, not as a standalone link:

```html
<!-- CORRECT — Flywheel structure -->
<div class="social-header">
  <h3 class="social-header-h3"><span class="feature">(808)498-1894</span></h3>
  <div class="social-links">
    <a href="..." class="pull-right btn btn-small btn-primary">
      <strong><span class="glyphicon glyphicon-calendar"></span> Book Online</strong>
    </a>
  </div>
</div>
```

The theme CSS at `@media (max-width:447px)` targets `.social-header` with `display:flex; flex-flow:column; align-items:center` — this layout only works when the inner structure matches.

### Mobile Toggle Click Interception

When the `social-header` div uses `float:right` or flex layout on mobile, it can physically cover the `.menu-toggle` button, making it unclickable. Playwright reports: `element is visible, enabled and stable — <div class=\"social-header\"> intercepts pointer events`. 

**Fix:** Ensure the Flywheel-exact header structure is used — `<header class=\"clearfix\">` wrapping both `<section id=\"branding\">` and `<nav class=\"main-navigation\">`. The theme CSS at `@media (max-width:447px)` sets `.navbar{clear:both}` which moves the nav below the social-header naturally when the classes match.

## Pitfalls

- **Nav HTML classes are as important as CSS files** — the theme CSS uses WordPress nav walker selectors. Custom class names on `<nav>`, `<ul>`, or `<li>` elements will silently break the mobile nav because the CSS rules don't match. Always `curl | grep 'menu-menu-1-container'` on the mirror page to verify.
- **Don't use git history as the source of truth** — commits may have been force-pushed, cherry-picked, or reverted. The Flywheel staging site is the live, working reference.
- **Don't add manual lang-switcher HTML when Weglot JS is present** — it creates a duplicate switcher and confuses the visual comparison.
- **Don't claim "done" until you've compared against the user's stated reference** — if they pointed you at a Flywheel URL, verify against that URL specifically.
- **Google Fonts link is essential** — the `Lato` and `Open Sans Condensed` fonts change the entire typographic feel. Without them, the nav looks heavier and slightly "off."
- **Weglot CSS is 152KB for a reason** — it contains flag sprites, dropdown styles, and responsive rules. The checkbox fix (replacing with manual links) should be a LAST RESORT, not the first approach.
