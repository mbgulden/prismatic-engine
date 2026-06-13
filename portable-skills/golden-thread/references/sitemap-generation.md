# Static Site Sitemap Generation

Quick reusable pattern for generating XML sitemaps from a static site's `docs/` directory.

## Use Case
When you have a static site with 100+ HTML pages and no sitemap, generate one programmatically. Used for `humandesignengine.com` (154 URLs) and applicable to any Cloudflare Pages static site.

## Script

```python
import os
from datetime import datetime, timezone

base_dir = os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "/work/<repo>/docs"
base_url = 'https://<domain>.com'

pages = []
for root, dirs, files in os.walk(base_dir):
    for f in files:
        if f.endswith('.html'):
            full = os.path.join(root, f)
            rel = os.path.relpath(full, base_dir)
            if rel == 'index.html':
                url_path = ''
            else:
                url_path = rel.replace('index.html', '').rstrip('/')
            pages.append(url_path)

pages = sorted(set(pages))
today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

xml = ['<?xml version="1.0" encoding="UTF-8"?>']
xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
xml.append(f'  <url><loc>{base_url}/</loc><lastmod>{today}</lastmod><priority>1.0</priority></url>')

for p in pages:
    if p in ('', 'index.html'):
        continue
    url = f'{base_url}/{p}'
    # Adjust priority heuristics per project
    if 'human-design' in p or 'guides' in p:
        prio = '0.8'
    elif 'landing' in p:
        prio = '0.9'
    else:
        prio = '0.6'
    xml.append(f'  <url><loc>{url}</loc><lastmod>{today}</lastmod><priority>{prio}</priority></url>')

xml.append('</urlset>')

sitemap = '\n'.join(xml)
with open(f'{base_dir}/sitemap.xml', 'w') as f:
    f.write(sitemap)

print(f"Sitemap: {len(pages)} URLs → {base_dir}/sitemap.xml")
```

## Deploy
```bash
cd ${PRISMATIC_HOME}/work/<repo> && \
  git add docs/sitemap.xml && \
  git commit -m "Add SEO sitemap with N URLs" && \
  git push origin main
```

Cloudflare Pages auto-deploys on push. Submit the sitemap URL to Google Search Console after deploy.

## Priority Heuristics (adjust per project)
- `1.0` — homepage
- `0.9` — landing pages, key conversion pages
- `0.8` — main content hubs (types, guides, profiles)
- `0.6` — leaf pages, individual items

## Pitfalls
- `datetime.utcnow()` is deprecated — use `datetime.now(timezone.utc)`
- Skip `index.html` files in subdirectories (they become the directory URL)
- Always validate: `python3 -c "import xml.etree.ElementTree; xml.etree.ElementTree.parse('docs/sitemap.xml')"`
