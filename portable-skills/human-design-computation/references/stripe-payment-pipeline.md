# Stripe Payment + Report Generation Pipeline

Complete end-to-end flow for selling Human Design reports. Static HTML frontend on Cloudflare Pages, Python backend on homelab via Cloudflare Tunnel.

## Architecture

```
Cloudflare Pages (static)           Homelab (dynamic, via Tunnel)
┌────────────────────────┐         ┌──────────────────────────────┐
│ buy-report.html        │────────▶│ server.py :8000              │
│ - birth data form      │  POST   │ /create-checkout → Stripe    │
│ - 4 report tiers       │         │ /webhook ← Stripe events     │
│ - Stripe redirect      │         │                              │
└────────────────────────┘         │ Webhook flow:                │
                                   │  1. Verify signature         │
                                   │  2. Call MCP engine :8765    │
                                   │  3. Format markdown report   │
                                   │  4. Pandoc → PDF             │
                                   │  5. SMTP → email customer    │
                                   └──────────────────────────────┘
```

## Report Tiers

| Report | Price | Description |
|--------|-------|-------------|
| Foundation (natal) | $19 | Type, Strategy, Authority, Profile, Centers, Channels, Gates |
| Relationship (synastry) | $29 | Electromagnetic channels, compromise, dominance dynamics |
| 12-Month Transit | $29 | Monthly transit highlights, conditioning fields |
| Complete Bundle | $59 | All three, save $18 |

## Environment Variables

```
STRIPE_SECRET_KEY=sk_live_...       # From Stripe dashboard
STRIPE_WEBHOOK_SECRET=whsec_...     # From Stripe dashboard
SMTP_HOST=smtp.gmail.com            # Or SendGrid/Resend
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
FROM_EMAIL=reports@humandesignengine.com
MCP_SERVER=http://localhost:8765
PORT=8000
```

## Deployment

```bash
# Copy env template and fill in keys
cp payment/.env.template payment/.env

# Install and start systemd service
sudo cp payment/hde-payment.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hde-payment

# Verify
curl http://localhost:8000/api/ping
```

## Stripe Setup

1. Create products in Stripe dashboard (or use inline price_data as in server.py)
2. Set webhook endpoint: `https://api.humandesignengine.com/webhook`
3. Webhook events needed: `checkout.session.completed`
4. Get signing secret from webhook settings

## Key Design Decisions

- **No database**: Customer data is passed through Stripe metadata (birth date, time, location). No PII stored on our server.
- **Pandoc for PDF**: `pandoc input.md -o output.pdf --pdf-engine=wkhtmltopdf`. Already installed on server.
- **Success page**: Static HTML at `/success.html` on Cloudflare Pages. Stripe redirects there after payment.
- **Report format**: Markdown with YAML frontmatter, converted to PDF. Clean, readable, no heavy design framework needed.

## Pitfalls

- **No Stripe CLI**: Use raw HTTP requests (`urllib.request`) — simpler, no dependency.
- **Webhook verification**: The `stripe` Python package is optional. Server.py works without it (try/except around verify).
- **SMTP**: Gmail requires App Password (not regular password). SendGrid/Resend are easier but cost money.
- **Metric vs Imperial**: `urllib.parse.urlencode` — the `urllib.parse` import works at module level, not `from urllib import parse`.
