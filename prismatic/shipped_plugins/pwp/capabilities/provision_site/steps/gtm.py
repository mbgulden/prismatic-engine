"""step_gtm_container — create a GTM container + default workspace.

Same Phase 2 pattern as ga4.py: real GTM API call when credentials are
present, clean failure when not. The `gtm_container_public_id` is what
gets embedded in the site (`<script src="googletagmanager.com/gtm.js?id=GTM-XXXX">`).

Output (on success):
  {
    "public_id": "GTM-ABC123DE",
    "account_id": "<gtm-account-id>",
    "container_id": "<container-id>",
    "container_name": "<site name>",
  }
"""

from __future__ import annotations

from typing import Any

from ..google_client import GoogleAuthError, GoogleClient, GoogleError
from ..types import StepResult
from .ga4 import _derive_site_name


def step_gtm_container(
    *,
    domain: str,
    owner: str,
    run,
    publish_root,
    prior_outputs=None,
    **_: Any,
) -> StepResult:
    """Create a GTM container for the site via shared service account."""
    prior = prior_outputs or {}
    if prior.get("gtm_container", {}).get("public_id"):
        return StepResult(
            name="gtm_container",
            status="complete",
            output={**prior["gtm_container"], "reused_prior_output": True},
        )
    site_name = _derive_site_name(domain)
    try:
        gc = GoogleClient.from_env()
        ctr = gc.gtm_container_create(site_name=site_name, domain=domain)
    except GoogleAuthError as e:
        return StepResult(
            name="gtm_container",
            status="failed",
            error=(
                f"GTM service-account not configured: {e}. Set "
                "GOOGLE_SA_JSON and GTM_ACCOUNT_ID env vars to enable "
                "GTM provisioning."
            ),
        )
    except GoogleError as e:
        return StepResult(
            name="gtm_container",
            status="failed",
            error=f"GTM API error: {e}",
        )
    except Exception as e:
        return StepResult(
            name="gtm_container",
            status="failed",
            error=f"Unexpected error creating GTM container: {e!r}",
        )
    return StepResult(
        name="gtm_container",
        status="complete",
        output={
            "public_id": ctr.public_id,
            "account_id": ctr.account_id,
            "container_id": ctr.container_id,
            "container_name": ctr.container_name,
        },
    )
