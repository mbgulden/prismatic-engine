# Live Infrastructure Discovery Checklist

**When to run:** Before concluding ANY project task needs code written. Run this on the project's host before generating "build X" tasks. Many services are deployed by prior sessions but never documented in Linear.

## 4-Point Discovery

### 1. Systemd Services
```bash
systemctl list-units --type=service --state=running | grep -iE 'hde|hd-|payment|report|api|mcp|stripe|checkout'
systemctl status <service-name> --no-pager
```

### 2. Port Listeners
```bash
ss -tlnp | grep -E '800[0-9]|808[0-9]|809[0-9]|300[0-9]|500[0-9]'
```
Cross-reference with registry's `port_*_status` fields. If registry says DOWN but `ss` shows a listener, the registry is stale.

### 3. Health Checks (on each active port)
```bash
for port in $(ss -tlnp | grep python3 | awk '{print $4}' | awk -F: '{print $NF}' | sort -u); do
  echo "=== Port $port ==="
  curl -s --max-time 3 http://localhost:$port/ping 2>/dev/null || \
  curl -s --max-time 3 http://localhost:$port/api/ping 2>/dev/null || \
  curl -s --max-time 3 http://localhost:$port/health 2>/dev/null || \
  echo "  (no /ping or /health endpoint)"
done
```

### 4. Tunnel Routing
```bash
ps aux | grep cloudflared | grep -v grep
cat ~/.cloudflared/config.yml 2>/dev/null
```
Then test each hostname: `curl -sI --max-time 5 https://<hostname>/ping`

## Common Discovery Patterns

| Symptom | What to check | Likely fix |
|---|---|---|
| Linear says "Build X" but X exists | `systemctl`, `ss -tlnp` | Move issue to Done, document in comments |
| Port shows listener but no external access | Tunnel config (`config.yml` + `ps aux`) | Add ingress rule in Cloudflare dashboard |
| Service running but returns 401 | `.env` file for placeholder keys | Update `.env` with real credentials |
| Registry says port DOWN but it's UP | Cross-check `ss -tlnp` vs registry | Update registry, fix monitoring metadata |

## Case Study: HD Engine Core (June 7, 2026)

GRO-291 said "Create Stripe Checkout." Discovery found:
- `hde-payment.service` running since June 3 on port 8002
- Full Stripe checkout + webhooks + affiliate tracking built
- `.env` had placeholder `STRIPE_SECRET_KEY=***`
- Tunnel routed `reports.humandesignengine.com` but not API/payment hostnames

**What was actually needed:** Tunnel ingress rules + real Stripe key — not building a payment server. The code was already deployed and verified (Stripe returned HTTP 401, proving the API call was correct).
