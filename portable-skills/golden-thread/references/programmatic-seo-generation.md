# Programmatic SEO Page Generation

## Pattern
When you have a structured data source (database, JSON, API) with consistent fields, generate SEO-optimized HTML pages programmatically rather than writing them one at a time.

## This Session's Example
Generated 112 Human Design pages from MCP engine data:
- 64 gate pages (gate name, I-Ching hexagram, circuit, center, plain-English meaning, channels)
- 36 channel pages (gates, circuit group, centers connected, energy type, living tips)
- 9 center pages (function, defined vs undefined, gates, channels, connected centers)
- 3 index pages (gates, channels, centers) with CollectionPage schema

Total: 112 pages across 3 subdirectories under docs/human-design/.

## Generator Pattern
```python
def generate_page(template, data):
    """One function that takes a template string and data dict, writes an HTML file."""
    html = template.format(**data)
    filename = slugify(data['name'])
    write_file(f'output/{filename}.html', html)
```

## Key Elements Per Page
1. **Unique title**: `Human Design Gate {N} — {Name} | Brand Name`
2. **Meta description**: All page-specific, 150-160 chars
3. **Open Graph + Twitter Cards**: Title, description, image per page
4. **JSON-LD structured data**: Article schema with headline, datePublished, author
5. **Canonical URL**: Full path to avoid duplicate content
6. **Internal linking**: Cross-link related pages (e.g., gate pages link to their channels)

## When to Use
- Any catalog with 20+ items sharing the same template
- E-commerce product pages
- Documentation/API reference pages
- Location/service area pages
- Any data-driven content where the template is consistent

## Pitfalls
- **FIRST: check what already exists.** Use `search_files(target='files', pattern='*.html', path='docs/...')` to see what's already generated before writing a generator script. The golden thread cron job runs daily — pages from a prior session are likely already done. Don't regenerate.
- Generate an index/sitemap page linking to all generated pages for crawlers
- Keep a `generate.py` script alongside output for regeneration (and commit it)
- Verify at least 3 random pages after generation with `curl` or `read_file`
- Use the site's existing CSS theme (navy/gold design system) — don't create a new design per page
- Generator scripts go in the same directory as output: `docs/human-design/<category>/generate.py`
- Update the registry's SEO `pages` count and `next_action` after each batch
