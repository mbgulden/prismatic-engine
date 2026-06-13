# Ubersuggest MCP Integration

## OAuth Connection Pattern

Ubersuggest MCP server at `https://ubersuggest-mcp.neilpatelapi.com/mcp` uses OAuth 2.0 with PKCE.

### Step 1: Register a client
```bash
curl -s -X POST "https://ubersuggest-mcp.neilpatelapi.com/register" \
  -H "Content-Type: application/json" \
  -d '{"client_name":"Hermes Agent","redirect_uris":["urn:ietf:wg:oauth:2.0:oob"],"scope":"profile domain keywords serp backlinks site_audit content"}'
```

### Step 2: Generate PKCE challenge
```python
import secrets, hashlib, base64
code_verifier = secrets.token_urlsafe(32)
digest = hashlib.sha256(code_verifier.encode()).digest()
code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
```

### Step 3: Build auth URL + give to user
```
https://ubersuggest-mcp.neilpatelapi.com/authorize?response_type=code&client_id=ubersuggest-mcp&redirect_uri=urn:ietf:wg:oauth:2.0:oob&scope=profile+domain+keywords+serp+backlinks+site_audit+content&code_challenge=CHALLENGE&code_challenge_method=S256&state=RANDOM
```

Safari can't handle `urn:ietf:wg:oauth:2.0:oob` — the server will redirect to its own callback page showing the code in the URL instead. User pastes the full URL or just the `?code=...` parameter.

### Step 4: Exchange code for token
```bash
curl -s -X POST "https://ubersuggest-mcp.neilpatelapi.com/token" \
  -d "grant_type=authorization_code" -d "client_id=ubersuggest-mcp" \
  -d "code=CODE_FROM_USER" -d "redirect_uri=urn:ietf:wg:oauth:2.0:oob" \
  -d "code_verifier=SAVED_VERIFIER"
```
Returns `access_token` (10-day expiry) and `refresh_token`. Save both.

### Step 5: Connect Python client
```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import json, asyncio

async def call(session, tool, args):
    result = await session.call_tool(tool, args)
    return json.loads(result.content[0].text)

async def main():
    url = "https://ubersuggest-mcp.neilpatelapi.com/mcp"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            data = await call(session, "domain_overview", {"domain": "example.com"})
            print(data)
```

**Critical:** Use `streamablehttp_client`, NOT `sse_client`. SSE mode has auth header bugs with the Python MCP SDK.

## Key Tools for SEO Research

| Tool | Use |
|------|-----|
| `domain_overview` | Traffic, keywords, DA, backlinks for any domain |
| `domain_keywords` | All organic/paid keywords with volume and position |
| `domain_top_pages` | Top pages ranked by estimated traffic |
| `competitors` | Main organic competitors for a domain |
| `keyword_suggestions` | Related, questions, comparisons for seed terms |
| `serp_analysis` | Who ranks for a keyword + their metrics |
| `backlink_opportunity` | Domains linking to competitors but not you |
| `content_ideas` | Top-performing content for a keyword by social shares |

## Token Storage
Save to `/tmp/ubs_token` and `/tmp/ubs_refresh`. Token expires in ~10 days. Refresh before expiry.
