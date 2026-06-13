# Active Oahu Tours Mirror — Guide Page Generation Recipe

Proven June 3, 2026 across 3 guide pages (GRO-458, 459, 460). Template-based
generation for the WordPress static mirror at `active-oahu-tours-mirror`.

## Repository & Paths

- **Repo**: `/home/ubuntu/work/active-oahu-tours-mirror`
- **Templates**: `site/_templates/head.html` (181 lines), `body_top.html` (218 lines), `body_bottom.html` (1,255 lines)
- **Output**: `site/guides/<slug>/index.html`
- **Pages generated**: sea-turtles-oahu, ocean-kayaking-beginners-oahu, waimanalo-beach

## Schema Templates

### Article + FAQPage (per-guide-page standard)

```python
article_schema = """<script type='application/ld+json'>
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "PAGE TITLE HERE",
  "description": "PAGE DESCRIPTION HERE",
  "author": {"@type": "Organization", "name": "Active Oahu, LLC"},
  "publisher": {"@type": "Organization", "name": "Active Oahu, LLC",
    "logo": {"@type": "ImageObject",
      "url": "https://activeoahutours.com/wp-content/uploads/2022/07/Active-Oahu-Logo-01.jpg"}
  },
  "datePublished": "2026-06-03",
  "dateModified": "2026-06-03",
  "mainEntityOfPage": {"@type": "WebPage",
    "@id": "https://activeoahutours.com/guides/SLUG/"}
}
</script>
"""

faq_schema = """<script type='application/ld+json'>
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "QUESTION TEXT",
      "acceptedAnswer": {"@type": "Answer", "text": "ANSWER TEXT"}
    }
    // 4-5 Q&As per page
  ]
}
</script>
"""
```

## CTA Box Template

```html
<div class="cta-box" style="background:#e8f4f8; padding:20px; border-radius:8px; margin:30px 0;">
  <h3 style="margin-top:0;">Want to [do the thing this guide is about]?</h3>
  <p>[One sentence connecting guide topic to Active Oahu's kayak tours/rentals.]</p>
  <a href="/kailua-kayak/" class="btn-primary" style="display:inline-block; padding:12px 24px;
     background:#006699; color:#fff; text-decoration:none; border-radius:4px; font-weight:bold;">
    Book a Kayak Tour or Rental</a>
</div>
```

## Content Structure (proven across 3 pages)

1. `<h1>` with primary keyword
2. `<p class="lead">` — 1-2 sentence hook
3. Why this topic matters (local expertise angle)
4. Numbered locations/spots (3-5 entries, each with h3 + p + difficulty/paddle time stats)
5. **CTA Box #1** (after first content section)
6. Comparison table (beach vs beach, guided vs self-guided, etc.)
7. Tips list (numbered `<ol>`)
8. FAQ section (expands on the 4-5 FAQ schema Q&As)
9. **CTA Box #2** (before final paragraph)

## Verification

```bash
head -10 site/guides/<slug>/index.html              # check title
grep -c 'ld+json' site/guides/<slug>/index.html     # should be 4 (WebSite, Organization, Article, FAQPage)
grep 'entry-content' site/guides/<slug>/index.html | wc -l  # should be 2
git log --oneline -1                                 # verify commit
```

## Performance

- Generation: < 0.3s per page (execute_code)
- Verify: 5s per page (manual grep)
- Commit+push: 10s
- CF Pages deploy: automatic on push

## Page Generation Checklist

- [ ] 8 SEO metadata fields regex-replaced in head
- [ ] Article schema injected before `</head>`
- [ ] FAQPage schema injected before `</head>` (4-5 Q&As)
- [ ] `body_bottom.html` `</html>` closure handled
- [ ] Entry-content wraps all unique body HTML
- [ ] 2 CTA boxes (one mid-page, one near end)
- [ ] Commit message: `feat(GRO-XXX): [page title] — [key features]`
- [ ] Linear issue moved to Done after push
