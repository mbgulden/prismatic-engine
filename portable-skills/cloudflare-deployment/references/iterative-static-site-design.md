# Iterative Static Site Design — AGY → Build → Deploy → Review

## The Pattern

Four-phase loop for building and refining static landing pages on Cloudflare Pages:

```
1. AGY DESIGN BRIEF → visual identity, colors, typography, wireframes, CSS snippets
2. FRED BUILD → single-file static HTML with embedded CSS, FormSubmit, Google Fonts
3. DEPLOY → git push → wrangler pages deploy → custom domain + SSL via API
4. AGY REVIEW → browse live URL, brutally honest design review with specific CSS/HTML fixes
   ↳ Loop back to step 2 with fixes applied
```

## Phase 1: AGY Design Brief

`/goal Design the [domain] landing page visual identity. Full design brief with: color palette (hex codes), typography (Google Fonts), homepage layout (ASCII wireframe for hero, feature sections, CTA, footer), mobile responsive plan, iconography direction, and 3-5 headline options. Output as structured design brief markdown. This will be handed to Fred to build as static HTML/CSS.`

## Phase 2: Build

Single-file HTML. No build step — instant CF Pages deploy. Key inclusions:
- Embedded CSS with CSS custom properties (`:root` variables)
- Google Fonts via CDN `<link>`
- FormSubmit.co contact form (action="https://formsubmit.co/email")
- Mobile responsive with media queries
- Semantic HTML sections with IDs for anchor links

## Phase 3: Deploy

```bash
# Create project
CLOUDFLARE_EMAIL="..." CLOUDFLARE_API_KEY="..." \
  npx wrangler pages project create <name> --production-branch=main

# Deploy
CLOUDFLARE_EMAIL="..." CLOUDFLARE_API_KEY="..." \
  npx wrangler pages deploy . --project-name=<name> --branch=main --commit-dirty=true

# Custom domain (see cf-pages-domain-management.md for full API flow)
# 1. Add domain to project via REST API
# 2. Create DNS CNAME → <project>.pages.dev (proxied=true)
# 3. Remove/re-add domain to trigger verification
# 4. Poll until status=active, verification=active, ssl=active
```

## Phase 4: AGY Design Review

`/goal Review the [name] landing page at [live URL] — give me a brutally honest design review. Evaluate visual hierarchy, typography, color usage, spacing, interactivity, mobile responsiveness, missing elements, and overall vibe. For each issue: what is wrong, why it matters, and a specific CSS/HTML fix.`

AGY will produce a structured review with:
- Executive summary and vibe check
- Per-issue breakdown: problem → why it matters → exact code fix
- Priority matrix (High/Medium/Low)
- Missing element recommendations (VU meters, patch matrices, etc.)

Apply the HIGH and MEDIUM fixes in a batch, redeploy, and verify.

## Common Iterations

| AGY feedback example | Fix applied |
|---|---|
| "Bubbly consumer app font" | Swap Outfit → Geist + Geist Mono |
| "2013 Flat UI colors" | Recalibrate to phosphor neon HSL values |
| "Floaty SaaS cards" | Sharp 4px borders, corner screw indicators, denser grid |
| "Missing telemetry" | Add LED VU meters to agent cards |
| "Rotated text arrow hack" | Replace with vertical connector lines on mobile |
| "Static, unconvincing" | Add patch matrix, signal router, or other interactive components |

## Cache-Busting (CRITICAL)

**When you replace an image but keep the same filename, browsers serve the old cached version.** The deploy succeeds, curl shows the new file, but users see stale assets.

Fix: Append version query strings to image `src` attributes:
```html
<img src="hero.png?v=2">
<img src="logo.png?v=2">
```

Increment the version on every asset change. Do NOT rely on CF Pages cache invalidation — browser caches are aggressive with images.

Proven: prismaticengine.com logo v2 and hero v2 were invisible until `?v=2` was added. The files were correct on the server, curl confirmed 200 with the right content-length, but browsers showed old cached versions.

## When to Use This vs. Consulting Site Deployment Pattern

| Pattern | Use when |
|---|---|
| **This pattern** | Static HTML on CF Pages with wrangler deploy |
| **consulting-site-deployment-pattern** | Tunnel-based serving from local python http.server |

Prefer this pattern for public landing pages. Use the consulting pattern for private/internal sites behind Cloudflare Access.
