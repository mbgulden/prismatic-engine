# Competitor SEO Audit Methodology

## When the user corrects your assumptions about a competitor

The user knows their competitors better than you. When they push back on a claim about a competitor's capabilities, physical presence, or content quality — accept the correction immediately and re-analyze from data. Don't defend the wrong read.

## Audit Checklist (before making any competitive claims)

### 1. Check Ubersuggest MCP
- `domain_overview` — traffic, keywords, DA, backlinks
- `domain_keywords` (organic, limit 30-50) — what they actually rank for
- `domain_top_pages` — what pages drive their traffic
- `serp_analysis` on target keywords — do they appear in results?

### 2. Check their website directly
- curl their site — look for address, phone, hours, owner name, "about" content
- Look for Squarespace/WordPress JSON metadata (reveals physical address, social profiles)
- Check for author bylines, team pages, "our story"
- Note: Squarespace sites embed full business data in `<script data-name="static-context">` blocks

### 3. Google Maps / GMB
- Search their business name — verify address, storefront, photos
- Check if they have a GMB profile and reviews

### 4. Verify before claiming
- "They don't have a shop" → verify address on their site + Google Maps
- "They don't rank for X" → verify with `serp_analysis` or `domain_keywords`
- "Their content is generic/AI-written" → read their top pages first
- "They can't do Y" → check their site for evidence they already do Y

## Example: KBA Audit Results

| Claim | Verdict | Evidence |
|-------|---------|----------|
| "No physical shop" | ❌ Wrong | 130 Kailua Rd, 6 storefronts, found in Squarespace JSON metadata |
| "No owner persona" | ✅ True | Zero author bylines, anonymous content |
| "No Kaneohe Bay content" | ❌ Wrong | `/kayaking-in-kaneohe`, `/kayaking-chinamans-hat-mokolii-kualoa` |
| "Generic SEO content" | ⚠️ Mixed | Decent detail but lacks first-person experience, no original data |
| "No schema markup" | ✅ True | Squarespace default only, no Tourism/FAQ/Product schema |

The user's pushback about KBA's physical presence and competitive aggression was correct. The audit revealed KBA is formidable but has exploitable weaknesses: no owner byline, no authentic first-hand content, no advanced schema markup, no presence in non-Kailua geographies.
