---
type: Integration
title: Cloudflare Tunnel — webhook routing setup
description: How the Cloudflare Tunnel 'Growth Web v2' routes webhooks.growthwebdev.com/webhooks/{linear,github} to the Prismatic Engine gateway on port 9000. Includes the API call to update, env vars needed, and verification steps.
resource: okf/integrations/cloudflare-tunnel-webhooks.md
tags: [integration, cloudflare, tunnel, webhook, routing, prismatic-engine]
timestamp: 2026-06-19T21:00:00Z
linear_issue: GRO-2084
---

# Cloudflare Tunnel — Webhook Routing Setup

This doc captures how `webhooks.growthwebdev.com` is routed to the Prismatic Engine gateway via Cloudflare Tunnel. It includes the API call used to set it up, the current config, and how to verify or modify it.

## Current state (as of 2026-06-19)

| Subdomain | Backend | Notes |
|---|---|---|
| `webhooks.growthwebdev.com` | `http://127.0.0.1:9000` | Prismatic Engine gateway (this session's setup) |
| `hermes.growthwebdev.com` | `http://127.0.0.1:9119` | Hermes orchestrator |
| `code.growthwebdev.com` | `http://127.0.0.1:8080` | Code service |
| `sentinel.growthwebdev.com` | k8s service | Sentinel backend |
| `beyondsaas.ai`, `www.beyondsaas.ai` | `http://127.0.0.1:8090` | BeyondSaaS site |
| `*` (catch-all) | `http_status:404` | No match → 404 |

Tunnel ID: `abe7bbd9-ff25-4c1e-be14-3efc5ea27bce` (named "Growth Web v2")
Tunnel status: `healthy` (live)

## DNS record

```
webhooks.growthwebdev.com  CNAME  abe7bbd9-ff25-4c1e-be14-3efc5ea27bce.cfargotunnel.com
```

(DNS is auto-managed by Cloudflare when you create a tunnel; you don't need to touch it manually.)

## How to update the routing (the API call I used)

```python
import json, urllib.request, os

ACCT = os.environ['CLOUDFLARE_PAGES_ACCOUNT_ID']
EMAIL = os.environ['CLOUDFLARE_GROWTHWEB_EMAIL']
KEY = os.environ['CLOUDFLARE_GROWTHWEB_API_KEY']
TUNNEL_ID = 'abe7bbd9-ff25-4c1e-be14-3efc5ea27bce'

# 1. Get current config
req = urllib.request.Request(
    f'https://api.cloudflare.com/client/v4/accounts/{ACCT}/cfd_tunnel/{TUNNEL_ID}/configurations',
    headers={'X-Auth-Email': EMAIL, 'X-Auth-Key': KEY},
)
with urllib.request.urlopen(req, timeout=30) as r:
    config = json.loads(r.read().decode())['result']['config']

# 2. Modify the ingress rule you want
new_ingress = []
for rule in config['ingress']:
    new_rule = dict(rule)
    if rule.get('hostname') == 'webhooks.growthwebdev.com':
        new_rule['service'] = 'http://127.0.0.1:9000'  # point to Prismatic Engine
    new_ingress.append(new_rule)

# 3. PUT the updated config
new_config = {'config': {**config, 'ingress': new_ingress}}
put_req = urllib.request.Request(
    f'https://api.cloudflare.com/client/v4/accounts/{ACCT}/cfd_tunnel/{TUNNEL_ID}/configurations',
    data=json.dumps(new_config).encode(),
    headers={'X-Auth-Email': EMAIL, 'X-Auth-Key': KEY, 'Content-Type': 'application/json'},
    method='PUT',
)
with urllib.request.urlopen(put_req, timeout=30) as r:
    result = json.loads(r.read().decode())
    assert result['success'], result
```

The `cloudflared` daemon picks up the new config automatically within ~5 seconds. No restart needed.

## Why port 9000 (Prismatic Engine) and not 8644 (Hermes)?

Previously, `webhooks.growthwebdev.com` routed to **port 8644** which is Hermes's gateway. Hermes's webhook platform was a stub: it validated HMAC and logged events but didn't dispatch. Real dispatch was happening via the 5-min `agent_dispatcher.py` cron.

The new Prismatic Engine gateway (port 9000) **does the actual dispatch** in the webhook handler itself. That's the production-grade path. Routing the tunnel to it eliminates the dependency on Hermes's webhook layer entirely.

## Required env vars in the gateway systemd unit

For the gateway to accept connections from the tunnel, these env vars must include the box's public IP (or the cloudflared exit IP):

```
PRISMATIC_ALLOWED_IPS=127.0.0.1,::1,<public-ip>
PRISMATIC_TRUSTED_PROXIES=127.0.0.1,::1,<public-ip>
```

For this box, the public IP is `75.174.0.18` — added to both lists in `/etc/systemd/system/prismatic-gateway.service`.

The `PRISMATIC_TRUSTED_PROXIES` setting enables `X-Forwarded-For` honoring when the immediate client is a trusted proxy (cloudflared in this case). Without this, requests behind a reverse proxy would all appear to come from the proxy's IP.

## Why a path alias?

The Linear webhook URL convention is `/webhooks/linear`. The Prismatic Engine gateway exposes the handler at `/api/gateway/linear`. To avoid breaking existing Linear-side webhook registrations, the gateway has path aliases:

- `POST /api/gateway/linear` → `linear_webhook` handler
- `POST /webhooks/linear` → `linear_webhook` handler (alias)
- Same for `/api/gateway/github` and `/webhooks/github`

Both routes accept the same HMAC-signed payloads and dispatch identically.

## Verification steps

After updating the tunnel config:

```bash
# 1. Wait ~10 seconds for cloudflared to pick up the new config
sleep 10

# 2. Test from outside (this verifies the tunnel end-to-end)
curl -X POST https://webhooks.growthwebdev.com/webhooks/linear \
  -H 'Content-Type: application/json' \
  -d '{}' \
  -w '\nHTTP=%{http_code}\n'
# Expected: HTTP/2 401 (HMAC required)
```

With valid HMAC:

```python
import json, hmac, hashlib, time, urllib.request

secret = b'<LINEAR_WEBHOOK_SIGNING_SECRET>'
now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
body = json.dumps({
    "type": "Issue", "action": "update", "createdAt": now_iso,
    "data": {"identifier": "GRO-2081", "labels": [{"id":"l1","name":"agent:fred"}]},
}, separators=(',', ':')).encode()
sig = hmac.new(secret, body, hashlib.sha256).hexdigest()

req = urllib.request.Request(
    'https://webhooks.growthwebdev.com/webhooks/linear',
    data=body,
    headers={'Linear-Signature': sig, 'Content-Type': 'application/json',
             'User-Agent': 'Linear-Webhooks/1.0 (https://api.linear.app)'},
    method='POST',
)
with urllib.request.urlopen(req, timeout=30) as r:
    print(r.read().decode())
# Expected: {"status":"dispatched","identifier":"GRO-2081","result":true}
```

The `User-Agent: Linear-Webhooks/1.0` header is needed to bypass Cloudflare's bot detection (default `Python-urllib` User-Agent triggers 1010 error).

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `HTTP 403 ip not allowed` | cloudflared's exit IP not in `PRISMATIC_ALLOWED_IPS` | Add the public IP to allowlist, restart gateway |
| `HTTP 401 missing Linear-Signature` | Expected — Linear doesn't include it on test calls | Sign your test request |
| `Cloudflare error 1010` | Bot protection triggered by default Python User-Agent | Set `User-Agent: Linear-Webhooks/1.0` |
| `HTTP 502` or `HTTP 504` | cloudflared can't reach `127.0.0.1:9000` | Check that prismatic-gateway.service is running |
| `HTTP 200 {"status":"queued"}` (not dispatched) | Event matched but no agent:* label or agent not in config | Add `agent:fred` (or similar) label to the issue |

## References

- Cloudflare Tunnel docs: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- Prismatic Engine webhook handler: `prismatic/gateway/server.py::linear_webhook`
- Webhook security standard: `okf/standards/webhook-security.md`
- Linear webhook events: `okf/integrations/linear-webhook-events.md`
