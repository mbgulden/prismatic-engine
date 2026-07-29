"""steps — individual provisioning steps.

Each step function:

  - Takes (domain, owner, run, publish_root).
  - Returns a StepResult with status="complete" / "failed" / "skipped".
  - Writes outputs to `run.steps[i].output` for downstream steps to read.
  - Does NOT raise — failures are returned as status="failed" + error string.

Step ordering is defined in `orchestrator.STEP_NAMES`. To add a new step:

  1. Implement `step_<name>` here.
  2. Add the name to `orchestrator.STEP_NAMES`.
  3. The orchestrator will pick it up automatically.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from ..domain_verifier import (
    VERIFY_PREFIX,
    generate_challenge_token,
    verify,
)
from ..types import StepResult
from .register_in_registry import add_site_to_registry, slug_from_domain
from .migrate import trigger_migrate


def step_verify_domain(
    *, domain: str, owner: str, run, publish_root: Path,
    prior_outputs: Optional[Dict[str, Dict[str, Any]]] = None,
    **_: Any
) -> StepResult:
    """Verify the domain via DNS TXT challenge.

    Phase 1 uses a "challenge issued" model: this step generates a
    token and writes it to `run.steps[0].output["challenge_token"]`,
    then immediately checks if it's already present (it won't be on
    the first run). The owner is expected to create the TXT record
    and re-run provisioning, which will reuse the SAME token (via
    `prior_outputs`) so the record value matches. If the record was
    found, the step is marked complete; subsequent steps then run.
    """
    out: Dict[str, Any] = {}
    prior = prior_outputs or {}
    prior_token = prior.get("verify_domain", {}).get("challenge_token")
    challenge_token = prior_token or generate_challenge_token()
    out["challenge_token"] = challenge_token
    out["record_name"] = f"{VERIFY_PREFIX}.{domain}"
    out["expected_value"] = challenge_token
    out["reused_prior_token"] = prior_token is not None

    # Check if the TXT record is present.
    result = verify(domain, challenge_token)
    out["observed_values"] = result.observed_values

    if not result.verified:
        return StepResult(
            name="verify_domain",
            status="failed",
            output=out,
            error=(
                f"domain verification pending. Create a DNS TXT record "
                f"at {result.record_name} with value "
                f"{challenge_token}, then re-run provisioning. "
                f"Observed TXT values: {result.observed_values}"
            ),
        )
    return StepResult(name="verify_domain", status="complete", output=out)


def step_cloudflare_zone(
    *, domain: str, owner: str, run, publish_root: Path, **_: Any
) -> StepResult:
    """Create or look up the Cloudflare zone for `domain`."""
    # Lazy import — Cloudflare client requires `requests` and is
    # optional for tests that mock it.
    from ..cloudflare_client import CloudflareClient, CloudflareError

    try:
        cf = CloudflareClient.from_env()
    except ValueError as exc:
        return StepResult(
            name="cloudflare_zone",
            status="failed",
            error=str(exc),
        )

    try:
        existing = cf.zone_lookup(domain)
        if existing is not None:
            return StepResult(
                name="cloudflare_zone",
                status="complete",
                output={
                    "zone_id": existing.id,
                    "nameservers": existing.nameservers,
                    "status": existing.status,
                    "action": "lookup",
                },
            )
        zone = cf.zone_create(domain)
        return StepResult(
            name="cloudflare_zone",
            status="complete",
            output={
                "zone_id": zone.id,
                "nameservers": zone.nameservers,
                "status": zone.status,
                "action": "create",
            },
        )
    except CloudflareError as exc:
        return StepResult(
            name="cloudflare_zone",
            status="failed",
            error=str(exc),
        )


def step_gsc_verify(
    *, domain: str, owner: str, run, publish_root: Path, **_: Any
) -> StepResult:
    """Verify the domain with Google Search Console via DNS TXT.

    Google Search Console supports `sc-domain:` verification via a
    DNS TXT record at the apex. The record name is `google-site-
    verification=<token>` (no prefix — the value itself contains
    the prefix). The token comes from the GSC API; Phase 1 will use
    a placeholder token since we don't have the GSC service account
    wired yet.

    For Phase 1, this step is a SKIPPED placeholder — the actual
    GSC verification will be wired in Phase 2 when the shared
    service account is provisioned.
    """
    return StepResult(
        name="gsc_verify",
        status="skipped",
        output={
            "reason": "GSC service account not yet provisioned (Phase 2)",
            "instruction": (
                "Once a service account is available, this step will "
                "call Search Console API sites.add with type=sc-domain "
                "and verify via DNS TXT at the apex."
            ),
        },
    )


def step_register_in_registry(
    *, domain: str, owner: str, run, publish_root: Path, **_: Any
) -> StepResult:
    """Add the domain to `config/seo_sites.json` as a v1 registry entry.

    Phase 1 uses a minimal entry shape: just slug, domain, and
    pwp_kpi_override={enabled:true}. The migration operator will
    populate the rest when `migrate` runs.

    Why minimal? Because `config/seo_sites.json` is read-only for
    the PWP plugin (different lane owns it). We add a NEW entry
    via a separate `<publish_root>/provisioning/sites.json`
    appendix file that the registry loader merges at read-time.

    For Phase 1 we write the appendix; Phase 2 will wire the
    registry loader to read it.
    """
    try:
        slug = slug_from_domain(domain)
        path = add_site_to_registry(domain, owner, slug, publish_root)
        return StepResult(
            name="register_in_registry",
            status="complete",
            output={
                "slug": slug,
                "appendix_path": str(path),
                "note": (
                    "Phase 1 writes a sites.json appendix; Phase 2 will "
                    "wire the registry loader to merge it into the "
                    "canonical config/seo_sites.json."
                ),
            },
        )
    except Exception as exc:
        return StepResult(
            name="register_in_registry",
            status="failed",
            error=repr(exc),
        )


def step_migrate_kpi(
    *, domain: str, owner: str, run, publish_root: Path, **_: Any
) -> StepResult:
    """Trigger `migrate --merge` to bootstrap the per-site `.kpi.json`."""
    # Get the slug from the prior step's output.
    slug = None
    for s in run.steps:
        if s.name == "register_in_registry" and s.output.get("slug"):
            slug = s.output["slug"]
            break
    if not slug:
        # Fall back: derive it ourselves.
        from .register_in_registry import slug_from_domain
        slug = slug_from_domain(domain)

    try:
        # Pass the publish_root so trigger_migrate reads the right
        # sites.json appendix (the one this orchestrator wrote to).
        result = trigger_migrate(slug, publish_root=publish_root)
        return StepResult(
            name="migrate_kpi",
            status="complete",
            output={
                "slug": slug,
                "publish_root": str(publish_root),
                "manifest": result,
            },
        )
    except Exception as exc:
        return StepResult(
            name="migrate_kpi",
            status="failed",
            error=repr(exc),
        )
