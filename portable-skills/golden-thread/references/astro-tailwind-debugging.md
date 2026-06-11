# Astro + Tailwind CSS Debugging

## Quick Check: Is Tailwind Working?

After `npm run build`, verify the output HTML does NOT contain raw directives:

```bash
grep -q '@tailwind\|@apply' dist/index.html && echo "BROKEN" || echo "OK"
```

If you see raw `@tailwind base;@tailwind components;@tailwind utilities;` or `@apply` directives in the built CSS, Tailwind's PostCSS pipeline isn't processing your styles.

## Root Causes & Fixes

### 1. Missing @astrojs/tailwind Integration (Most Common)

Astro requires `@astrojs/tailwind` to process Tailwind directives. A standalone `postcss.config.js` is not sufficient.

```bash
npm install @astrojs/tailwind
```

In `astro.config.mjs`:
```js
import tailwind from '@astrojs/tailwind';
export default defineConfig({
  integrations: [tailwind()],
});
```

Remove any manual `vite.css.postcss` config — the integration handles it.

### 2. Missing tailwind.config

`@astrojs/tailwind` needs a `tailwind.config.mjs` (or `.cjs`, `.js`, `.ts`):

```js
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: '#1a2744', light: '#243356' },
        gold: { DEFAULT: '#f5a623', dark: '#d4941e' },
        ocean: { DEFAULT: '#2e86ab', light: '#3a9ec8' },
        sand: { DEFAULT: '#e8dcc8' },
        cream: { DEFAULT: '#faf7f2' },
      },
    },
  },
};
```

### 3. Version Compatibility

| Astro | @astrojs/tailwind | Tailwind CSS | Node |
|-------|------------------|-------------|------|
| 6.x   | @astrojs/tailwind@6 | 4.x | ≥22.12 |
| 5.x   | @astrojs/tailwind@5 | 3.x | ≥18 |
| 4.x   | @astrojs/tailwind@5 | 3.x | ≥18 |

When Astro 6 builds fail with "Node v20 is not supported," either set `NODE_VERSION=22` or downgrade to Astro 5 + @astrojs/tailwind@5.

### 4. global.css Must Include Tailwind Directives

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

These are processed by the PostCSS pipeline (via the Astro integration) and replaced with actual CSS.

## Verification

After a successful build:
- `dist/_assets/*.css` files should exist and contain expanded utility classes
- `dist/index.html` should have `<link rel="stylesheet" href="/_assets/...css">`
- No raw `@tailwind` or `@apply` strings anywhere in `dist/`
- `grep 'bg-navy' dist/index.html` should return matches (color classes expanded from Tailwind config)
