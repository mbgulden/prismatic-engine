# AOT FareHarbor Deep-Link CTA Pattern

Replace generic FareHarbor catalog embeds with item-specific deep-linked CTAs on the
Active Oahu Tours static mirror. Eliminates catalog-browsing friction — users go
directly to the specific item's booking calendar.

## Generic Catalog Pattern (TARGET — replace these)

```html
<!-- Opens full FareHarbor catalog, not a specific item -->
<a href="https://fareharbor.com/embeds/book/activeoahutours/?u=<UUID>&from-ssl=yes">
  <strong><span class="glyphicon glyphicon-calendar"></span> Book Online</strong>
</a>
```

Identified by: `embeds/book/activeoahutours/?u=` with no `items/`, `flow=`, or `selected-items=`.

## Deep-Link Pattern (REPLACEMENT)

```html
<a href="https://fareharbor.com/embeds/book/activeoahutours/items/<ITEM_CODE>/calendar/"
   onclick="FH.open({'shortname':'activeoahutours','view':{'item':'<ITEM_CODE>'},'fallback':'simple'}); return false;"
   target="_blank"
   class="...">
  <strong><span class="glyphicon glyphicon-calendar"></span> <SPECIFIC_CTA_TEXT></strong>
</a>
```

Key elements:
- `href`: Calendar fallback URL with `/items/<ITEM_CODE>/calendar/` — works even if `FH.open()` JS hasn't loaded
- `onclick`: `FH.open()` with item-specific view — preferred path, opens FareHarbor lightframe
- `target="_blank"`: Opens in new tab if lightframe blocked
- CTA text: Specific and value-oriented (e.g., "Book Chinaman's Hat Tour", "Rent Kayaks from $49")

## Known Item Codes

| Code | Product | Used On |
|------|---------|---------|
| 115595 | Chinaman's Hat Self-Guided Kayak Tour | Homepage, activities listing |
| 8522 | Kahana Rainforest River Kayak Tour | Homepage, activities listing |
| 521252 | Mokulua Islands Guided Kayak Tour (Kailua) | Homepage, activities listing |
| 7872 | Primary kayak/bike rental (most common code) | 182 pages, body_bottom template |
| 516089 | Kailua Guided Kayak Tour | Activities listing |
| 526154 | Activity listing item | Activities listing |
| 524167 | Activity listing item | Activities listing |
| 654229 | Activity listing item | Activities listing |
| 654233 | Activity listing item | Activities listing |
| 703390 | Activity listing item | Activities listing |

Find all codes: `grep -roh "item':'[0-9]*'" site/ | sort | uniq -c | sort -rn`

## Flow URLs (alternative — pre-filtered catalog views)

Some pages use FareHarbor "flows" which show a subset of items without deep-linking to one:
```
https://fareharbor.com/embeds/book/activeoahutours/?flow=728039
```
Flows are better than full catalog but not as targeted as item deep-links. Use for
category pages where multiple items are relevant (rentals page with flow=728039).

## Audit Commands

```bash
# Find all generic catalog embeds (no item code, no flow)
grep -rn 'embeds/book/activeoahutours/?u=' site/ --include="*.html" | grep -v '_templates'

# Find all deep-linked items (already correct)
grep -rn "item':'[0-9]*'" site/ --include="*.html" | wc -l

# Find pages using flows (pre-filtered, acceptable)
grep -rn 'flow=' site/ --include="*.html" | grep -v '_templates'
```

## Header "Book Online" — Site-Wide Template Issue

The sticky header "Book Online" button (appears on 250+ pages) links to the generic catalog
and is embedded in every page's inline header, not in a shared template. Changing it
requires a decision: should the header deep-link to the most popular item? Or stay general?
This is a separate concern from in-content CTA fixes.

## Conversion-Focused CTA Copy

Replace generic button text with specific, value-oriented copy per the CTA audit
(`site/_seo/reports/05-cro-seo/cta-audit.md`):

| Generic | Optimized |
|---------|-----------|
| "Book Online" | "Check Availability & Prices" or specific: "Book Chinaman's Hat Tour" |
| "Book" | "Reserve Tandem Kayak" |
| "Rent Kayaks & Beach Gear" | "Rent Kayaks from $49" |
