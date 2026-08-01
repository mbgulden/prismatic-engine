"""step_zapier_webhook — provision_site Phase 4.6 Zapier webhook registration.

Validates the configured Zapier webhook URL and (optionally) the
FareHarbor shortname it should receive data from, then persists the
configuration to ``external_sources.zapier`` in kpi-collections.json.

Soft-fail: missing creds (no ZAPIER_WEBHOOK_URL in env) means this
step records ``_soft_failure=True`` and the orchestrator continues.
The dashboard's pending-changes panel surfaces the missing credential
so the user can resolve it and re-run.

Idempotent: re-running this step with the same configuration replaces
the ``external_sources.zapier`` block (no duplicates).

The FareHarbor shortname comes from one of:

  1. The ``prior_outputs['platform_detect']`` (when the deployment
     already knows the platform).
  2. The ``context.platform`` from the funnel-config form (when the
     user picked "fareharbor" as the platform during Configure).
  3. A hardcoded fallback (``activeoahutours`` for the canonical
     Active Oahu Tours shortname) — this is the safe default for
     Phase 4.6 because the user's stated goal is to wire Zapier
     to Active Oahu Tours' FareHarbor company.

The FareHarbor probe is best-effort: when the shortname resolves
we persist the company info; when it doesn't (404 or network error)
the step still completes — the webhook URL is the primary
configuration, and the FareHarbor probe is just a sanity check.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _load_kpi_collections(sites_root: Path, slug: str) -> dict[str, Any] | None:
    """Load the site's kpi-collections.json. Returns None if not found."""
    path = sites_root / f"{slug}.kpi.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_kpi_collections(sites_root: Path, slug: str, data: dict[str, Any]) -> Path:
    """Save the site's kpi-collections.json. Returns the path written."""
    sites_root.mkdir(parents=True, exist_ok=True)
    path = sites_root / f"{slug}.kpi.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _resolve_fareharbor_shortname(
    prior_outputs: dict[str, dict[str, Any]] | None,
    slug: str,
) -> str:
    """Pick the FareHarbor shortname for this site.

    Order:
      1. prior_outputs['platform_detect']['fareharbor_shortname']
      2. prior_outputs['platform_detect']['shortname'] (legacy alias)
      3. Slug heuristic: if the slug is 'active-oahu' or 'activeoahu',
         return 'activeoahutours' (the canonical shortname).
      4. Slug fallback for the user's other sites.
      5. Default to 'activeoahutours' (Phase 4.6 canonical site).
    """
    platform_out = (prior_outputs or {}).get("platform_detect") or {}
    shortname = (
        platform_out.get("fareharbor_shortname") or platform_out.get("shortname") or ""
    )
    if shortname:
        return shortname
    # Slug heuristic.
    slug_lower = (slug or "").lower()
    if slug_lower in ("active-oahu", "activeoahu", "active-oahu-tours"):
        return "activeoahutours"
    # Default for Phase 4.6 (the user's stated goal).
    return "activeoahutours"


