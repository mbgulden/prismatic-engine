# Tourism Schema Injection — Static Site Mirror Pattern

## When to Use
You have a static HTML mirror (e.g., WordPress → wget → Cloudflare Pages) with zero JSON-LD schema and need to inject schema.org markup across the entire site without rebuilding it.

## Approach: Page-Type Detection + Batch Injection

Rather than manually identifying each page type, use a Python script that:

1. **Scans all `.html` files** in the site directory (skip `_templates/`)
2. **Detects page type** by URL path pattern:
   - Root `index.html` → TravelAgency + LocalBusiness (homepage)
   - URLs containing `faq` → FAQPage (extract Q&A from `<h2>`/`<h3>` + following `<p>`/`<li>`)
   - URLs containing `activities/` (but not `/page/`) → TouristTrip
   - URLs containing `rental` or `equipment` → Product
   - URLs containing `contact` → ContactPage
   - URLs containing `about` or `review` → Organization
   - URLs containing `blog`, `post`, `guide`, etc. → Article
3. **Injects JSON-LD** before `</head>` (or before `<body` as fallback)
4. **Skips pages** that already have `application/ld+json` in the HTML

## Schema Templates

### TravelAgency (Homepage)
```json
{
  "@context": "https://schema.org",
  "@type": ["TravelAgency", "LocalBusiness"],
  "name": "Business Name",
  "description": "...",
  "url": "https://example.com",
  "telephone": "+1-...",
  "address": { "@type": "PostalAddress", ... },
  "geo": { "@type": "GeoCoordinates", ... },
  "openingHours": "...",
  "priceRange": "...",
  "sameAs": ["...", "..."]
}
```

### FAQPage
```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Question text from <h2>/<h3>",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Answer text from following <p>/<li> (up to 500 chars)"
      }
    }
  ]
}
```

### TouristTrip
```json
{
  "@context": "https://schema.org",
  "@type": "TouristTrip",
  "name": "Tour name from <h1>",
  "description": "From meta description or fallback",
  "tourOperator": { "@type": "TravelAgency", ... },
  "touristType": ["Adventure Travelers", "Families", "Couples"],
  "offers": { "@type": "Offer", "priceCurrency": "USD", ... }
}
```

### Product (Rentals)
```json
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Rental item name",
  "brand": { "@type": "Brand", "name": "Business" },
  "offers": { "@type": "Offer", ... }
}
```

## Verification
After injection, spot-check key pages (homepage, FAQ, reviews) for schema presence:
```bash
python3 -c "
from pathlib import Path; import re, json
for p in ['index.html', 'faq/index.html', 'reviews/index.html']:
    text = Path(p).read_text()
    schemas = re.findall(r'<script type=\"application/ld\+json\">(.+?)</script>', text, re.DOTALL)
    if schemas:
        d = json.loads(schemas[0])
        print(f'{p}: {d.get(\"@type\")}')
"
```

## Template Script

A reusable `inject_schema.py` template lives at `templates/inject_schema.py`. Copy it to the target repo, customize the `BUSINESS` dict and `SITE_DIR`, then run:
```bash
cp ~/.hermes/profiles/orchestrator/skills/orchestration/golden-thread/templates/inject_schema.py .
# Edit BUSINESS dict and SITE_DIR
pip install --break-system-packages beautifulsoup4
python3 inject_schema.py
```
The script handles both modes: (a) reads a pre-existing `seo_audit_report.json` to target only missing pages, or (b) scans all `.html` files directly if no audit exists.

## Pitfalls
- **Cross-repo sync causes merge conflicts — re-run injection directly against the target repo instead.** When injecting schema into a working copy and then needing to deploy via a separate mirror repo, do NOT rsync the entire site directory from working copy to mirror. The two repos may have diverged (nav fixes, CSS changes, new pages) and rsync brings ALL differences — not just schema changes. This produces 200+ file diffs and merge conflicts on push. **Instead:** re-run the injection script against the mirror repo's site directory directly. Only schema changes go into the commit. Pattern: `cp inject_schema.py /path/to/mirror/ && cd /path/to/mirror && sed -i 's|/old/path|/new/path|g' inject_schema.py && python3 inject_schema.py`. GRO-697 (Jun 2026) is the canonical example: rsync → 249 files changed + merge conflicts → had to abort, reset to remote, and re-run injection against the mirror directly → clean 148-file commit.
- **Root-level pages with bare filenames** (like `index.html` at site root) have `rel_path = Path('index.html')`, not `Path('./index.html')`. Match against `str(rel_path)`, not `rel_path.name`.
- **FAQ page extraction**: Only extract up to 10 Q&A pairs to keep the JSON reasonable. Use `tag.find_next_siblings()` bounded by the next heading.
- **Duplicate pages**: Some WordPress mirrors have both `/page.html` and `/page/index.html`. Both need schema but may have different URL structures.
- **Error pages**: `404.html`, `search.html`, and admin pages should be skipped entirely.
- **Japanese/translated pages**: Schema should stay in the original language of the page. The script handles this since it reads page content and extracts questions/answers in whatever language the page uses.
