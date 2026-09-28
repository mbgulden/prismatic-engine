# Astro + Cloudflare Pages — Verified Pitfalls

## 1. Astro `<script>` Module Wrapping Breaks DOM Scripts

**Symptom:** Mobile menu toggle, FareHarbor embed triggers, or any inline `<script>` silently fails.

**Root cause:** Astro wraps `<script>` tags as `<script type="module">`, which:
- Defers execution (runs after DOMContentLoaded, so elements may exist)
- Changes variable scoping
- Changes `this` context

**Fix:** Add `is:inline` to bypass module wrapping:
```html
<script is:inline>
  (function() {
    var toggle = document.getElementById('mobile-menu-toggle');
    var menu = document.getElementById('mobile-menu');
    if (toggle && menu) {
      toggle.addEventListener('click', function() {
        menu.classList.toggle('hidden');
      });
    }
  })();
</script>
```

Avoid arrow functions and optional chaining (`?.`) in is:inline scripts — use ES5-compatible patterns for maximum browser compatibility.

## 2. Tailwind CSS Not Processing

**Symptom:** Built HTML has raw `@tailwind base`, `@apply bg-navy` in inline `<style>` blocks. No utility classes generated.

**Root cause:** Astro needs `@astrojs/tailwind` integration. Manual `postcss.config.js` alone is insufficient.

**Fix (3 steps):**
```bash
npm install @astrojs/tailwind
```

```js
// astro.config.mjs
import tailwind from '@astrojs/tailwind';
export default defineConfig({
  integrations: [tailwind()],
});
```

```js
// tailwind.config.mjs
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: '#1a2744', light: '#243356' },
        gold: { DEFAULT: '#f5a623', dark: '#d4941e' },
        ocean: { DEFAULT: '#2e86ab', light: '#3a9ec8' },
      },
    },
  },
};
```

Remove any `postcss.config.js` — the integration handles it. Verify: grep for `@tailwind` in built HTML — should return zero matches.

## 3. FareHarbor Embed Format

**Symptom:** FareHarbor booking calendar shows "Loading..." forever.

**Root cause:** FareHarbor's `autolightframe` API requires specific `data-*` attributes on container divs. Random `id` values (like `fareharbor-landing-lightframe`) are not recognized.

**Fix:**
```html
<!-- In <head> — load ONCE globally -->
<script is:inline src="https://fareharbor.com/embeds/api/v1/?autolightframe=yes"></script>

<!-- In body — proper container format -->
<div data-fareharbor-lightframe data-fareharbor-shortname="activeoahutours" class="min-h-[500px]">
  <p>Loading booking calendar...</p>
</div>
```

Key points:
- `data-fareharbor-lightframe` attribute is required
- `data-fareharbor-shortname="YOUR_SHORTNAME"` tells FareHarbor which account
- Load the script ONCE in `<head>`, not per-page in booking sections
- Use `is:inline` on the script tag to prevent Astro module wrapping
- Shortname must match exactly what's configured in FareHarbor dashboard

## 4. Node Version on CF Pages

CF Pages defaults to Node 20. Astro 6 requires ≥22.12.0.

**Triple-prong approach (use all three):**
1. `echo "22" > .node-version` at repo root
2. `echo "22" > .nvmrc` at repo root (CF sometimes prefers this)
3. Dashboard → Settings → Environment Variables → `NODE_VERSION=22` (Plaintext type, not Secret)

If env var doesn't appear in build logs and `.node-version`/`.nvmrc` are ignored, **downgrade framework** (e.g., `npm install astro@5` — Astro 5 supports Node 18+). Don't spend >3 attempts debugging Node version.

## 5. wrangler.toml Short-Circuits Build

If `wrangler.toml` has `pages_build_output_dir = "dist"`, CF Pages skips `npm run build` entirely.

**Symptom:** Build log: "Found wrangler.toml file... No build command specified. Skipping build step. Error: Output directory 'dist' not found."

**Fix:** Rename to `wrangler.toml.example`. CF Pages auto-detects Astro and runs the full pipeline.

## 6. Missing `site:` in astro.config.mjs → og:url Shows localhost

**Symptom:** Deployed pages have `<meta property="og:url" content="http://localhost:4321/path/">` instead of the production URL. Twitter cards and Open Graph previews show localhost. Also affects canonical link tags and sitemap.xml URLs.

**Root cause:** Astro uses the `site` config option to populate og:url, twitter:url, canonical URLs, and sitemap entries. When `site` is not set, it falls back to localhost during development builds.

**Fix (add one line to astro.config.mjs):**
```
export default defineConfig({
  site: 'https://beyondsaas.ai',
  // ... rest of config
});
```

Verify after rebuild: `grep 'og:url' dist/path/to/page/index.html` should show the production URL, not localhost. This one config line fixes all URL-related metadata across every page in the build.

Discovered via AGY brand verification comparing a new page against the production homepage (June 2026).
