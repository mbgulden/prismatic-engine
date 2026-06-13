# WordPress to Astro Migration — Session Patterns

## Content Pull via WP REST API

```bash
# Get all pages with their rendered content
curl -s "https://site.com/wp-json/wp/v2/pages?per_page=50" | python3 -c "
import json, sys
for page in json.load(sys.stdin):
    print(f'{page[\"slug\"]}: {page[\"title\"][\"rendered\"][:80]}')
"
```

Key endpoints:
- `/wp-json/wp/v2/pages?per_page=50` — all pages (WP often stores blog posts as pages)
- `/wp-json/wp/v2/media?per_page=100` — media library (for photo migration)
- Yoast SEO data is NOT exposed via default REST API routes — use page excerpts as fallback meta descriptions

## Line-Number Prefix Corruption (CRITICAL)

When pulling WordPress content and writing to Astro `.md` files via Python/terminal pipeline, the `read_file`-style output format (`LINE_NUM|CONTENT`) can accidentally get baked into files. 

**Detection:**
```bash
python3 -c "print(repr(open('file.md','rb').read(20)))"
# Corrupted output: b'     1|---\\n     2|ti...'
# Clean output: b'---\\ntitle: \"...'
```

**Fix (batch):**
```python
import re, os
for fname in os.listdir(blog_dir):
    if not fname.endswith('.md'): continue
    path = os.path.join(blog_dir, fname)
    with open(path) as f:
        content = f.read()
    if re.match(r'^\s+\d+\|', content):
        cleaned = '\n'.join(
            re.sub(r'^\s+\d+\|', '', line)
            for line in content.split('\n')
        ).strip() + '\n'
        with open(path, 'w') as f:
            f.write(cleaned)
```

**Impact:** 18/64 blog posts corrupted this way during the Active Oahu migration. Schema validation (`astro build`) failed because corrupted frontmatter had no valid YAML delimiters.

## WordPress HTML Cleanup

After pulling content, strip WordPress artifacts:

```python
# FareHarbor ref codes in URLs
text = re.sub(r'(\?full-items=yes)[^)\s"\]]*', r'\1', text)

# WP image URLs → local placeholders
text = re.sub(r'https://site\.com/wp-content/uploads/[^\s)"\]]+', '/images/placeholder.jpg', text)

# HTML entities
text = text.replace('&#038;', '&').replace('&#8217;', "'")
```

## Content Collection Schema for Migrated Posts

Astro 6 glob loader with Zod:
```typescript
const blogCollection = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    slug: z.string(),
    description: z.string().default(''),
    pageType: z.string().default('blog'),
    oldUrl: z.string().default(''),
    newUrl: z.string().default(''),
    publishedDate: z.coerce.date().default(() => new Date()),
  }),
});
```

Use `z.coerce.date()` for dates in YAML frontmatter — it handles string-to-Date conversion.

## Redirect Map Generation

Build from content inventory JSON:
```python
# Old WP URL → new Astro URL mapping
for item in inventory:
    old = item['oldUrl'].replace('https://site.com', '')
    new = item.get('newUrl', f"/guides/{item['slug']}/")
    if old and old != '/' and old != new:
        print(f"{old:<50} {new:<35} 301")
```

Cloudflare Pages serves `_redirects` from `public/` directory. One redirect per line, space-separated fields.

## Photo Migration from Self-Hosted Media

WP media library accessible at `/wp-json/wp/v2/media`. Download and resize:

```python
from PIL import Image

img = Image.open(source_path)
ratio = max_width / img.size[0]
img = img.resize((max_width, int(img.size[1] * ratio)), Image.LANCZOS)
if img.mode in ('RGBA', 'P'):
    img = img.convert('RGB')
img.save(dest_path, 'JPEG', quality=85, optimize=True)
```

Hero images: 1200-2000px wide. Tour card images: 800px wide. Blog post images: 800px wide.
