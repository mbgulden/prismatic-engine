# Cloudflare Tunnel Debugging Recipes

## Decode the Tunnel Token

When the tunnel uses `--token <jwt>`, the JWT contains the credentials needed for local config mode. Decode it:

```bash
python3 -c "
import json, base64
token = '<your-tunnel-token>'
# Add base64 padding if needed
padding = 4 - len(token) % 4
if padding != 4:
    token += '=' * padding
data = json.loads(base64.b64decode(token))
print(json.dumps({
    'AccountTag': data['a'],
    'TunnelSecret': data['s'],
    'TunnelID': data['t']
}, indent=2))
"
```

The token structure is: `{"a": "<account_id>", "t": "<tunnel_id>", "s": "<secret>"}`.

Write the output to `$PRISMATIC_HOME/.cloudflared/<tunnel_id>.json` if you ever need local config mode (for a NON-dashboard-managed tunnel — dashboard-managed tunnels ignore local config regardless).

## Session Diagnostic Pattern: Dashboard Change Not Syncing

**Symptoms:**
- User added a public hostname in Zero Trust dashboard
- DNS CNAME already points to tunnel
- `curl https://reports.example.com/api/endpoint` returns HTTP 530 / error 1033
- Local `curl localhost:8081/api/endpoint` returns 200
- Named tunnel `systemctl is-active cloudflared` = active

**Step 0: Are you looking at the RIGHT tunnel?**
This is the #1 cause of hours-long wild goose chases. Many setups have multiple named tunnels (e.g., one for growthwebdev.com, another for humandesignengine.com). If the user says they configured the route in the dashboard but the tunnel logs don't show it, you're probably on the wrong tunnel.

```bash
# List ALL cloudflared services
systemctl list-units --all | grep cloudflared

# For each one, extract its tunnel ID from the token
for unit in $(systemctl list-units --all --no-legend | grep cloudflared | awk '{print $1}'); do
  echo "=== $unit ==="
  systemctl cat "$unit" 2>/dev/null | grep -oP 'token \K[^ ]+' | head -1 | python3 -c "
import sys, json, base64
token = sys.stdin.read().strip()
padding = 4 - len(token) % 4
if padding != 4: token += '=' * padding
d = json.loads(base64.b64decode(token))
print(f\"  TunnelID: {d['t']}\")
" 2>/dev/null
done
```

**Then ask the user:** "Which tunnel ID did you configure in the dashboard?" If their tunnel ID doesn't appear in the output above, the connector isn't running. They need to provide the token so you can spin it up.

**Step 1: Check what the tunnel actually loaded**
journalctl -u cloudflared --no-pager -n 50 | grep "Updated to new configuration"

# Step 2: The config JSON contains the ingress array.
# Look for your hostname in the JSON. Example bad output:
# "ingress":[{"hostname":"sentinel.growthwebdev.com",...},
#            {"hostname":"hermes.growthwebdev.com",...},
#            {"hostname":"code.growthwebdev.com",...},
#            {"service":"http_status:404"}]
# If your hostname isn't there, the dashboard change didn't sync.

# Step 3: Note the version number. If it didn't increment
# from before the change, the dashboard didn't save it.
```

**Root cause:** Dashboard change didn't save or didn't propagate to the tunnel. The tunnel loads remote config from Cloudflare's API — the local config.yml is ignored.

**Fix:** Go back to Zero Trust dashboard → Networks → Tunnels → [tunnel] → Public Hostnames. Delete the existing entry (if present) and re-add it. Ensure you see a confirmation toast/notification that the change was saved. Wait 30 seconds, then restart the tunnel with `sudo systemctl restart cloudflared`. Verify the config version incremented.

**Workaround (if dashboard is broken):** Quick tunnels work but are fragile:
```bash
cloudflared tunnel --url http://localhost:8081
# Returns: https://<random>.trycloudflare.com
```
Quick tunnels can fail with Cloudflare-edge 404 (different from 1033 — `server: cloudflare` in response headers, no origin headers at all). If they do, they're not a reliable fallback. Fix the dashboard instead.

## Quick Tunnel 404 vs Named Tunnel 1033

| Error | Response Headers | Meaning |
|---|---|---|
| 1033 / 530 | `server: cloudflare`, `cf-ray: ...` | Hostname not in ingress config |
| Cloudflare-edge 404 | `server: cloudflare`, `cf-cache-status: DYNAMIC`, NO origin server headers | Quick tunnel connected but edge isn't routing to origin — possibly QUIC issues, rate limiting, or Cloudflare-side problem |
| Origin 404 | `server: BaseHTTP/...` or your app's server header | Tunnel works, your app doesn't have that route |
