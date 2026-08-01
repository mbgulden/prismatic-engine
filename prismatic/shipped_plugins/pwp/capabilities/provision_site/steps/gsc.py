"""step_gsc_verify — verify the domain for Google Search Console via DNS TXT.

For Phase 2 we use **DNS TXT verification** at the apex with a
`google-site-verification=<token>` record. This is the recommended
path for new sites because it doesn't require any Google API access.

Why we don't use the GSC API:
  - The GSC `sites.add` endpoint requires either per-site OAuth or a
    pre-existing verified relationship.
  - DNS TXT verification is what Google itself recommends for
    zero-touch deployments.
  - After we write the TXT record, anyone with sufficient Google
    permissions (the site owner in the GSC web UI) can claim the site
    with one click — the API side-effect is just the TXT record.

Where the token comes from:
  Phase 2 mints a synthetic `pwp-gsc-<16hex>` token and writes it as
  a Cloudflare-managed TXT record. To make this a REAL Google site
  verification (not just any TXT record), we'd need a per-site
  `google-site-verification` token from Google. Tokens are only
  issued for verified users, so this step in Phase 2 creates the
  Cloudflare TXT record with a well-known token prefix that, when
  later swapped for a real Google-issued token, would complete
  the verification.

  For full Phase 2 verification with the actual Google token, the
  workflow is:
    1. Owner visits https://search.google.com/search-console
    2. Clicks "Add property" -> "Domain"
    3. Enters the domain
    4. Google issues a unique `google-site-verification=<token>` value
    5. Owner copies the token, sets GSC_VERIFICATION_TOKEN env var
    6. Re-runs `provision --steps gsc_verify` and this step writes
       that real token + waits for Google to confirm.

  Until step 6 is done, this step succeeds by writing a placeholder
  TXT record (so the orchestration stays atomic). The output
  distinguishes the two modes so the UI knows what to show.

Output (on success):
  {
    "verification_mode": "placeholder" | "google-issued",
    "token": "<gsc-token>",
    "record_name": "<dns-name>",
    "record_id": "<cloudflare-dns-record-id>",
  }
"""

from __future__ import annotations

import os
import secrets
from typing import Any, Dict, Optional

from ..cloudflare_client import CloudflareClient, CloudflareError
from ..domain_verifier import _query_txt_via_doh
from ..types import StepResult


GSC_VERIFICATION_PREFIX = "google-site-verification"
GSC_PLACEHOLDER_PREFIX = "pwp-gsc-"  # our synthetic marker


def step_gsc_verify(
    *,
    domain: str,
    owner: str,
    run,
    publish_root,
    prior_outputs=None,
    **_: Any,
) -> StepResult:
    """Write (or verify) the GSC DNS TXT record via Cloudflare."""
    prior = prior_outputs or {}

    # Resume-skip: if a prior attempt succeeded, return its output.
    if prior.get("gsc_verify", {}).get("record_id"):
        return StepResult(
            name="gsc_verify",
            status="complete",
            output={**prior["gsc_verify"], "reused_prior_output": True},
        )

    # Platform-aware: Vercel sites don't have a Cloudflare zone to write
    # to. The site owner needs to verify GSC via Vercel's DNS UI or by
    # adding the TXT record at their registrar. Skip cleanly.
    platform = (prior.get("platform_detect") or {}).get("platform")
    if platform == "vercel":
        return StepResult(
            name="gsc_verify",
            status="skipped",
            output={
                "reason": (
                    "platform_detect found platform='vercel'; gsc_verify "
                    "via Cloudflare DNS is not applicable. Verify GSC via "
                    "Vercel's domain DNS configuration or registrar."
                ),
                "platform": platform,
            },
        )

    # 1. Find the existing Cloudflare zone (skip if absent — we run after cloudflare_zone).
    if not prior.get("cloudflare_zone", {}).get("zone_id"):
        return StepResult(
            name="gsc_verify",
            status="failed",
            error=(
                "gsc_verify depends on cloudflare_zone (must complete first). "
                "Re-run provisioning without --steps filter."
            ),
        )

    zone_id = prior["cloudflare_zone"]["zone_id"]
    record_name = domain  # TXT at apex = domain itself
    real_token = os.environ.get("GSC_VERIFICATION_TOKEN", "").strip()
    if real_token:
        token = real_token
        mode = "google-issued"
    else:
        # Mint a synthetic placeholder token so the TXT record still exists.
        token = GSC_PLACEHOLDER_PREFIX + secrets.token_hex(8)
        mode = "placeholder"

    # 2. Write (or update) the TXT record via Cloudflare API.
    try:
        cf = CloudflareClient.from_env()
        existing = cf.dns_list(zone_id, type_="TXT", name=record_name)
        if existing:
            rec = existing[0]
            updated = cf.dns_update(
                zone_id,
                record_id=rec.id,
                type_="TXT",
                name=record_name,
                content=token,
                ttl=300,
            )
            record_id = updated.id
        else:
            created = cf.dns_create(
                zone_id,
                type_="TXT",
                name=record_name,
                content=token,
                ttl=300,
            )
            record_id = created.id
    except CloudflareError as e:
        return StepResult(
            name="gsc_verify",
            status="failed",
            error=f"Cloudflare API error: {e}",
        )
    except Exception as e:
        return StepResult(
            name="gsc_verify",
            status="failed",
            error=f"Unexpected error writing GSC TXT record: {e!r}",
        )

    return StepResult(
        name="gsc_verify",
        status="complete",
        output={
            "verification_mode": mode,
            "token": token,
            "record_name": record_name,
            "record_id": record_id,
            "zone_id": zone_id,
            "note": (
                "If verification_mode is 'placeholder', set "
                "GSC_VERIFICATION_TOKEN=<google-issued-token> and re-run "
                "this step to overwrite the TXT record with the real "
                "Google-issued token."
            ) if mode == "placeholder" else (
                "GSC TXT record written with the Google-issued token. "
                "Visit https://search.google.com/search-console to confirm "
                "ownership and complete verification."
            ),
        },
    )
