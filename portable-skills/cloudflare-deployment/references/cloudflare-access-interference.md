# Cloudflare Access Interference — Diagnosis & Fix

## Symptom
Tunnel appears healthy (DNS resolves, ingress correct, service running) but browser hits `https://hostname` and gets HTTP 302 redirect to:
```
https://<team>.cloudflareaccess.com/cdn-cgi/access/login/<hostname>?kid=...
```

Response headers include:
```
www-authenticate: Cloudflare-Access resource_metadata="https://<hostname>/.well-known/cloudflare-access-protected-resource/"
```

## Diagnosis Sequence
Run these checks in order to isolate the issue:

1. **Is the local service healthy?** `curl -s http://localhost:<port>/` — should return expected content
2. **Is the tunnel connected?** `ps aux | grep "cloudflared.*tunnel"` — should show running process
3. **Does DNS resolve?** `dig +short <hostname>` — should return Cloudflare IPs
4. **Is the ingress rule correct?** Check `config.yml` (if config-file mode) or dashboard Public Hostnames (if token mode). Confirm `<hostname> → localhost:<port>` exists.
5. **Is Cloudflare Access intercepting?** `curl -sI https://<hostname>/` — if 302 with `cloudflareaccess.com`, the tunnel is fine but Access is blocking.

## Fix
**Zero Trust dashboard → Access → Applications → find the application for this hostname → delete or disable the policy.**

The tunnel, DNS, ingress, and service are all healthy — Access is the sole blocker. No tunnel config changes needed.

## Quick Tunnel Workaround
While the Access policy is being removed, a quick tunnel bypasses both DNS and Access:
```bash
cloudflared tunnel --url http://localhost:<port> 2>&1 | tee /tmp/cf-quick-tunnel.log
# Look for: https://<random>.trycloudflare.com
```
Test: `curl -s https://<random>.trycloudflare.com/`

## Pitfall: Token vs Config Mode
Token-based tunnels (`--token <JWT>`) pull ingress from the API/dashboard, not local `config.yml`. The Access policy is also dashboard-managed. You cannot fix this from the server — it requires dashboard access.

## Session Reference
2026-06-03: `hermes.growthwebdev.com` → Hermes gateway (localhost:9119). Tunnel 4a6097ff, DNS correct, ingress correct, gateway healthy. Cloudflare Access was intercepting with 302 → `cloudflareaccess.com`. Quick tunnel (`https://pdf-mass-hospital-multi.trycloudflare.com`) confirmed gateway was working.
