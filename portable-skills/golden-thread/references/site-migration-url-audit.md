# Static Site Migration — URL Audit & Fix Pattern

Reusable workflow for any WordPress → static mirror migration. Tested on Active Oahu Tours (206→216 pages, Flywheel → Cloudflare Pages).

## Problem: WordPress Static Dumps Have Hidden Breakage

When you `wget --mirror` a WordPress site, three categories of issues emerge:

### 1. Missing-Route Pages (SPA Fallback Problem)
CF Pages with Framework Preset "None" uses SPA fallback: if no file exists at the requested path, it serves `index.html` (200 status). This means pages that 404 on the live WordPress site will return 200 on the mirror — but with **duplicate homepage content**.

**Detection:**
```bash
# Pages returning 200 but with identical title/body to homepage
for page in $(cat sitemap_urls.txt); do
  title=$(curl -s "$page" | grep -oP '<title>\K[^<]+')
  [ "$title" = "$HP_TITLE" ] && echo "DUPLICATE: $page"
done
```

**Fix:** Create unique `index.html` files in the corresponding directories. The new files override the SPA fallback. Each needs: unique title, meta description, canonical URL, AEO Quick Answer block, FAQPage schema, GTM snippet, FareHarbor CTA.

### 2. Orphan Pages (Lost Navigation)
WordPress menus are dynamically generated via PHP. A static dump captures the rendered HTML but loses the menu's awareness of the full site structure. Internal links are often relative (`href="../"`, `href="index.html"`) and many pages end up with **zero incoming links from other pages** — invisible to both users and Google.

**Detection script:**
```python
# Find pages with zero incoming internal links
for page in all_pages:
    linkers = set()  # pages that link TO this page
    # Crawl all pages, extract hrefs, resolve relative URLs
    # A page is orphaned if len(linkers) == 0
```

**Fix:** Inject a universal navigation bar into every page's `<body>`:
```html
<div style="background:#0a1628;padding:12px 20px">
  <a href="/">🏠 Home</a>
  <a href="/key-page-1/">Page 1</a>
  <a href="/key-page-2/">Page 2</a>
  ...
</div>
```

Use Python to batch-patch: `html[:body_end] + NAV_HTML + html[body_end:]`

### 3. Redirect Chains
WordPress often has competing URL structures — clean permalinks redirect to nested paths. The mirror captures files at their actual paths, eliminating redirects. The migration itself fixes this problem; just verify zero 301/302/308 remain.

## Full Audit Checklist

- [ ] Pull all sitemap URLs (page-sitemap.xml, activities-sitemap.xml, etc.)
- [ ] Check every URL: live status vs mirror status
- [ ] Categorize: hard 404s, 301 redirects, duplicate content, clean
- [ ] Cross-reference with Search Console traffic data
- [ ] Quantify lost traffic (clicks × CTR = estimated recovery)
- [ ] Fix missing-route pages with unique content
- [ ] Fix orphan pages with nav injection
- [ ] Add schema markup (TravelAgency/TouristTrip/FAQPage per page type)
- [ ] Generate XML sitemap
- [ ] Set up weekly broken-link monitoring cron
- [ ] Verify on latest deploy URL (CF Pages creates a new preview URL per push)
- [ ] Add custom domain → DNS CNAME switch

## Schema Strategy for Tour/Activity Operators

No perfect "kayak tour operator" schema exists. The optimal stack:

| Page Type | Schema Type | Key Properties |
|-----------|------------|----------------|
| Homepage | `TravelAgency` | NAP, geo, areaServed, priceRange, makesOffer |
| Tour pages | `TouristTrip` | name, itinerary, provider, offers, touristType |
| Rental pages | `Product` | name, offers (price), category |
| FAQ sections | `FAQPage` | mainEntity (Question/Answer pairs) |
| Hub pages | `ItemList` | itemListElement pointing to tours/rentals |

TravelAgency inherits from LocalBusiness and is specifically for businesses that book tours. It supports `makesOffer` → `Offer` → `TouristTrip`. No Oahu competitor uses any schema markup — first-mover advantage.

## AEO Quick Answer Block Spec

Placed immediately below H1 or key H2. Engineered for LLM extraction:
- **Length:** 50-75 words
- **Structure:** H2 question → standalone factual answer in first sentence
- **Stat density:** 2+ specific data points per block
- **Quote-readiness:** Every sentence is grammatically standalone

## CF Pages Deploy Pattern

Each `git push` creates a new preview URL (e.g., `a1b2c3d4.active-oahu-tours-mirror.pages.dev`). The production alias (`active-oahu-tours-mirror.pages.dev`) updates to the latest deploy within ~2 minutes. Always verify against the LATEST deploy URL from the CF API, not the production alias.
