"""provision_site — PWP site provisioning capability.

This capability orchestrates the setup of a new (or existing) website
so its KPIs are trackable end-to-end. Phase 1 (this commit) covers
the Cloudflare-first MVP:

  1. Domain ownership verification (DNS TXT challenge)
  2. Cloudflare zone creation or lookup
  3. Cloudflare DNS record management (TXT for verification,
     CNAME for Pages, etc.)
  4. Google Search Console site verification via DNS TXT
     (avoids needing write access to the site's static dir)
  5. Auto-register the new site in `config/seo_sites.json`
  6. Trigger `migrate --merge` to bootstrap the per-site
     `<slug>.kpi.json` from the registry

Later phases (toward Option 3):
  - Phase 2: Shared-service-account GA4 + GTM provisioning
  - Phase 3: Stripe Connect / product creation for commerce sites
  - Phase 4: emdash site templates + Cloudflare Pages deploy +
    GitHub backup
  - Phase 5: LLM-driven diagnosis + planning agents

Entry points:
  - CLI: pwp-kpi-tracker provision --domain example.com --owner email
  - API: TBD (FastAPI router added in PWP plugin later)

Status tracking:
  Each provisioning run writes `<publish_root>/provisioning/<domain>.json`
  with the current step, status, and any error details. The UI
  polls this file (or hits the API).
"""

from __future__ import annotations

from .cloudflare_client import (
    CF_API_BASE,
    CloudflareClient,
    CloudflareError,
    DNSRecord,
    Zone,
)

__all__ = [
    "CF_API_BASE",
    "CloudflareClient",
    "CloudflareError",
    "DNSRecord",
    "Zone",
]