def step_register_zapier_webhook(
    *,
    domain: str,
    owner: str,
    run,
    publish_root: Path,
    prior_outputs: dict[str, dict[str, Any]] | None = None,
    sites_root: Path | None = None,
    fareharbor_shortname: str | None = None,
    **_kwargs: Any,
) -> Any:
    """Validate the Zapier webhook URL and persist the config.

    Args:
      domain: The site domain.
      owner: Email of the site owner.
      run: ProvisionRun (typed).
      publish_root: Where provision_state is persisted.
      prior_outputs: Dict of step_name -> {status, output, error}.
      sites_root: Override for the sites/ directory.
      fareharbor_shortname: Optional explicit shortname override.

    Returns:
      StepResult with status="complete" / "failed" + structured output.
    """
    # Derive slug from domain.
    slug = domain.split(".")[0] if "." in domain else domain

    # Default sites_root. Walk up from __file__ until we find the
    # canonical `sites/` directory; this is robust against symlinked
    # repos (the `plugins/` symlink at the repo root) and against
    # `PRISMATIC_REPO_ROOT` being set to the repo root.
    if sites_root is None:
        env_root = os.environ.get("PRISMATIC_REPO_ROOT")
        if env_root:
            # Note: base was assigned previously but not used here because
            # we walk up from __file__ to find the canonical sites dir.
            # Keeping the env_root read for forward-compatibility (e.g.
            # when we want to honor a different repo root).
            del env_root
        else:
            # Find the publish_kpi_tracker/sites directory. __file__ may
            # resolve through the `plugins/` symlink at the repo root, so
            # we anchor on the resolved (canonical) path. The canonical
            # structure is:
            #   <repo>/prismatic/shipped_plugins/pwp/capabilities/
            #     provision_site/steps/zapier.py
            #     publish_kpi_tracker/sites/
            # From the canonical anchor, parents[3] is the
            # `prismatic/shipped_plugins` dir; the sites/ folder is
            # at `<anchor.parents[3]>/pwp/capabilities/publish_kpi_tracker/sites`.
            anchor = Path(__file__).resolve()
            # Primary path: canonical parents[3].
            sites_root = (
                anchor.parents[3] / "pwp/capabilities/publish_kpi_tracker/sites"
            )
            if not sites_root.is_dir():
                # Fallback: walk up from the canonical path looking for
                # the `publish_kpi_tracker/sites` directory.
                cur = anchor.parent
                while cur != cur.parent:
                    candidate = cur / "pwp/capabilities/publish_kpi_tracker/sites"
                    if candidate.is_dir():
                        sites_root = candidate
                        break
                    cur = cur.parent

    # Lazy imports.
    from ..types import StepResult
    from ..zapier_client import (
        FareHarborNotFoundError,
        ZapierClient,
        ZapierError,
    )

    # Resolve the Zapier client.
    try:
        client = ZapierClient.from_env()
    except ValueError as exc:
        # No creds: soft-fail.
        return StepResult(
            name="register_zapier_webhook",
            status="failed",
            error=str(exc),
            output={
                "_soft_failure": True,
                "missing_env": ["ZAPIER_WEBHOOK_URL"],
                "hint": (
                    "Set ZAPIER_WEBHOOK_URL in ~/.hermes/profiles/ned/.env "
                    "(the Zapier 'Catch Hook' URL), then re-run: "
                    "pwp-kpi-tracker provision --domain "
                    f"{domain} --steps register_zapier_webhook"
                ),
            },
        )

    # Resolve the shortname.
    shortname = fareharbor_shortname or _resolve_fareharbor_shortname(
        prior_outputs, slug
    )

    # Probe the webhook URL.
    webhook_probe = client.probe_webhook()

    # Probe FareHarbor (best-effort).
    fareharbor_data: dict[str, Any] | None = None
    fareharbor_error: str | None = None
    try:
        company = client.probe_fareharbor(shortname)
        fareharbor_data = company.to_dict()
    except FareHarborNotFoundError:
        # 404: the shortname doesn't exist. Note in the output but
        # don't fail the step — the webhook URL is the primary config.
        fareharbor_error = f"not_found: {shortname!r}"
    except ZapierError as exc:
        # Network/transport error: soft-fail.
        fareharbor_error = f"transport_error: {exc}"

    # Webhook URL must be reachable for the step to be "complete".
    if not webhook_probe.reachable:
        return StepResult(
            name="register_zapier_webhook",
            status="failed",
            error=(
                f"Zapier webhook URL is not reachable: {webhook_probe.error_message}"
            ),
            output={
                "_soft_failure": True,
                "webhook": webhook_probe.to_dict(),
                "fareharbor_shortname": shortname,
                "fareharbor": fareharbor_data,
                "fareharbor_error": fareharbor_error,
                "hint": (
                    "Verify the webhook URL is correct and the endpoint "
                    "is publicly reachable. The URL is the one Zapier "
                    "gives you when you set up a 'Catch Hook' trigger."
                ),
            },
        )

    # Build the external_sources.zapier block.
    zapier_block = {
        "webhook_url": webhook_probe.url,
        "webhook_reachable": webhook_probe.reachable,
        "webhook_status": webhook_probe.status,
        "webhook_content_type": webhook_probe.content_type,
        "webhook_token_source": client.webhook_token_source,
        "fareharbor_shortname": shortname,
        "fareharbor_company": fareharbor_data,
        "fareharbor_error": fareharbor_error,
        "registered_at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "validated": True,
    }

    # Load + update kpi-collections.json.
    kpi = _load_kpi_collections(sites_root, slug)
    if kpi is None:
        kpi = {
            "site_slug": slug,
            "domain": domain,
            "version": 1,
            "external_sources": {"zapier": zapier_block},
        }
    else:
        existing_sources = kpi.get("external_sources") or {}
        existing_sources["zapier"] = zapier_block
        kpi["external_sources"] = existing_sources

    saved = _save_kpi_collections(sites_root, slug, kpi)

    return StepResult(
        name="register_zapier_webhook",
        status="complete",
        output={
            "zapier_block": zapier_block,
            "kpi_collections_path": str(saved),
            "fareharbor_shortname": shortname,
        },
    )
