# Standalone Nav Rebuild — GRO-712 Pattern

## When to Use

When the static mirror's WordPress-dependent nav has been broken for multiple sessions despite CSS/JS patches. The #1 symptom: the hamburger toggle doesn't work on real mobile devices (Safari, Chrome iOS) despite looking fine in Playwright screenshots.

## The Anti-Pattern (Don't Do This)

Patching the WP-scraped nav with inline CSS overrides, fixing one JS conflict at a time, adding `!important` rules. This is whack-a-mole — WordPress navigation.js, jQuery, and Kadence theme JS have complex interactions that can't be replicated in static HTML.

In June 2026, 6+ hours were spent on CSS patches (nav-fix.css, brand-overrides.css, inline style blocks, Kadence version matching, Weglot flag fixes). AGY found the root cause in 30 seconds: two JS scripts fighting over `.toggled`.

## The Pattern (Do This)

**Check Linear first.** There may already be a properly scoped issue with a better approach (e.g., GRO-712: "Full nav re-vamp — desktop + mobile redesign").

**Rebuild as a standalone component.** Pure HTML/CSS/vanilla JS — zero WordPress dependencies:
- No jQuery, no Kadence navigation.js, no Weglot JS
- Self-contained in one file for testing
- Extract the reference site's computed styles (Playwright `getComputedStyle`) before writing any CSS
- Use AGY for visual QA only — NOT for building from scratch

**The deliberate AGY loop:** Fred builds → AGY compares against reference screenshots + live site → AGY produces structured review with prioritized fixes → Fred applies all fixes → repeat (6+ rounds).

## Key Pitfalls Discovered (Jun 2026)

1. **Safari mobile toggle:** Need `e.preventDefault()` + `e.stopPropagation()` + `-webkit-tap-highlight-color: transparent` for iOS Safari/Chrome
2. **Phone color cascade:** Old Kadence CSS overrides inline styles — use `!important` on `.feature` and `::before` pseudo-elements
3. **Mobile sub-menus:** Show only when `#site-navigation.toggled` is active (don't show sub-menus by default — breaks layout)
4. **Content padding:** Bootstrap `.container-fluid` adds 15px left/right padding on mobile — override with `.container-fluid{padding:0}` + `.site-content{padding:0!important}`
5. **AGY md-in-prompt bug:** Don't use markdown formatting in `--print` argument — write instructions to a plain text file instead
6. **Computed styles over guessing:** CTA button was `#ff6600` not `#ff7f00`; padding was `9px 15px` not `14px 18px`; sub-menu bg was white not dark blue
