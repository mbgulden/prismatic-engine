# Ubersuggest MCP — OAuth 2.0 Connection Pattern

## Overview

Ubersuggest exposes an MCP server at `https://ubersuggest-mcp.neilpatelapi.com/mcp` with OAuth 2.0 (authorization_code grant + PKCE). This reference documents the connection flow for headless/CLI environments where browser-based redirects are impractical.

## Available Scopes

`profile`, `domain`, `keywords`, `serp`, `backlinks`, `site_audit`, `content`, `projects`, `utility`

## Connection Flow (Headless)

### 1. Register Client

```bash
curl -s -X POST "https://ubersuggest-mcp.neilpatelapi.com/register" \
  -H "Content-Type: application/json" \
  -d '{"client_name":"<name>","redirect_uris":["urn:ietf:wg:oauth:2.0:oob"],"scope":"profile domain keywords serp backlinks site_audit content"}'
```

Client ID is always `ubersuggest-mcp`. No client secret — token endpoint uses `none` auth method.

### 2. Generate PKCE Challenge

```python
import secrets, hashlib, base64

code_verifier = secrets.token_urlsafe(32)
digest = hashlib.sha256(code_verifier.encode()).digest()
code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
```

Save `code_verifier` — needed in step 4.

### 3. Build Authorization URL

```
https://ubersuggest-mcp.neilpatelapi.com/authorize?
  response_type=code&
  client_id=ubersuggest-mcp&
  redirect_uri=urn:ietf:wg:oauth:2.0:oob&
  scope=profile+domain+keywords+serp+backlinks+site_audit+content&
  code_challenge=<CHALLENGE>&
  code_challenge_method=S256&
  state=<RANDOM>
```

**Redirect URI options:**
- `urn:ietf:wg:oauth:2.0:oob` — OOB flow. Safari cannot open this URI; Chrome/Firefox may show the code in the URL bar. The server often redirects to its own `/callback?code=...` page instead, which works in all browsers.
- `http://localhost:<port>/callback` — requires a local HTTP server + Cloudflare Tunnel (see below).
- `https://<tunnel-url>/callback` — start callback server, expose via `cloudflared tunnel --url`, register with that URL.

**User flow:** Open URL in browser → log in to Ubersuggest → server redirects to a page showing the authorization code. Paste the code.

### 4. Exchange Code for Token

```bash
curl -s -X POST "https://ubersuggest-mcp.neilpatelapi.com/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "client_id=ubersuggest-mcp" \
  -d "code=<CODE>" \
  -d "redirect_uri=urn:ietf:wg:oauth:2.0:oob" \
  -d "code_verifier=<VERIFIER>"
```

Returns:
```json
{
  "token_type": "Bearer",
  "access_token": "ubs_oa...",
  "expires_in": 864000,
  "refresh_token": "ubs_oa...",
  "scope": "profile domain keywords serp backlinks site_audit content"
}
```

**CRITICAL:** Save the token immediately. The authorization code is single-use — if the exchange succeeds but the token isn't saved, you need a new auth code from the user.

Save pattern:
```python
with open('/tmp/ubs_token', 'w') as f:
    f.write(token_data["access_token"])
with open('/tmp/ubs_refresh', 'w') as f:
    f.write(token_data["refresh_token"])
```

### 5. Connect to MCP Server

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client  # PREFERRED over sse_client

with open('/tmp/ubs_token') as f:
    TOKEN = f.read().strip()

headers = {"Authorization": f"Bearer {TOKEN}"}
async with streamablehttp_client(url, headers=headers) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        # Call tools:
        result = await session.call_tool("domain_overview", {"domain": "example.com"})
```

## Token Management

- Access token expires: 10 days (864,000 seconds)
- Refresh token endpoint: same as token endpoint with `grant_type=refresh_token`
- Save both tokens; refresh before expiry

## Callback Server + Tunnel Pattern (for real redirect URIs)

When OOB doesn't work (e.g., Safari), use a Cloudflare Tunnel:

```python
# Start callback server on port 8092
from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('code', [''])[0]
        print(f"CODE={code}", flush=True)
        self.send_response(200); self.end_headers()
        self.wfile.write(f'<h1>Code: {code}</h1>'.encode())
server = HTTPServer(('0.0.0.0', 8092), Handler)
server.serve_forever()
```

```bash
# Expose via Cloudflare Tunnel (in background)
cloudflared tunnel --url http://localhost:8092 --no-autoupdate
# → https://<random>.trycloudflare.com
```

Register with the tunnel URL as `redirect_uri`, then the OAuth redirect reaches your server.

## Priority Reports

Once connected, these Ubersuggest reports have the highest SEO ROI:

| # | Tool | What | Why |
|---|------|------|-----|
| 1 | `keyword_gap` | Your site vs top competitor | Shows every keyword they rank for that you don't — this IS your content calendar |
| 2 | `domain_overview` | Each competitor | Traffic, top pages, keyword count, backlinks |
| 3 | `top_pages` | Top competitor only | Their 20 highest-traffic pages — match or build better versions |
| 4 | `backlink_gap` | Who links to them but not you | 5-10 link targets = meaningful DA boost |
| 5 | `site_audit` | Your own site | Technical issues, validates recent changes |
