# WordPress FAQ Extraction Patterns

## The problem

When extracting Q&A pairs from WordPress static HTML exports to build FAQPage JSON-LD, naive H2/H3/strong→P regex matching picks up false positives: promo banners, navigation buttons, award sections, and "View Job Openings" links — all of which use `<strong>` tags inside `<h4>`, `<li>`, or other non-FAQ containers.

## Correct extraction pattern

WordPress FAQ pages (and many static mirrors) use this structure for Q&As:

```html
<p><strong>Can we rent gear for just a couple of hours?</strong> — No, we charge for 4 hours minimum…</p>
<p><strong>How long is the "Full Day" rental?</strong> — From 8 am to 4.30 pm…</p>
```

The reliable extraction pattern:

```python
import re

qa_matches = re.findall(r'<strong>(.*?)</strong>(.*?)</p>', content, re.DOTALL)

questions = []
for q_html, a_html in qa_matches:
    q_text = re.sub(r'<[^>]+>', '', q_html).strip()
    a_text = re.sub(r'<[^>]+>', ' ', a_html).strip()
    a_text = re.sub(r'\s+', ' ', a_text)
    # Strip leading em-dash or hyphen separator
    a_text = re.sub(r'^[—\-–]\s*', '', a_text).strip()
    # Strip "a t " → "at " (span-tag artifact)
    a_text = re.sub(r'^a t ', 'at ', a_text)
    # Filter: must contain '?' and be substantive
    if '?' in q_text and len(q_text) > 10 and len(a_text) > 20:
        questions.append({"q": q_text, "a": a_text})
```

## False-positive filters

These content patterns are NOT FAQ questions and must be excluded:

```python
SKIP_KEYWORDS = [
    'book online', 'view job', 'view the gallery',
    '15off', 'deal:', 'follow us', 'get update',
    'awesome photo', 'our location', 'recent post'
]

if not any(skip in q_text.lower() for skip in SKIP_KEYWORDS):
    questions.append({"q": q_text, "a": a_text})
```

## Anti-patterns (avoid)

1. **Generic H2/H3 → next-P matching** — grabs award section headings, testimonial headers, and "Quick Answer" blocks as fake Q&As
2. **`<strong>` anywhere on the page** — banners, buttons, and nav all use `<strong>`. Constrain to `<p><strong>...</strong>...</p>` context
3. **Skipping the `?` filter** — real FAQ questions end with `?`; promo text and buttons do not

## Existing FAQPage replacement

When a page already has a weak FAQPage schema (from a prior bad extraction), replace in-place rather than injecting a duplicate:

```python
existing = re.compile(
    r'<script type="application/ld\+json">\s*\{.*?"@type"\s*:\s*"FAQPage".*?</script>',
    re.DOTALL
)
matches = existing.findall(content)
if matches:
    new_block = f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>'
    content = content.replace(matches[0], new_block, 1)
else:
    content = content.replace('</head>', new_block + '\n</head>', 1)
```
