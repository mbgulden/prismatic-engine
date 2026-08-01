"""step_stripe — provision_site Phase 4 Stripe API registration step.

Runs after `gtm_container`. Validates the Stripe API key + account_id
via StripeClient.validate(), then writes the account metadata into
the per-site kpi-collections.json `external_sources.stripe` block.

Soft-fail: missing creds (no STRIPE_RESTRICTED_KEY in env) means this
step records _soft_failure=True and the orchestrator continues. The
dashboard's pending-changes panel surfaces the missing credential so
the user can resolve it and re-run.

Idempotent: re-running this step with the same key replaces the
external_sources.stripe block (no duplicates).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _load_kpi_collections(sites_root: Path, slug: str) -> Optional[Dict[str, Any]]:
    """Load the site's kpi-collections.json. Returns None if not found."""
    path = sites_root / f"{slug}.kpi.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_kpi_collections(
    sites_root: Path, slug: str, data: Dict[str, Any]
) -> Path:
    """Save the site's kpi-collections.json. Returns the path written."""
    sites_root.mkdir(parents=True, exist_ok=True)
    path = sites_root / f"{slug}.kpi.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def step_register_stripe(
    *,
    domain: str,
    owner: str,
    run,
    publish_root: Path,
    prior_outputs: Optional[Dict[str, Dict[str, Any]]] = None,
    sites_root: Optional[Path] = None,
    **_kwargs: Any,
) -> "StepResult":
    """Validate Stripe credentials and persist them to kpi-collections.json.

    Args:
      domain: The site domain (e.g. "ezshare.systems").
      owner:  Email of the site owner.
      run:    ProvisionRun (typed).
      publish_root: Where provision_state is persisted (unused here).
      prior_outputs: Dict of step_name -> {status, output, error}.
      sites_root: Override for the sites/ directory.
    """
    # Derive slug from domain (matches the convention used elsewhere).
    slug = domain.split(".")[0] if "." in domain else domain

    # Default sites_root to the publish_kpi_tracker/sites/ directory.
    # Resolved via PRISMATIC_REPO_ROOT env var or walking up from __file__
    # so the step is portable across dev / staging / prod.
    if sites_root is None:
        env_root = os.environ.get("PRISMATIC_REPO_ROOT")
        if env_root:
            base = Path(env_root).expanduser()
        else:
            # __file__ = .../prismatic/shipped_plugins/pwp/capabilities/provision_site/steps/stripe.py
            base = Path(__file__).resolve().parents[4]
        sites_root = base / "prismatic/shipped_plugins/pwp/capabilities/publish_kpi_tracker/sites"

    # Lazy import to avoid hard dependency in unit tests.
    from ..stripe_client import StripeClient, StripeError
    from ..types import StepResult

    try:
        client = StripeClient.from_env()
    except ValueError as exc:
        # No creds: soft-fail
        return StepResult(
            name="register_stripe",
            status="failed",
            error=str(exc),
            output={
                "_soft_failure": True,
                "missing_env": ["STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY"],
                "hint": (
                    "Set STRIPE_RESTRICTED_KEY (preferred) or "
                    "STRIPE_API_KEY in ~/.hermes/profiles/ned/.env, "
                    "then re-run: pwp-kpi-tracker provision --domain "
                    f"{domain} --steps register_stripe"
                ),
            },
        )

    # Validate the key
    try:
        balance = client.validate()
    except StripeError as exc:
        return StepResult(
            name="register_stripe",
            status="failed",
            error=f"Stripe validation failed: {exc}",
            output={
                "_soft_failure": True,
                "stripe_status": exc.status,
                "stripe_error_code": exc.error_code,
            },
        )

    # Build the external_sources.stripe block
    stripe_block = {
        "account_id": client.account_id or "",
        "key_type": "restricted" if client.token_source == "STRIPE_RESTRICTED_KEY" else "standard",
        "registered_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "validated": True,
        "currency": (balance.get("available") or [{}])[0].get("currency") if isinstance(balance.get("available"), list) else None,
        "available_cents": (
            ((balance.get("available") or [{}])[0].get("amount") if isinstance(balance.get("available"), list) else 0)
            or 0
        ),
    }

    # Load + update kpi-collections.json
    kpi = _load_kpi_collections(sites_root, slug)
    if kpi is None:
        # The site hasn't been provisioned yet; create a minimal block
        kpi = {
            "site_slug": slug,
            "domain": domain,
            "version": 1,
            "external_sources": {"stripe": stripe_block},
        }
    else:
        existing_sources = kpi.get("external_sources") or {}
        existing_sources["stripe"] = stripe_block
        kpi["external_sources"] = existing_sources

    saved = _save_kpi_collections(sites_root, slug, kpi)

    return StepResult(
        name="register_stripe",
        status="complete",
        output={
            "stripe_block": stripe_block,
            "kpi_collections_path": str(saved),
            "key_type": stripe_block["key_type"],
        },
    )
