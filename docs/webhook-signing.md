# Webhook signing — operator guide

All gateway webhook ingress is **fail-closed**: unsigned, invalid-signature,
or stale deliveries are rejected with `401` and recorded in the hypervisor
audit ledger (`WEBHOOK_AUTH_FAILED`). Webhook routes are exempt from the
control-auth Bearer <redacted> because HMAC is their authentication — they are never
unauthenticated.

## Endpoints and schemes

| Endpoint | Scheme | Header | Secrets (env) |
|---|---|---|---|
| `POST /api/gateway/github` | GitHub native | `X-Hub-Signature-256: sha256=<hex>` | `PRISMATIC_GITHUB_WEBHOOK_SECRET` (+ `_SECONDARY`) |
| `POST /api/gateway/linear`, `POST /webhooks/linear` | Linear native | `linear-signature: <hex>` | `PRISMATIC_LINEAR_WEBHOOK_SECRET` (+ `_SECONDARY`) |
| `POST /api/pwp/webhooks/zapier` | Prismatic generic | `X-Prismatic-Signature` | `PRISMATIC_WEBHOOK_SECRET` (+ `_SECONDARY`) |
| `POST /api/pwp/webhooks/stripe` | Prismatic generic | `X-Prismatic-Signature` | `PRISMATIC_WEBHOOK_SECRET` (+ `_SECONDARY`) |

Vendor endpoints (GitHub, Linear) verify the vendor's own HMAC: HMAC-SHA256
over the **exact raw request body**, constant-time compared against every
configured secret slot.

## The Prismatic generic scheme

For first-party integrations (Zapier, PWP listeners, anything you build):

- Header: `X-Prismatic-Signature: t=<unix_timestamp>,v1=<hex>`
- Signed bytes: `"<timestamp>.<raw_body>"` — the decimal timestamp, a
  literal `.`, then the exact raw request bytes.
- MAC: HMAC-SHA256 with the shared secret, hex-encoded.
- Freshness: `|now - t|` must be within `PRISMATIC_WEBHOOK_MAX_AGE_SECONDS`
  (default `300`). This is the replay protection — captured deliveries
  cannot be replayed after the window closes.
- Rejection reasons (also in the ledger payload): `no-secret-configured`,
  `missing-signature`, `malformed-signature`, `stale-timestamp`,
  `bad-signature`.

### Configuring the secret

```bash
# Generate (do this once, store in your secret manager):
python3 -c "import secrets; print(secrets.token_hex(32))"

# Export for the gateway process (never commit the value):
export PRISMATIC_WEBHOOK_SECRET="<hex>"
# Optional rotation slot — both are accepted while you roll over:
export PRISMATIC_WEBHOOK_SECRET_SECONDARY="<previous-or-next-hex>"
# Optional freshness window (seconds, default 300):
export PRISMATIC_WEBHOOK_MAX_AGE_SECONDS=300
```

### Signing a delivery (Python)

```python
import hashlib, hmac, time, urllib.request

secret = "<PRISMATIC_WEBHOOK_SECRET>"
body = b'{"site_slug": "prismatic-core", "event": "lead_captured"}'
ts = int(time.time())
sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()

req = urllib.request.Request(
    "https://your-gateway/api/pwp/webhooks/zapier",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-Prismatic-Signature": f"t={ts},v1={sig}",
    },
)
print(urllib.request.urlopen(req).read())
```

### Signing a delivery (bash)

```bash
SECRET="<PRISMATIC_WEBHOOK_SECRET>"
BODY='{"site_slug": "prismatic-core", "event": "lead_captured"}'
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')
curl -s -X POST https://your-gateway/api/pwp/webhooks/zapier \
  -H "Content-Type: application/json" \
  -H "X-Prismatic-Signature: t=${TS},v1=${SIG}" \
  -d "$BODY"
```

### Rotation procedure

1. Generate the new secret; put it in `PRISMATIC_WEBHOOK_SECRET_SECONDARY`
   (keep the current one in `PRISMATIC_WEBHOOK_SECRET`).
2. Restart/reload the gateway; update senders to sign with the new secret.
3. Once all senders are on the new secret, promote it to
   `PRISMATIC_WEBHOOK_SECRET` and clear the secondary.

Both slots are accepted during the overlap, so rotation never drops a
legitimate delivery.

## Behavior change note

Before this hardening, webhook HMAC verification was opportunistic:
deliveries without a signature header were accepted. They are now rejected.
If an integration stops delivering after this change, it is missing a
signature — configure the secret on both sides per this guide.
