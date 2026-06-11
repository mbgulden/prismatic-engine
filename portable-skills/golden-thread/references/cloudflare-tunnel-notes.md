# Cloudflare Tunnel — Setup & Troubleshooting

## Token-Based Tunnel (What Michael Uses)

### Setup
1. Get token from [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com) → Access → Tunnels → your tunnel → Configure
2. Run: `cloudflared tunnel run --token <TOKEN>`
3. In Docker: `docker run -d --restart=always --network host cloudflare/cloudflared:latest tunnel --no-autoupdate run --token <TOKEN>`

### Routing
**Do NOT use `--url` flag for production.** Configure hostnames in the dashboard:
- Access → Tunnels → your tunnel → Public Hostnames
- Add entries: subdomain → service URL (e.g., `humandesignengine.com` → `localhost:8080`)

The `--url` flag is for quick testing only (e.g., `cloudflared tunnel --url http://localhost:8080` to get a temporary trycloudflare.com URL).

### Docker: Reaching localhost
Docker containers need `--network host` to reach services on the host's localhost. Without it, `localhost` refers to the container's own loopback.

### Port Conflicts (Critical Pitfall)
Before starting a service on a port, ALWAYS check what's already listening:
```bash
lsof -i :8080 2>/dev/null || ss -tlnp | grep 8080
```
- If a Node.js app (Hermes dashboard) is on 8080, use 8090 instead
- If nothing is there, proceed
- Update the Cloudflare dashboard to match the new port

### Service Health Check
After starting a service, verify:
```bash
curl -s -o /dev/null -w "HTTP %{http_code}" http://localhost:PORT/page.html
```
- 200 = good
- 401 = something else is intercepting (check with lsof)
- Connection refused = nothing listening

### Common Gotchas
- **401 on port**: Another process is intercepting. Check with `lsof -i :PORT`.
- **Connection refused**: Service crashed or never started. Check `ps aux | grep python` or `process(action='list')`.
- **DNS not resolving**: CNAME records in Cloudflare DNS must point to `<TUNNEL_ID>.cfargotunnel.com` — this is separate from the Public Hostnames config.
- **SSL errors**: Cloudflare handles SSL. The local service can be plain HTTP.

### Error 1033 — Tunnel Ingress Misconfiguration

**BEFORE debugging 1033, verify you're working with the RIGHT tunnel.** Cloudflare accounts often have multiple tunnels (e.g., one for `growthwebdev.com`, another for `humandesignengine.com`). Checking the wrong tunnel's logs while the dashboard change was made on a different tunnel wastes hours.

**Quick check — which tunnel is this machine running?**
```bash
journalctl -u cloudflared --no-pager -n 5 | grep "Starting tunnel"
# → "Starting tunnel tunnelID=4a6097ff-..." 
```
Compare this ID to the tunnel you configured in the dashboard. If they don't match, you need the token for the OTHER tunnel.

**Quick check — what tunnels exist on the account?**
```bash
cloudflared tunnel list
```
This shows all tunnels the authenticated account owns. Each has its own token, its own ingress rules, and its own DNS entries.

**If a second tunnel needs to run on the same machine, create a separate systemd service:**
```bash
sudo tee /etc/systemd/system/cloudflared-hde.service << 'EOF'
[Unit]
Description=Cloudflare Tunnel - humandesignengine.com
After=network-online.target
[Service]
TimeoutStartSec=30
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run --token <TOKEN>
Restart=on-failure
RestartSec=5s
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now cloudflared-hde
```

When a dashboard change "isn't syncing" despite tunnel restarts, the #1 cause is looking at the wrong tunnel's config. Always confirm the tunnel ID before spending cycles on DNS, certificates, or ingress debugging.

**Original diagnostic steps (use AFTER confirming tunnel ID):**
When a subdomain returns HTTP 530 with error code 1033 ("Argo Tunnel error"), the tunnel IS connected but has no ingress rule for that hostname. This is the most common silent failure — everything LOOKS configured but the tunnel doesn't know about the hostname.

**Diagnostic (quick):**
```bash
curl -s https://subdomain.humandesignengine.com/ping
# → "error code: 1033" — tunnel ingress missing
```

**Diagnostic (definitive):**
```bash
# Read the tunnel's actual ingress config from the logs
journalctl -u cloudflared --no-pager -n 30 --since "5 min ago" | grep "Updated to new configuration"
# This outputs the full ingress JSON — shows every hostname the tunnel knows about
```

Example output shows exactly which hostnames are routed:
```json
{
  "ingress": [
    {"hostname": "sentinel.growthwebdev.com", "service": "http://..."},
    {"hostname": "hermes.growthwebdev.com", "service": "http://localhost:9119"},
    {"service": "http_status:404"}
  ]
}
```
If `reports.humandesignengine.com` is NOT in the ingress array, the tunnel won't route it. This is a dashboard-side fix.

**Fix:** In Cloudflare Zero Trust → Networks → Tunnels → [tunnel] → Configure → Public Hostnames → Add the missing hostname with:
- Subdomain + Domain (e.g., `reports.humandesignengine.com`)
- Service Type: HTTP
- URL: `localhost:PORT`

**Two-part requirement for token-based tunnels:**
1. **DNS CNAME** → `<tunnel-id>.cfargotunnel.com` (proxied, orange cloud) — routes traffic TO the tunnel
2. **Public Hostname** in Zero Trust dashboard → service URL — tells tunnel WHERE to send it

Both must exist. Having only the DNS CNAME without the Public Hostname entry = 1033 error.

**Tunnel ID discovery** (when you need the CNAME target):
```bash
python3 -c "
import base64, json
token = open('/proc/$(pgrep -f cloudflared.*tunnel | head -1)/cmdline').read().split('\\x00')
token = [t for t in token if len(t) > 50][0].split()[-1]
data = json.loads(base64.b64decode(token + '=='))
print(f'Tunnel ID: {data[\"t\"]}')  # CNAME target: <id>.cfargotunnel.com
print(f'Account: {data[\"a\"]}')
"
```
