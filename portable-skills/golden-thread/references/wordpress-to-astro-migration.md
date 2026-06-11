# WordPress → Astro Content Migration Pipeline

End-to-end pattern for pulling WordPress content via REST API, cleaning it, and loading it into Astro 6 content collections for a static site rebuild.

## Phase 1: Content Inventory

```bash
# Get all published pages
curl -s "https://example.com/wp-json/wp/v2/pages?per_page=50" | python3 -c "
import json, sys
pages = json.load(sys.stdin)
for p in sorted(pages, key=lambda x: x['id']):
    print(f'PAGE|{p[\"id\"]}|{p[\"status\"]}|{p[\"slug\"]}|{p.get(\"parent\",0)}|{p[\"title\"][\"rendered\"][:100]}')
"
```

Map page hierarchy: `parent=0` = top-level page, `parent=<id>` = child of that page.

## Phase 2: Pull Full Content

```python
# For each page, fetch rendered content + extract meta
# WP REST returns rendered HTML in content.rendered
# Yoast SEO data may be in yoast_head_json.og_description (but often empty)
# Fallback: use excerpt.rendered or first 160 chars of content
```

Save each page as markdown with YAML frontmatter:
```markdown
---
title: "Page Title"
slug: "page-slug"
description: "SEO description under 160 chars"
pageType: "blog"     # or "page" for static pages
oldUrl: "/original-wp-slug/"
newUrl: "/guides/new-slug/"
publishedDate: 2025-01-15
---
```

## Phase 3: Clean WordPress Artifacts

```python
import re

text = re.sub(r'(\?full-items=yes)[^)\s"\]]*', r'\1', text)           # strip FareHarbor refs
text = re.sub(r'https://example\.com/wp-content/uploads/[^\s)"\]]+',
              '/images/placeholder-tour.jpg', text)                    # replace WP image URLs
text = text.replace('&#038;', '&')                                     # HTML entities
text = text.replace('&#8217;', "'")
text = text.replace('&#8220;', '"')
text = text.replace('&#8221;', '"')
text = text.replace('&#8211;', '–')
text = re.sub(r'\n{4,}', '\n\n\n', text)                              # compact whitespace
```

### Fix read_file Corruption

If files were created by piping `read_file` output (which includes `LINE_NUM|` prefixes):
```python
cleaned = re.sub(r'^\s+\d+\|', '', line)  # strip "     1|" prefixes
```

## Phase 4: Astro Content Collection Setup

### Astro 6: Use `glob` loader ONLY

`type: 'content'` is removed in Astro 6 — it silently fails. Always use:
```ts
const blogCollection = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    slug: z.string(),
    description: z.string().default(''),
    publishedDate: z.coerce.date().default(() => new Date()),
  }),
});
```

Key: use `z.coerce.date()` — string dates in YAML frontmatter won't pass `z.date()` validation.

### Rendering Content Body

Glob-loaded entries are data-only. To render markdown body content:
```astro
---
import { getCollection, render } from 'astro:content';

export const getStaticPaths: GetStaticPaths = async () => {
  const entries = await getCollection('blog');
  return entries.map((entry) => ({
    params: { slug: entry.data.slug },
    props: { entry },
  }));
};

const { entry } = Astro.props;
const { Content } = await render(entry);  // NOT entry.render()
---
<article>
  <Content />
</article>
```

## Phase 5: Remove Duplicates

Blog posts that overlap with new static Astro pages should be removed:
- `/about/`, `/contact/`, `/faq/`, `/cancellation/`, `/privacy/`, `/reviews/`
- Any WP pages used as static landing pages

Keep them as static `.astro` pages with proper schema, not as blog collection entries.

## Phase 6: Redirect Map

Generate `_redirects` from the content inventory:
```
# Old WordPress URL → New Astro URL (301)
/old-wp-slug/          /new-astro-url/          301
```

Use Cloudflare Pages `_redirects` in `public/` — these deploy with the site and are applied by Cloudflare's edge, no server config needed.
