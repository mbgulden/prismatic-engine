# Website Rebuild: Astro + Cloudflare Pages

Template for researching and planning a static site rebuild (WordPress → Astro + Cloudflare Pages).

## Research Checklist

### 1. Stack Compatibility
- **Astro + Cloudflare Pages**: First-class support, official adapter. Static site generation (SSG) recommended.
- **Deployment**: Push to GitHub → Cloudflare Pages auto-deploys from repo. Custom domains via Cloudflare DNS.
- **Cost**: Free tier — unlimited sites, unlimited requests, 500 builds/month.

### 2. Existing Integrations
- **Booking widgets** (FareHarbor, Lockii): Work as client-side JS embeds in any static framework. Paste the snippet into an Astro component.
- **Payment processing** (Stripe): Client-side Stripe.js or Stripe Checkout links work unchanged.
- **Smart lock integration** (Igloohome): Happens through FareHarbor's booking system, NOT the website. No website change needed.
- **AI customer support**: Separate service, embedded via chat widget or API.

### 3. Migration Path
1. **Extract content** from WordPress: pages, posts, images, SEO metadata. See `references/wordpress-to-astro-migration.md` for the full WP REST API → markdown → Astro content collection pipeline, including glob loader patterns and `render()` usage.
2. **Rebuild in Astro**: Recreate pages as `.astro` components, Tailwind for styling. Astro 6+ requires Node ≥22.12.0 — set `NODE_VERSION=22` in CF Pages env vars (`.node-version` file alone is NOT reliably read).
3. **301 redirects**: Map old WordPress URLs → new Astro URLs in `_redirects` or Cloudflare Bulk Redirects
4. **DNS cutover**: Point domain to Cloudflare Pages, verify SSL

### 4. Template Recommendation
- **Astro Basics + Tailwind**: Full control, good for former devs
- **Lumio theme**: Pre-built for service businesses, faster initial setup
- Recommendation: Start with Basics + Tailwind for full control

### 5. Timeline (Solo Dev)
- Part-time: 3-5 weeks
- Full-time: 2 weeks
- Breakdown: Content extraction (2 days), rebuild (5-7 days), testing (2 days), redirects + launch (1 day)

### 6. Gotchas

#### Cloudflare Pages
- **Cloudflare Auto Minify** can break Astro hydration — disable it
- **Node.js APIs** unavailable in Workers runtime — use static generation
- **SEO preservation**: 301 redirects for every old URL
- **FareHarbor GA4 parameters** must be preserved in embeds
- **Astro 6 requires Node ≥22.12.0**: Set `NODE_VERSION=22` in CF Pages env vars (`.node-version` file alone is NOT reliably read by CF Pages). Without this, builds fail with "Node.js v20.x.x is not supported by Astro." Alternatively, downgrade to Astro 5.x which supports Node 18+.
- **`wrangler.toml` BREAKS CF Pages builds**: When `pages_build_output_dir` is present in `wrangler.toml`, CF Pages SKIPS the build command entirely and looks for a pre-existing output directory. Fix: rename to `wrangler.toml.example` or remove it. Let CF Pages auto-detect the framework.
- **CF Pages may ignore new commits**: If deployments keep building old commits despite pushes, the GitHub webhook is stuck. Go to Settings → Builds & Deployments → Git Repository → Disconnect, then reconnect. This forces a fresh clone of the latest commit.
- **Committing `dist/` as backup**: If CF Pages build infrastructure is unreliable, pre-build locally and commit `dist/` (with `git add -f dist/` since it's usually gitignored). CF Pages deploys pre-built files directly without running a build step.
- **`@astrojs/tailwind` is REQUIRED for Tailwind to work in Astro**: Removing it breaks CSS — `@tailwind` and `@apply` directives appear raw in the output. The integration handles the PostCSS pipeline; a standalone `postcss.config.js` is insufficient. Verify CSS processing by checking that built HTML contains NO raw `@tailwind` or `@apply` strings. Full debugging guide: `references/astro-tailwind-debugging.md`.
- **`NODE_VERSION` env var must be Plaintext (not Secret)**: Secrets are only available at runtime, not build time. If it's set as a Secret, it won't appear in build logs and Node 20 will be used.
- **Build command must be set in dashboard**: If CF Pages doesn't auto-detect the framework, set **Build command: `npm run build`** and **Build output directory: `dist`** manually in Settings → Builds & Deployments → Build configuration.

#### Astro 6 Content Collections (Glob Loader)
- **`glob` loader entries do NOT have `.render()`**: Unlike the deprecated `type: 'content'` API, glob-loaded entries are data-only. To render markdown body content, use the standalone `render()` function from `astro:content`:
  ```astro
  import { getCollection, render } from 'astro:content';
  const entries = await getCollection('blog');
  const { Content } = await render(entry); // NOT entry.render()
  ```
- **`<slot />` does NOT work for glob-loaded content page templates**: The `<slot />` pattern only works when the markdown file IS the page route, not when mapping entries through `getStaticPaths` with a page template.
- **`type: 'content'` is removed in Astro 6**: It silently fails with "collection does not exist." Only `loader: glob()` is supported.
- **`z.coerce.date()` for frontmatter dates**: Use `z.coerce.date()` instead of `z.date()` in Zod schemas when frontmatter may have string dates — prevents silent schema rejection of all entries.

#### Content Migration Pitfalls
- **`read_file` output corruption**: read_file returns `LINE_NUM|CONTENT` format. NEVER pipe read_file output directly into write_file without stripping line numbers. If files already corrupted, fix with:
  ```python
  cleaned = re.sub(r'^\s+\d+\|', '', line)
  ```
- **Frontmatter descriptions >160 chars**: Truncate to prevent bloated meta tags. Trim at 157 chars + `...`.
- **WordPress HTML entities**: Mass-replace `&#8217;` → `'`, `&#038;` → `&`, `&#8220;` → `"`, etc. after pull.
- **Duplicate pages**: WP often has blog posts that overlap with static pages. Remove blog entries that duplicate new Astro static pages (about, contact, FAQ, privacy, cancellation) to avoid broken internal links.

## Active Oahu Tours Example
- Current: WordPress + FareHarbor embeds + Lockii widget
- Target: Astro SSG on Cloudflare Pages
- Booking: FareHarbor lightframe embed unchanged
- Self-service SOP: Separate from website — handled by FareHarbor/Lockii/Igloohome
- Research saved to: `~/work/alignment-deliverables/activeoahutours-astro-rebuild.md`

## ⚠️ Universal Rule: Design Cohesion

**When adding new pages to an existing website — whether Astro rebuild OR static mirror — always reuse the site's actual header, footer, and CSS.** Never create a parallel minimal-CSS design system. This creates TWO visual designs that clash, two navigation structures that confuse users, and breaks brand cohesion.

- **Static mirror**: Extract WordPress header/footer from a template page. See `references/static-mirror-lift-and-shift.md`.
- **Astro rebuild**: New pages must use the same `<Layout>` component as existing pages. Never create a one-off minimal layout for "just this page."
