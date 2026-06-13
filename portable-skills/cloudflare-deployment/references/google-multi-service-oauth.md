# Google Multi-Service OAuth Setup

Pattern for adding new Google APIs (Analytics, Search Console, Tag Manager, GMB) to an existing OAuth flow without losing existing scopes.

## The Problem

The GDrive MCP server at `/home/ubuntu/work/local-gdrive-mcp/` starts with `drive.readonly` and `spreadsheets` scopes. When you need to add more Google services (GA4, Search Console, GTM, GMB), the existing tokens don't have the new scopes.

## The Pattern

### 1. Generate a Comprehensive Auth URL

```bash
cd /home/ubuntu/work/local-gdrive-mcp
node get_full_auth_url.js
```

The script uses the same OAuth client but requests ALL needed scopes:

```javascript
const authUrl = client.generateAuthUrl({
    access_type: 'offline',
    prompt: 'consent',  // Force re-consent for new scopes
    scope: [
        'https://www.googleapis.com/auth/drive.readonly',
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/analytics.readonly',
        'https://www.googleapis.com/auth/webmasters.readonly',
        'https://www.googleapis.com/auth/tagmanager.readonly',
        'https://www.googleapis.com/auth/business.manage',
    ]
});
```

### 2. User Authorizes → Exchange Code

Same as existing flow: user opens URL → authorizes → copies `code` from redirect → paste:

```bash
node manual_auth.js "4/0AeoWuM-..."
```

### 3. Verify Each API Works

After token refresh, test each service individually. Some will fail because the API isn't enabled in the GCP project:

```javascript
// GA4 Admin (property discovery)
const analytics = google.analyticsadmin({version: 'v1beta', auth});
await analytics.accountSummaries.list({});

// Search Console (site list)
const sc = google.webmasters({version: 'v3', auth});
await sc.sites.list({});

// Tag Manager (containers)
const tm = google.tagmanager({version: 'v2', auth});
await tm.accounts.list({});

// Google My Business (locations)
const mb = google.mybusinessaccountmanagement({version: 'v1', auth});
await mb.accounts.list({});
```

### 4. Enable APIs in GCP Console

Each API needs to be enabled in the GCP project. The error message includes the direct enable link:

```
Google Analytics Admin API has not been used in project 977861670312 before or it is disabled.
Enable it by visiting:
https://console.developers.google.com/apis/api/analyticsadmin.googleapis.com/overview?project=977861670312
```

Common APIs to enable:
- `analyticsadmin.googleapis.com` — GA4 property discovery
- `analyticsdata.googleapis.com` — GA4 report data
- `searchconsole.googleapis.com` — Search Console
- `tagmanager.googleapis.com` — Tag Manager
- `mybusinessaccountmanagement.googleapis.com` — GMB account management
- `mybusinessbusinessinformation.googleapis.com` — GMB location details

### 5. Rate Limits

Newly enabled APIs may have zero quota initially. If you get `Quota exceeded for quota metric 'Requests' and limit 'Requests per minute'`, wait 5-10 minutes for quota propagation. This is normal for freshly-enabled APIs.

**Persistent rate limit:** Some APIs (particularly `mybusinessaccountmanagement.googleapis.com`) can return sustained rate limits even after 5+ minutes of waiting. If `Requests per minute` persists across multiple attempts spaced 60+ seconds apart, the quota may not have propagated yet or the project may have a 0-quota default. Retry every 5 minutes up to 3 times, then escalate to the user for GCP console quota check.

**Verification:** After the API starts working, immediately pull account data and cache it. API discovery (listing accounts/properties/sites) rarely needs to be repeated — cache the IDs.

### Key Account/Property IDs (Active Oahu Tours)

For quick reference — these are the live IDs discovered this session:

| Service | Account | Property/ID |
|---|---|---|
| GA4 | activeoahutours (44575351) | properties/289642224 |
| Search Console | sc-domain:activeoahutours.com | Owner |
| Tag Manager | Active Oahu Tours (10319161) | GTM-P55TSP, Container: 626981 |
| GMB | michael@growthwebdev.com | (rate-limited, retry later) |
| GCP Project | 977861670312 | OAuth client |

### Ubersuggest OAuth (PKCE)

Ubersuggest's MCP server at `https://ubersuggest-mcp.neilpatelapi.com/mcp` uses PKCE OAuth 2.0. Key details from their `.well-known/oauth-authorization-server`:

- **Token endpoint:** `https://ubersuggest-mcp.neilpatelapi.com/token`
- **Auth endpoint:** `https://ubersuggest-mcp.neilpatelapi.com/authorize`
- **Registration endpoint:** `https://ubersuggest-mcp.neilpatelapi.com/register`
- **Client ID:** `ubersuggest-mcp` (discovered via registration — NOT a custom value)
- **PKCE:** S256 required
- **Scopes:** profile, domain, keywords, serp, backlinks, site_audit, content, projects, utility

**Pitfall:** Using a self-invented client_id (like `hermes-agent`) produces tokens that the MCP endpoint rejects. ALWAYS call the registration endpoint first to get the correct client_id.

**Current status (May 2026):** Token validation backend returns 502. The OAuth flow works but the MCP gateway can't validate tokens. Server-side outage — retry periodically.

### 6. Read-Write vs Read-Only

The initial setup uses read-only scopes where possible (`analytics.readonly`, `webmasters.readonly`, `tagmanager.readonly`). For GMB, use `business.manage` for read-write (needed to view location details). Match the scope to what the task needs — don't request write access unless required.

## GA4 Property ID Discovery
## GA4 Property ID Discovery

GA4 uses two types of IDs:
- **Measurement ID**: `G-PRRRLMBR8Z` (seen in gtag snippets)
- **Property ID**: `properties/289642224` (used in API calls)
The Admin API returns both. After discovering accounts, map the Measurement ID to the Property ID for Data API calls:

```javascript
// Discovery
const accts = await analytics.accountSummaries.list({});
for (const a of accts.data.accountSummaries || []) {
    for (const p of a.propertySummaries || []) {
        console.log(`${p.displayName}: ${p.property} (${p.propertyType})`);
    }
}

// Data API — use the property ID, not measurement ID
const prop = 'properties/289642224'; // Active Oahu Tours - GA4
const report = await analyticsData.properties.runReport({
    property: prop,
    requestBody: {
        dateRanges: [{ startDate: '2026-02-28', endDate: '2026-05-30' }],
        metrics: [{ name: 'totalUsers' }, { name: 'sessions' }],
        dimensions: [{ name: 'landingPage' }],
        limit: 20,
    }
});
```

**CRITICAL: Verify the property ID before pulling data.** `accountSummaries.list()` returns ALL accounts the user has access to, ordered by account creation, not relevance. The first property may be a completely unrelated site (e.g., a photo portfolio). Always cross-reference with the Measurement ID from the live site's gtag snippet. If the data looks wrong (404.html as top landing page, or unfamiliar domain names), you're pulling the wrong property. Look for the account named after the target site.

## Search Console: Domain vs URL Prefix

Search Console supports two property types. The API uses different siteUrl values:
- **Domain property**: `sc-domain:activeoahutours.com` (includes all subdomains + protocols)
- **URL prefix**: `https://activeoahutours.com/` (specific protocol + path)

Use the domain property for the broadest data. The `sites.list()` endpoint shows which type you have access to.
