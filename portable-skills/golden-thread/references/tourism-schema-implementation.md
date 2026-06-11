# Tourism & Activities Schema Implementation Pattern

## The Schema Hierarchy

For any tour operator, rental company, or activity outfitter, the schema.org types form a hierarchy:

```
TravelAgency (homepage — the business entity)
├── makesOffer → TouristTrip (guided/self-guided tours & activities)
├── makesOffer → Product (rental items — kayaks, bikes, gear)
├── subjectOf → FAQPage (Q&A sections on any page)
├── ItemList (hub/index pages listing all offerings)
└── ContactPage (contact page with business info)
```

## Which Schema for Which Page Type

| Page Category | Schema Type | Key Properties |
|--------------|-------------|----------------|
| Homepage, About | `TravelAgency` | name, url, logo, address, telephone, sameAs, areaServed |
| Guided tours | `TouristTrip` | name, description, touristType ("Guided"), itinerary, provider→TravelAgency |
| Self-guided tours | `TouristTrip` | name, description, touristType ("Self-Guided"), provider→TravelAgency |
| Rental items (single kayak, tandem, SUP, bike) | `Product` | name, description, offers (price, currency, availability), category, brand |
| Rental hub page | `Product` (with variants) or `ItemList` | List of Product items |
| Hub page listing all tours | `ItemList` | itemListElement array of TouristTrip references |
| Q&A/info pages | `FAQPage` | mainEntity array of Question/Answer pairs |
| Contact page | `ContactPage` | about→TravelAgency, url |

## Why TouristTrip over Product for Tours

`TouristTrip` is a schema.org type specifically for tourism excursions. It has:
- `touristType` — "Guided" or "Self-Guided"
- `itinerary` — structured trip itinerary
- `provider` — link to the TravelAgency

`Product` is for physical/digital goods. A kayak rental ITEM is a Product, but a guided kayak TOUR is a TouristTrip.

**Oahu competitor landscape**: As of May 2026, zero Oahu tour operators use TouristTrip, FAQPage, or ItemList schema. Most use only basic WebSite/Organization JSON-LD from Yoast. This is a significant technical SEO moat.

## JSON-LD Injection Pattern

When working with static HTML pages that already have a `<head>` section, inject schema before `</head>`:

```python
import json, re

schema = {
    "@context": "https://schema.org",
    "@type": "TouristTrip",
    "name": "Sharks Cove Snorkeling — Self-Guided Oahu Snorkel Tour",
    "description": "...",
    "touristType": "Self-Guided",
    "provider": {
        "@type": "TravelAgency",
        "name": "Active Oahu, LLC",
        "url": "https://activeoahutours.com/"
    }
}

schema_ld = f"<script type='application/ld+json'>{json.dumps(schema)}</script>"
html = html.replace('</head>', f'{schema_ld}\n</head>')
```

**Important**: If the page already has other JSON-LD blocks (Yoast WebSite, Organization), the new schema is additive — inject all of them. Multiple `<script type='application/ld+json'>` blocks are valid and Google processes all of them.

## Schema per Page — Implementation Checklist

For a full site audit + implementation:

1. **Homepage** — TravelAgency (verify all fields populated)
2. **Every tour/activity page** — TouristTrip with correct touristType
3. **Every rental page** — Product with price/availability
4. **Hub/index pages** — ItemList referencing child entities
5. **Contact page** — ContactPage with about→TravelAgency
6. **Q&A pages** — FAQPage (question/answer pairs)
7. **Blog posts** — Article (verify Yoast output is valid JSON-LD)
8. **About page** — TravelAgency (or AboutPage if purely informational)

## Verification

- **Google Rich Results Test**: `https://search.google.com/test/rich-results`
- **Schema Markup Validator**: `https://validator.schema.org/`
- **Quick grep**: `grep -c 'TouristTrip\|Product\|FAQPage\|ItemList\|ContactPage' site/*/index.html`

## Integration with Static Mirror Migration

When generating pages from templates (see `static-mirror-migration.md` Step 6b), inject the schema as part of the head customization pass. Each page definition in the batch includes its schema object alongside title, description, and body content.

## Bulk Schema Injection Across an Existing Static Site

When you have a large static mirror with zero schema (common after `wget --mirror`), use a Python script to scan all pages by URL path and inject the correct schema type:

```python
# Pseudocode pattern — see inject-schema.py for full implementation
for path in site.rglob("*.html"):
    if 'faq' in str(path):
        # Extract Q&A from h2/h3 + adjacent <p> tags via BeautifulSoup
        # Inject FAQPage schema
    elif 'activities/' in str(path):
        # Inject TouristTrip schema linked to TravelAgency provider
    elif 'rental' in str(path):
        # Inject Product schema with price/availability
    elif 'contact' in str(path):
        # Inject ContactPage schema
    elif 'about' in str(path) or 'review' in str(path):
        # Inject Organization schema
    elif path.name == 'index.html':  # homepage
        # Inject LocalBusiness + TravelAgency combined schema
    else:
        # Inject Article schema for blog/guide pages
```

**Key patterns:**
1. Check `'application/ld+json' in html` first to skip already-injected pages
2. Inject before `</head>`: `html.replace('</head>', schema_block + '</head>', 1)`
3. For FAQPage, use BeautifulSoup to extract h2/h3 questions + adjacent p/li answers
4. For TouristTrip, link back to a shared TravelAgency entity via `tourOperator` or `provider`
5. Handle duplicate pages: `page.html` and `page/index.html` both exist in WP mirrors — inject both
6. Process JA pages alongside EN pages — same schema types, different `url` field
