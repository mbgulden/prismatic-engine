#!/usr/bin/env bash
# scripts/verify_gro2313_webhook.sh
# GRO-2313: Verify whether GRO-2281 is already fixed (webhook chain recovery).
#
# This script tests whether the active Linear webhook URL
#   https://prismatic.growthwebdev.com/api/gateway/linear
# is reachable end-to-end or still hits Cloudflare Access login page.
#
# Acceptance: returns exit 0 if HTTP 200 / 401 / 403 with application/json
# (gateway reached). Returns exit 1 if HTTP 200/302 + text/html with
# "Cloudflare Access" — i.e. CF Access blocked it.
#
# Usage:
#   bash scripts/verify_gro2313_webhook.sh
#
# Verified broken on 2026-06-25 by Ned — endpoint still returns CF Access
# login HTML. See okf/integrations/gro-2313-verification-report-2026-06-25.md.

set -u
URL="${URL:-https://prismatic.growthwebdev.com/api/gateway/linear}"
ALT_URL="${ALT_URL:-https://webhooks.growthwebdev.com/webhooks/linear}"
TS=$(date +%s)
BODY='{"action":"create","type":"Issue","data":{"identifier":"GRO-2313-VERIFY"},"createdAt":"'"$(date -u +%Y-%m-%dT%H:%M:%S)"'Z"}'

# Pull secret from systemd unit if not set
if [ -z "${SECRET:-}" ]; then
  SECRET=$(grep -oP 'PRISMATIC_LINEAR_WEBHOOK_SECRET=\K.*' /etc/systemd/system/prismatic-gateway.service 2>/dev/null || echo "")
fi
SIG=$(printf "%s" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex 2>/dev/null | awk '{print $NF}')

echo "=== Test primary endpoint: $URL ==="
PRIMARY=$(curl -s -o /tmp/_gro2313_primary.html -w "%{http_code}|%{content_type}" \
  -X POST "$URL" \
  -H "Content-Type: application/json" \
  -H "Linear-Signature: t=$TS,v1=$SIG" \
  -d "$BODY" --max-time 10)
echo "  result: HTTP $PRIMARY"
echo "  body (first 200 chars):"
head -c 200 /tmp/_gro2313_primary.html | sed 's/^/    /'

# CF Access blocks: any 2xx/3xx/4xx with text/html containing "Cloudflare Access"
if echo "$PRIMARY" | grep -qE "(text/html|Cloudflare Access)" \
   || grep -q "Cloudflare Access" /tmp/_gro2313_primary.html 2>/dev/null; then
  echo ""
  echo "❌ PRIMARY ENDPOINT BLOCKED BY CF ACCESS — GRO-2281 is NOT fixed"
  PRIMARY_BLOCKED=1
else
  echo ""
  echo "✅ PRIMARY ENDPOINT REACHES GATEWAY"
  PRIMARY_BLOCKED=0
fi

echo ""
echo "=== Test alternate endpoint: $ALT_URL ==="
ALT=$(curl -s -o /tmp/_gro2313_alt.json -w "%{http_code}|%{content_type}" \
  -X POST "$ALT_URL" \
  -H "Content-Type: application/json" \
  -H "Linear-Signature: t=$TS,v1=$SIG" \
  -d "$BODY" --max-time 10)
echo "  result: HTTP $ALT"
echo "  body: $(cat /tmp/_gro2313_alt.json)"

if [ "$PRIMARY_BLOCKED" -eq 1 ]; then
  echo ""
  echo "DIAGNOSIS:"
  echo "  - Active Linear webhook (f6b67574-d70e-40f9-a20c-0e979932caf2) points to"
  echo "    $URL — but this is intercepted by Cloudflare Access."
  echo "  - The recovery fix on 2026-06-23 fixed bugs in the GATEWAY APPLICATION"
  echo "    (IP allowlist + signature parser) — it did NOT add a CF Access bypass"
  echo "    policy for prismatic.growthwebdev.com/api/gateway/*."
  echo "  - Working path remains webhooks.growthwebdev.com/webhooks/linear"
  echo "    (no CF Access in front; tunnel routes to same gateway on :9000)."
  echo ""
  echo "RECOMMENDED FOLLOW-UP:"
  echo "  GRO-2281 remains OPEN. Either:"
  echo "  (A) Add prismatic.growthwebdev.com/api/gateway/* to CF Access bypass policy"
  echo "  (B) Re-point the Linear webhook to webhooks.growthwebdev.com/webhooks/linear"
  echo "  (C) Remove the CF Access app on prismatic.growthwebdev.com entirely if not needed"
  exit 1
fi

exit 0
