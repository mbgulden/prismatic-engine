# Astro + Tailwind + CF Pages — Verified Setup (2026-05-31)

## Version compatibility

| Astro | Node | Tailwind | Integration |
|-------|------|----------|-------------|
| 5.x | 18+ | 3.x | `@astrojs/tailwind@5` |
| 6.x | 22+ | 3.x | `@astrojs/tailwind@6` (incompatible, use native CSS) |

CF Pages defaults to Node 20, so use Astro 5.x.

## Required files

### astro.config.mjs
```js
import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  integrations: [tailwind()],
  site: 'https://yourdomain.com',
});
```

### tailwind.config.mjs
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

### DO NOT use manual PostCSS config
`@astrojs/tailwind` handles the Tailwind → PostCSS pipeline. A separate `postcss.config.js` with manual `tailwindcss: {}` will conflict.

## Astro is:inline for scripts

Astro's bundler wraps `<script>` tags as `type="module"` which:
- Defers execution
- Scopes variables
- Can break simple DOM manipulation (getElementById, classList.toggle)

Fix: use `is:inline` directive:
```astro
<script is:inline>
  document.getElementById('toggle')?.addEventListener('click', () => { ... });
</script>
```

This preserves the script as-is in the output HTML.

## Content collections: glob loader vs type:'content'

Astro 5/6 uses `loader: glob()` for content collections:
```ts
const blogCollection = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({ title: z.string(), slug: z.string() }),
});
```

Body content rendering: use `<slot />` in the layout (markdown body is auto-injected), NOT `<Content />` from `render()`. The `render()` function requires `type: 'content'` which was removed in Astro 5.

## Image handling

Placeholder images (0-byte files) break. Always verify image files are valid:
```bash
file public/images/hero.jpg  # Should show "JPEG image data", not "empty"
```

For generating placeholder images with PIL:
```python
from PIL import Image, ImageDraw
img = Image.new('RGB', (1200, 630), (26, 39, 68))
draw = ImageDraw.Draw(img)
draw.text((600, 315), "Site Name", fill=(255,255,255))
img.save('hero.jpg', 'JPEG', quality=85)
```
