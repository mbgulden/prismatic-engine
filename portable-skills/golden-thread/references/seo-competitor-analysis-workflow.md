# SEO Competitor Analysis Workflow (Ubersuggest MCP)

## When to Use

When evaluating SEO strategy for any venture with competitors. Load after `ubersuggest-mcp-connection.md` to ensure the MCP connection is active.

## Pre-Flight: Check Report Quota

Before calling ANY report-category tools (`domain_overview`, `domain_keywords`, `keyword_overview`, `match_keywords`), check whether the daily report limit (3/day) has been exhausted. These tools return `HTTP 403` when the limit is hit. See `ubersuggest-mcp-connection.md` for the full report-vs-non-report tool table.

**If reports are already exhausted, skip directly to Step 1b (Rate-Limited Fallback).** Do not burn calls that will 403 — use non-report tools instead.

## Step 1: Domain Overviews (All Competitors + You)

Pull `domain_overview` for your site and every competitor. Compare:

| Metric | Meaning |
|--------|---------|
| `organic` | Monthly organic traffic |
| `domainAuthority` | Moz-style DA (1-100) |
| `backlinks` | Total backlinks |
| `refDomains` | Unique referring domains |
| `domainTraffic` | Historical traffic trend (monthly) |

**Key insight:** Traffic trend matters more than absolute traffic. A site dropping from 3,391→1,513 tells a different story than one holding steady.

### Step 1b: Rate-Limited Fallback (when reports are exhausted)

When `domain_overview` returns 403 (daily report limit hit), use non-report tools to build the competitive landscape:

1. **`competitors`** for your domain — returns `traffic`, `organic`, `domainAuthority`, `backlinks`, `commonKeywordCount`, `gapKeywordCount` for all discovered competitors. This covers 80% of what `domain_overview` provides.
2. **`backlinks_overview`** for your domain + each competitor — returns `domainAuthority`, `backlinks`, `refDomains`, `refDomainsGovEdu`, `follow`/`noFollow`.
3. **Synthesize the table** from these two sources. Traffic numbers for YOUR domain may need estimation from prior known baselines.

**Example (from GRO-1171, Jun 2026):** Daily limit exhausted after 0 calls (prior sessions consumed quota). Built full competitive landscape using only `competitors` + `backlinks_overview` across 5 domains. The gap: no exact traffic number for your own domain — use the most recent known value.

## Step 2: Domain Keywords (Both Sides)

Pull `domain_keywords` for your site and top competitors (type=organic, limit=30). Look for:

- **Volume dominance:** Which keywords drive their traffic?
- **Position gaps:** Where are they #1-3 that you're #10+?
- **Commercial intent:** Are they ranking for buying-intent keywords or just informational?

**Rate-limited?** Skip to Step 2b (SERP Battle Map) — use `serp_analysis` on 10-15 target keywords to build a position-level competitive picture without keyword volume data.

### Step 2b: SERP Battle Map (when keywords are rate-limited)

`serp_analysis` is the most reliable Ubersuggest tool — 13+ calls succeeded in one session with no rate limits. Use it to build a keyword-by-keyword position comparison:

1. **Seed list:** Start with 10-15 high-intent keywords: "[activity] rental oahu", "[location] kayak", "self guided tour oahu", etc.
2. **For each keyword:** Call `serp_analysis` (limit=10). Extract your domain's position and each competitor's position from `serpEntries`.
3. **Categorize:**
   - 🟢 **You win:** Your position < competitor position
   - ⚠️ **Close:** Positions within 2 spots
   - 🔴 **They win:** Competitor position < your position
   - 🚨 **Missing:** You don't appear in top 10 at all
4. **Striking distance:** Any term where you're #4-10 is a candidate for immediate improvement (schema injection, content refresh, internal links).

**Example output format (from GRO-1171):**
```
| Keyword | AOT Position | KBA Position | Gap |
| chinamans hat kayak | #1 | #2 | -1 🟢 |
| snorkel rental oahu | N/A | #6 | MISSING 🚨 |
```

This replaces Step 3 (Top Pages) when both `domain_keywords` and `domain_top_pages` are rate-limited, since SERP data reveals which pages rank for which terms.

## Step 3: Top Pages (Competitors)

Pull `domain_top_pages` for each competitor. This reveals their actual content strategy — what pages Google rewards. Often informational pages (beach guides, how-to articles) drive more traffic than commercial pages (rental booking).

## Step 4: Competitive Reality Check (CRITICAL)

**Before making any claims about competitors, verify:**

1. Do they have a physical presence? (check their site for address, photos, storefront)
2. What platform are they on? (Squarespace = limited technical SEO; custom = more capability)
3. What's their traffic trend? (growing or declining?)
4. What content types drive their traffic? (guides vs commercial pages)
5. Do they have owner bylines or are they anonymous?

**Never assume a competitor is weak.** Michael has deep competitive intelligence from operating in the market for years. His assessment of competitors' physical presence, funding, political connections, and tactics should be treated as ground truth unless contradicted by hard data.

## Step 5: Strategy Synthesis

Based on the data, categorize keywords/geographies:

| Category | Approach |
|----------|----------|
| **Owned** (you rank #1-3) | Defend — update content, add schema, build links |
| **Contested** (both rank #4-10) | Out-quality — better E-E-A-T signals, AEO blocks, FAQPage schema |
| **Competitor-owned** (they rank #1-3) | Long-tail flank — target related lower-volume keywords they ignore |
| **Uncontested** (neither ranks well) | Claim first — be first to publish comprehensive content |

## Step 6: Backlink Gap (Optional)

Use `backlink_opportunity` with positive_targets (competitors) and negative_targets (your site). Even 5-10 new referring domains can move the needle when DA is in the 20-30 range.

## Output

Every competitor analysis should produce:
1. A traffic + authority comparison table (from `domain_overview` or `competitors` + `backlinks_overview` fallback)
2. Top 10 keyword opportunities (keyword, volume, your position, competitor position) — or SERP battle map table if volume data unavailable
3. Content cluster recommendation (which topics/geographies to target)
4. E-E-A-T strategy (how your content will be qualitatively better)
5. **Rate-limit note** — explicitly state which tools were blocked and what data is missing, so the next session knows what to re-run

**When key data is missing (rate-limited):** Include a "Recommended 24h Re-Sweep" section listing exactly which tools to re-run when limits reset. This prevents the next agent from re-discovering the same rate-limit wall.
