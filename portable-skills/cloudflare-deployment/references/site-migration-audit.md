# Site Migration Audit — Methodology

Full-audit playbook for migrating a WordPress (or any) site to a new platform (Astro/CF Pages). Produces a master tracking spreadsheet every page owner can review.

## Phase 1: Pull the Sitemap Universe

WordPress with Yoast SEO uses a sitemap index pattern:

```bash
# 1. Get the sitemap index
curl -sL https://site.com/sitemap_index.xml

# 2. Pull each sub-sitemap
curl -sL https://site.com/page-sitemap.xml
curl -sL https://site.com/activities-sitemap.xml
curl -sL https://site.com/rentals-sitemap.xml
# ... etc for all sub-sitemaps
```

Parse each `<url>` → extract `<loc>`, `<lastmod>`, optional `<image:title>` / `<image:caption>`.

## Phase 2: Pull the New Site's Sitemap

```bash
curl -sL https://new-site.pages.dev/sitemap-0.xml
```

This gives the "ASTRO URL" column for cross-referencing.

## Phase 3: Spider Crawl for Stragglers

Sitemaps miss pages. A spider crawl of the live site catches:

- **Translation pages** (Weglot `/ja/` — sitemaps don't include these)
- **Pagination** (`/activities/page/2/`, `/reviews/page/<?>/`)
- **Orphaned pages** (in sitemap but not linked from any page)
- **Trailing-slash variants** (`/contact-us` vs `/contact-us/`)
- **Legacy pages** (old job postings, campaign landing pages)

### Spider Crawl Python Script

```python
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()
    def handle_starttag(self, tag, attrs):
        if tag in ('a', 'link'):
            for name, value in attrs:
                if name == 'href' and value:
                    self.links.add(value)

BASE = "https://site.com"
visited = set()
found = set()
queue = [BASE + "/"]

for _ in range(200):
    if not queue: break
    url = queue.pop(0).split('#')[0].split('?')[0]
    if url in visited or not url.startswith(BASE): continue
    visited.add(url)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            if 'text/html' not in resp.headers.get('Content-Type', ''): continue
            parser = LinkExtractor()
            parser.feed(resp.read().decode('utf-8', errors='ignore'))
            for link in parser.links:
                full = urljoin(url, link).split('#')[0].split('?')[0]
                if full.startswith(BASE) and full not in visited:
                    found.add(full)
                    queue.append(full)
    except: pass

all_crawled = visited | found
```

Diff crawled paths against known sitemap paths:

```python
known = {urlparse(u).path for u in sitemap_urls}
crawled = {urlparse(u).path for u in all_crawled}

stragglers = crawled - known     # linked but NOT in sitemap
orphaned = known - crawled       # in sitemap but unreachable
```

### WP REST API — Find Pages the Spider Missed

Spiders only find linked pages. Use the WP REST API to discover ALL published content, including pages with no internal links:

```bash
# Discover custom post types
curl -sL https://site.com/wp-json/wp/v2/types | python3 -c "
import json,sys
d=json.load(sys.stdin)
[print(f'{k}: {v.get(\"name\",\"?\")} ({v.get(\"rest_base\",\"?\")})') for k,v in d.items()]
"

# Pull all pages, posts, and custom post type URLs
# Pages: /wp-json/wp/v2/pages?per_page=100&status=publish&_fields=link
# Posts: /wp-json/wp/v2/posts?per_page=100&status=publish&_fields=link
# Rentals: /wp-json/wp/v2/rentals?per_page=100&status=publish&_fields=link
# Reviews: /wp-json/wp/v2/reviews?per_page=100&status=publish&_fields=link
```

Paginate through each endpoint until it returns an empty array or 400. Collect ALL URLs into a set. Then diff against the mirrored files to find what wget missed. This catches review subpages, deep blog posts, and rental detail pages that aren't in the main navigation.

## Phase 4: Build the Master Tracking CSV

Columns:

| Column | Purpose |
|--------|---------|
| `ID` | Sequential row number |
| `Section` | TOURS, RENTALS, BLOG, STATIC, JAPANESE, PAGINATION, REDIRECT, MISC |
| `WordPress URL` | Full old URL (or "—" if new-only) |
| `WP Title / Topic` | Human-readable label |
| `Astro URL` | New URL if built (or "—") |
| `FareHarbor ID` | For tour pages (or "—") |
| `Status` | Coded status (see below) |
| `Notes` | Actionable note for the reviewer |

### Status Codes

```
MIGRATED          — Old page has a working new equivalent
MISSING_IN_ASTRO  — Real page on old site, no new page yet (PRIORITY)
HALLUCINATED      — Page on new site that doesn't match any real old page
REPLACED          — Was hallucinated, now replaced with real content
NEW               — Brand new page with no old equivalent
ASTRO_ONLY        — Custom page created for new site
NOT_MIGRATED      — Old page exists, no new equivalent (low priority)
DECISION_NEEDED   — Requires human decision (translations, redirects)
REDIRECT          — Should 301 to canonical URL
DUPLICATE         — Redundant page (merge or delete)
DELETE            — Remove entirely
REVIEW_NEEDED     — Exists on both but content alignment uncertain
PARTIAL           — Partial migration (some sub-pages missing)
```

### Section Categories

- **TOURS**: Bookable tour/activity pages with FareHarbor IDs
- **RENTALS**: Equipment rental pages
- **BLOG**: Blog posts / guides (may map to `/guides/` in Astro)
- **STATIC**: About, contact, FAQ, privacy, reviews, etc.
- **JAPANESE**: Weglot translation pages (`/ja/*`)
- **PAGINATION**: Archive pagination pages
- **LANDING**: Hub/category landing pages
- **RENTAL_INFO**: Rental-adjacent info pages (delivery, partners, etc.)
- **MISC**: Everything else

## Phase 5: Identify the Critical Gaps

After building the CSV, run counts:

```python
from collections import Counter
statuses = Counter(r["Status"] for r in rows)
# Focus on: MISSING_IN_ASTRO, HALLUCINATED, DECISION_NEEDED
```

Priority order for remediation:
1. **HALLUCINATED** — replace with real content immediately (they're live on the new site with wrong info)
2. **MISSING_IN_ASTRO (TOURS)** — these are revenue pages with FareHarbor IDs
3. **MISSING_IN_ASTRO (RENTALS)** — second revenue stream
4. **DECISION_NEEDED** — ask the user (translations, redirect strategy)
5. **REDIRECT / DELETE** — quick wins, can batch

## Phase 6: Deliver the Spreadsheet

Google Sheets is ideal (shareable, commentable). Fallback: commit the CSV into the site's dist/ folder so it's downloadable at `https://site.pages.dev/audit.csv`.

### CSV Delivery Pattern
```bash
cp audit.csv project/dist/
git add -f project/dist/audit.csv
git commit -m "Add migration audit CSV"
git push
```

## Google Sheets Delivery (Preferred)

Google Sheets is better than CSV — shareable, commentable, multi-sheet. When the GDrive MCP's read-only tools are insufficient for creating new sheets, use the `googleapis` Node SDK directly from `/home/ubuntu/work/local-gdrive-mcp/`:

```javascript
import { google } from 'googleapis';

// Auth (reuse MCP credentials)
const creds = JSON.parse(fs.readFileSync(
  '/home/ubuntu/.config/mcp-gdrive/.gdrive-server-credentials.json', 'utf8'));
const keys = JSON.parse(fs.readFileSync(
  '/home/ubuntu/.config/mcp-gdrive/gcp-oauth.keys.json', 'utf8'));
const cfg = keys.installed || keys.web;
const auth = new google.auth.OAuth2(cfg.client_id, cfg.client_secret, 'http://localhost:8085');
auth.setCredentials(creds);

const sheets = google.sheets({ version: 'v4', auth });

// 1. Create spreadsheet
const res = await sheets.spreadsheets.create({
  requestBody: { properties: { title: 'Site Migration Audit — Example.com' } }
});
const SID = res.data.spreadsheetId;

// 2. Populate Sheet1 with audit data
await sheets.spreadsheets.values.update({
  spreadsheetId: SID, range: 'Sheet1!A1', valueInputOption: 'RAW',
  requestBody: { values: csvAsArrays }
});

// 3. Create additional sheets (Summary, Priority)
await sheets.spreadsheets.batchUpdate({
  spreadsheetId: SID,
  requestBody: {
    requests: [
      { addSheet: { properties: { title: 'Summary' } } },
      { addSheet: { properties: { title: 'Priority' } } }
    ]
  }
});

// 4. Populate new sheets
await sheets.spreadsheets.values.update({
  spreadsheetId: SID, range: 'Summary!A1', valueInputOption: 'RAW',
  requestBody: { values: summaryRows }
});
```

**Critical pitfall:** Do NOT write to a sheet that doesn't exist. Use `batchUpdate` → `addSheet` first. Writing to `Summary!A1` before the Summary tab exists throws `Unable to parse range`.

**Sharing:** The MCP server's `drive.readonly` scope cannot modify permissions. The spreadsheet creator (the authenticated user) must share it manually via Google Drive UI, or share the link and let recipients request access.

**Token recovery**: If the GDrive MCP returns `invalid_grant`, run the re-auth procedure documented in `offline-mcp-server-building/references/gdrive-mcp-auth-recovery.md`.

## Common Findings

### Weglot Translations
WordPress sites with Weglot have `/ja/`, `/es/`, `/fr/` etc. translated pages. These are real pages with real traffic but are hidden from sitemaps. The spider crawl catches them. Decision: keep translations (requires i18n setup), redirect all to English, or rebuild with proper i18n.

### Pagination Traps
WordPress archive pages generate `/page/2/`, `/page/3/` etc. These aren't in sitemaps but are linked from archive indexes. Always 301 redirect to the main section page.

### Orphaned Pages
Pages in the sitemap but unreachable by crawl have no internal links. They may still get search traffic. Check Search Console data before deleting.

### FareHarbor ID Mapping
For tour pages, FareHarbor item IDs (e.g., `491549`) are the source of truth. Every tour page MUST have the correct FH ID. The fact sheet / pricing sheet is the canonical source — cross-reference against the sitemap.
