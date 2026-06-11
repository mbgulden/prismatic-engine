# Ubersuggest MCP Connection via OAuth

## Quick Connect

1. Install MCP Python SDK: `pip install --break-system-packages mcp`
2. Register OAuth client + get auth URL
3. User opens URL in browser, logs in, gets authorization code
4. Exchange code for token at `/token` endpoint
5. Connect using `streamablehttp_client` (NOT `sse_client` — SSE returns 401)

## OAuth Endpoints

```
Authorization Server: https://ubersuggest-mcp.neilpatelapi.com/
Token Endpoint:       https://ubersuggest-mcp.neilpatelapi.com/token
Authorize Endpoint:   https://ubersuggest-mcp.neilpatelapi.com/authorize
Register Endpoint:    https://ubersuggest-mcp.neilpatelapi.com/register
MCP Server URL:       https://ubersuggest-mcp.neilpatelapi.com/mcp
```

## Full Connection Script

```python
import secrets, hashlib, base64, urllib.parse, json, urllib.request

# Step 1: Register client
reg = urllib.request.Request("https://ubersuggest-mcp.neilpatelapi.com/register",
    data=json.dumps({
        "client_name": "Hermes Agent",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
        "scope": "profile domain keywords serp backlinks site_audit content"
    }).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(reg, timeout=10) as r:
    reg_data = json.loads(r.read())
# client_id will be "ubersuggest-mcp"

# Step 2: Generate PKCE
code_verifier = secrets.token_urlsafe(32)
digest = hashlib.sha256(code_verifier.encode()).digest()
code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()

# Step 3: Build auth URL (give this to user)
params = {
    'response_type': 'code',
    'client_id': 'ubersuggest-mcp',
    'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
    'scope': 'profile domain keywords serp backlinks site_audit content',
    'code_challenge': code_challenge,
    'code_challenge_method': 'S256',
    'state': secrets.token_urlsafe(8)
}
auth_url = f"https://ubersuggest-mcp.neilpatelapi.com/authorize?{urllib.parse.urlencode(params)}"
# User opens this URL, logs in, gets redirected to a page showing the authorization code

# Step 4: Exchange code for token (after user provides the code)
token_data = urllib.parse.urlencode({
    "grant_type": "authorization_code",
    "client_id": "ubersuggest-mcp",
    "code": "CODE_FROM_USER",
    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
    "code_verifier": code_verifier
}).encode()
token_req = urllib.request.Request("https://ubersuggest-mcp.neilpatelapi.com/token", data=token_data)
with urllib.request.urlopen(token_req, timeout=10) as r:
    token = json.loads(r.read())
# token["access_token"], token["refresh_token"], token["expires_in"] (864000 = 10 days)

# Step 5: Connect using Python MCP SDK
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def call_ubersuggest(tool_name, args):
    access_token = open('/tmp/ubs_token').read().strip()
    url = "https://ubersuggest-mcp.neilpatelapi.com/mcp"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            return json.loads(result.content[0].text) if result.content else {}

# Save token for reuse
with open('/tmp/ubs_token', 'w') as f: f.write(token['access_token'])
with open('/tmp/ubs_refresh', 'w') as f: f.write(token['refresh_token'])
```

## Key Gotchas

- Use `streamablehttp_client` NOT `sse_client` — SSE returns 401 even with valid Bearer token
- The OOB redirect URI (`urn:ietf:wg:oauth:2.0:oob`) works — the server redirects to its own callback page showing the code
- Safari can't handle `urn:` protocol URIs, but the server handles it by showing the code on its own page
- Authorization codes are single-use — if the token exchange fails, generate a new PKCE and get a fresh code
- Token expires in 10 days (864,000 seconds) — refresh before expiry
- **Single session handles 10+ tool calls reliably** — contrary to the "3-4 calls max" advice sometimes seen in issue descriptions. A single `async with streamablehttp_client(...) as (read, write, _): async with ClientSession(read, write) as session:` block successfully handled 13 `serp_analysis` calls + `competitors` + `backlinks_overview` in one session (GRO-1171, Jun 2026). No need to tear down and reconnect between calls — one session, loop over tools.

## Daily Report Limit (3/day) — CRITICAL

Ubersuggest enforces a hard daily limit of **3 reports per day** across the account. The following tools count as reports and will return `HTTP 403 "You have reached the daily reports limit: 3"` once exhausted:

| Tool | Counts as Report? |
|------|-------------------|
| `domain_overview` | ✅ YES — 403 after 3 calls |
| `domain_keywords` | ✅ YES — 403 after 3 calls |
| `keyword_overview` | ✅ YES — 403 after 3 calls |
| `match_keywords` | ✅ YES — 403 after 3 calls |
| `traffic_value` | ✅ YES — 403 after 3 calls |
| `competitors` | ❌ NO — works after limit |
| `backlinks_overview` | ❌ NO — works after limit |
| `serp_analysis` | ❌ NO — works reliably (tested 13 calls) |
| `google_suggestions` | ⚠️ Separate 429 rate limit |
| `content_ideas` | ❌ Not tested post-limit |

**Fallback when reports are exhausted:** Use `competitors` (returns traffic, DA, keyword counts for all competitors — covers 80% of what `domain_overview` gives), `backlinks_overview` (DA, backlinks, refDomains), and `serp_analysis` (position-level competitive data). SERP analysis is the workhorse — it never hit a rate limit across 13 calls in one session.

**Re-run window:** Limits reset daily. Schedule full keyword/overview sweeps for a separate session 24+ hours after the previous sweep.

## Tool Argument Quirks

| Tool | Quirk | Fix |
|------|-------|-----|
| `content_ideas` | `sortby` rejects `"visits"` | Use `"estVisits"` or `"-estVisits"` |
| `match_keywords` | Rejects 4+ seed keywords | Max 3 seeds. Error: "Provide 1 to 3 seed keywords." |
| `backlink_opportunity` | `positive_targets` rejects strings | Pass array of objects: `[{"domain": "competitor.com"}]` |
| `google_suggestions` | 429 rate limit after ~1 call | Space out calls, use sparingly |
| `domain_keywords` | Returns raw list (not dict with `data` key) | Iterate directly: `for item in result:` each has `keyword`, `position`, `volume`, `traffic` |
| `serp_analysis` | Returns `{"serpEntries": [...]}` | Access `data["serpEntries"]`; each entry has `domain`, `position`, `type`, `clicks`, `domainAuthority` |

## Available Tools

Key tools for SEO analysis:
- `domain_overview` — traffic, keywords, DA, backlinks summary
- `domain_keywords` — organic/paid keywords with volume, position, difficulty
- `domain_top_pages` — pages ranked by estimated traffic
- `competitors` — main organic competitors
- `keyword_overview` — volume, CPC, difficulty
- `keyword_suggestions` — related, questions, comparisons, prepositions
- `serp_analysis` — SERP results for a keyword
- `backlinks_overview` / `backlinks` / `linking_domains`
- `backlink_opportunity` — domains linking to competitors but not you
- `content_ideas` — top-performing content for keywords
- `site_audit` — crawl a domain (paid feature)
