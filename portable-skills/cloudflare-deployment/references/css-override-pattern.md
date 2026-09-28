# Standalone CSS Override Pattern for Static Mirrors

## Problem
A static site mirror has a **minified** main CSS file (single-line, 50KB+). Appending patches to it is unreliable — every added rule fights earlier specificity in the same file and produces unpredictable results.

## Solution
Create a **standalone override file** loaded AFTER the main stylesheet. The CSS cascade guarantees your rules win at equal specificity.

## Pattern

### 1. Create the override CSS
Create `site/wp-content/themes/<theme>/css/nav-fix.css` — clean, readable CSS targeting only the rules you're changing. Use the same selectors as the original for equal-specificity overrides. Use `!important` sparingly; the cascade should do most of the work.

### 2. Link it after the main stylesheet
In `_templates/head.html`, add immediately after the main stylesheet link:
```html
<link rel='stylesheet' href='/wp-content/themes/activeoahu/css/style.css?v=7' type='text/css' media='all' />
<link rel='stylesheet' href='/wp-content/themes/activeoahu/css/nav-fix.css?v=1' type='text/css' media='all' />
```

### 3. Inject into all existing pages
If templates don't auto-rebuild (fully-baked static HTML), inject the new link into every page:
```bash
find site -name '*.html' -not -path '*/_templates/*' -exec sed -i "/style.css?v=[0-9]*'/a <link rel='stylesheet' href='/wp-content/themes/activeoahu/css/nav-fix.css?v=1' type='text/css' media='all' />" {} +
```

### 4. Version-Bump on Every Deploy
CDN caches CSS aggressively (Cloudflare: 8.7-hour TTL default). Bump BOTH `style.css?v=N` and `nav-fix.css?v=N` on every deploy. Update the template AND all generated HTML pages.

## Critical Pitfall: Global `::before` Kill Rules

**NEVER** add a global `content: none !important` rule targeting a pseudo-element to "remove the original version" before adding your own.

Concrete example from Active Oahu Tours:
- Original minified CSS had `.menu-toggle:before { content: "☰" }` (outside any media query)
- Our `nav-fix.css` set `.menu-toggle::before { content: "☰  " }` inside `@media (max-width: 1023px)`
- On mobile, both match. Our rule wins because `nav-fix.css` is loaded AFTER `style.css` — cascade handles it
- Adding a global `content: none !important` on `.menu-toggle::before` kills **both** hamburgers
- Result: user sees "Main Menu" text with no icon, and the **wrong** hamburger (the original's) gets blamed

**The rule**: Trust the cascade. Your override file loads after the original — your `::before` rule naturally overrides the original's. The only global kill you need is `display: none` on the element itself for desktop view.

## Flexbox Header Pattern

For a header with logo flush-left and contact info flush-right:
```css
#branding {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  flex-wrap: wrap !important;
}

.aot-logo {
  max-width: 180px !important;
}

.social-header {
  margin-left: auto !important;  /* pushes to right edge */
  display: flex !important;
  flex-direction: column !important;
  align-items: flex-end !important;
}
```

## Visual Verification

Before reporting done, verify locally:
```bash
# Start server
python3 -m http.server 8094 --bind 127.0.0.1 &
# Desktop screenshot
wkhtmltoimage --width 1200 --height 900 --quality 90 http://127.0.0.1:8094/index.html /tmp/aot-desktop.png
# Mobile screenshot
wkhtmltoimage --width 390 --height 844 --quality 90 http://127.0.0.1:8094/index.html /tmp/aot-mobile.png
```
