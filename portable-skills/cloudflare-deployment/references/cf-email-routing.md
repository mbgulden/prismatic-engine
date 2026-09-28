# Cloudflare Email Routing — API & Configuration

## What It Is
Free email forwarding for custom domains. Incoming mail to `you@yourdomain.com` → forwarded to your Gmail/Outlook/ProtonMail inbox. No inbox hosting — forward-only. No outgoing sending from the custom domain.

## API Coverage

### Enable Email Routing
```bash
ZONE_ID="..."
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/email/routing/enable" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"
```

Enabling auto-creates the required MX records: `route1.mx.cloudflare.net` (priority 67), `route2.mx.cloudflare.net` (priority 21), `route3.mx.cloudflare.net` (priority 15). No manual DNS setup needed.

### Check Status
```bash
curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/email/routing" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY"
```

Returns: `enabled` (boolean), `status` ("ready" or "unconfigured"), `errors` (if MX records missing).

### Create Routing Rule
```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/email/routing/rules" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" \
  -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -d '{
    "actions": [{"type": "forward", "value": ["destination@gmail.com"]}],
    "matchers": [{"field": "to", "type": "literal", "value": "you@yourdomain.com"}],
    "enabled": true,
    "name": "Primary forward"
  }'
```

### What REQUIRES Dashboard (No API)

| Operation | API? | Notes |
|---|---|---|
| Enable routing | ✅ | POST enable |
| Add MX records | ✅ | Auto-created on enable |
| Add destination address | ❌ | Dashboard only — Email → Email Routing → Destination Addresses |
| Verify destination | ❌ | Cloudflare sends verification email — user clicks link in Gmail |
| Create routing rules | ✅ | BUT requires pre-verified destination address |
| Disable routing | ✅ | POST disable |

**Critical:** The destination email must be verified before any routing rules can include it. This requires manual dashboard action — there is NO API endpoint for triggering verification or checking verification status. The `/destination_addresses` endpoints return 404.

### Error: "Destination address is not verified" (code 2054)
When creating a routing rule that forwards to an unverified email, the API returns error 2054. Solution: user must go to Dashboard → Email → Email Routing → Destination Addresses → Add destination → Check email → Click verification link. Then retry the API call.

## When Forwarding Is Good Enough
For early-stage consulting/startup:
- ✅ Professional domain address for incoming mail
- ✅ Free, zero maintenance
- 🟡 Replies come from personal email (Gmail address visible)

Upgrade when: client count exceeds ~10, or outbound domain-branded email matters for procurement/compliance.

## Upgrade Paths

| Option | Cost | Sends from custom domain? | Complexity |
|---|---|---|---|
| CF Email Routing | Free | ❌ (forward-only) | Zero |
| Google Workspace | $6/mo | ✅ | 15 min setup |
| Zoho Mail | Free (1 user) | ✅ | 10 min setup |
| CF Routing + Resend/SendGrid | Free | ✅ (transactional only) | Medium |
