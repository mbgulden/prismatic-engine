# FAQPage Schema Injection — Programmatic Pattern

## When to Use
When you have existing HTML pages with visible FAQ sections (`.faq-item` divs with Q&A) but no FAQPage JSON-LD structured data. This is the most common AEO gap on programmatic SEO sites — content exists in HTML but isn't machine-readable for Google rich results.

## The Pattern

### 1. Extract visible FAQ content from HTML
```python
import re

def extract_faq_items(html):
    """Extract Q&A pairs from visible .faq-item divs."""
    # Handle BOTH <h3> and <h4> heading levels — different page types use different tags
    pattern = r'<div class="faq-item">\s*<h[34]>(.*?)</h[34]>\s*<p>(.*?)</p>\s*</div>'
    matches = re.findall(pattern, html, re.DOTALL)
    
    faq_items = []
    for q_html, a_html in matches:
        q_text = re.sub(r'<[^>]+>', '', q_html).strip()
        a_text = re.sub(r'<[^>]+>', '', a_html).strip()
        a_text = re.sub(r'\s+', ' ', a_text)
        if q_text and a_text:
            faq_items.append({'question': q_text, 'answer': a_text})
    return faq_items
```

### 2. Generate matching JSON-LD (Google requirement: text must match visible content exactly)
```python
def generate_faqpage_jsonld(faq_items):
    entities = []
    for item in faq_items:
        q = item['question'].replace('\\', '\\\\').replace('"', '\\"')
        a = item['answer'].replace('\\', '\\\\').replace('"', '\\"')
        entities.append(f'''    {{
      "@type": "Question",
      "name": "{q}",
      "acceptedAnswer": {{
        "@type": "Answer",
        "text": "{a}"
      }}
    }}''')
    
    return f'''<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
{",\n".join(entities)}
  ]
}}
</script>'''
```

### 3. Find insertion point in `<head>`
The insertion point varies by page structure. Try in order:
1. `</script>\n<style>` — after existing Article schema, before CSS block
2. `</script>\n<!-- Google Analytics` — after schema, before GA tag
3. `\n<style>` — fallback: before the CSS block

```python
article_end = html.find('</script>\n<style>')
if article_end == -1:
    article_end = html.find('</script>\n<!-- Google Analytics')
if article_end == -1:
    article_end = html.find('\n<style>')

insert_point = article_end
if html[insert_point:insert_point+9] == '</script>':
    insert_point += 9
    if html[insert_point] == '\n':
        insert_point += 1

new_html = html[:insert_point] + '\n' + schema_html + '\n' + html[insert_point:]
```

### 4. Process all pages by type
```python
PAGE_DIRS = {
    'gates':       BASE / 'gates',
    'channels':    BASE / 'channels', 
    'types':       BASE / 'types',
    'profiles':    BASE / 'profiles',
    'authorities': BASE / 'authorities',
}

for page_type, directory in PAGE_DIRS.items():
    for filepath in directory.glob('*.html'):
        if filepath.stem in ('index', 'generate'):
            continue
        if '"@type": "FAQPage"' in filepath.read_text():
            continue  # already has schema
        # extract + generate + insert + write
```

## Pitfalls

### `<h3>` vs `<h4>` heading level variation
**Symptom:** Regex matches FAQ items from gate pages but NOT channel pages. Script reports 0 FAQ items found for channels despite visible FAQ sections.
**Root cause:** Gate pages use `<h3>` for FAQ question headings; channel pages use `<h4>`. A regex targeting only `<h3>` silently skips channels.
**Fix:** Use `<h[34]>` to match both heading levels. Always spot-check a sample from each page type before running the full batch.

### Pages with no FAQ sections (e.g., index pages, centers)
Skip them — adding invisible schema that doesn't match visible content is a Google violation. Flag them for separate content creation.

### Double-injection
Always check `'"@type": "FAQPage"' in html` before writing. A prior session or script run may have already added the schema.

## Real Case: HD Growth Engine (June 2026)
- **Input:** 133 HTML pages (64 gates, 36 channels, 5 types, 12 profiles, 8 authorities, 9 centers) in `$PRISMATIC_HOME/work/hd-platform/docs/human-design/`
- **Result:** 124 pages received FAQPage schema (+5,423 lines). 9 center pages skipped (no FAQ sections). 0 double-injections.
- **Commit:** `304ba38` on `mbgulden/hd-platform` main branch
- **Deployment:** Cloudflare Pages auto-deploy from GitHub push
- **Impact:** First-mover in HD niche — zero competitors have FAQPage schema on individual gate/channel pages
