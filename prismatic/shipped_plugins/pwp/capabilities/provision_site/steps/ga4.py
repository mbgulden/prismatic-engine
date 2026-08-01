"""step_ga4_property — create a GA4 property + web data stream.

Phase 2 implements the real create path via the GA4 Admin API using
a shared service account. Falls back to a clear "missing credentials"
failure if GOOGLE_SA_JSON is not set, so the rest of the pipeline
stays runnable.

Output (on success):
  {
    "property_name": "properties/123456789",
    "property_id": "123456789",
    "measurement_id": "G-ABC123DEF4",
    "data_stream_name": "properties/123456789/dataStreams/987",
    "service_account": "<sa-email>",
  }

Used by the orchestrator to populate the new site's KPI config
(ga4_measurement_id, ga4_property_env, etc.).
"""

from __future__ import annotations

from typing import Any, Dict

from ..google_client import GoogleAuthError, GoogleClient, GoogleError
from ..types import StepResult


def step_ga4_property(
    *,
    domain: str,
    owner: str,
    run,
    publish_root,
    prior_outputs=None,
    **_: Any,
) -> StepResult:
    """Create a GA4 property for the site via shared service account."""
    prior = prior_outputs or {}
    # If we already succeeded on a prior attempt, return immediately.
    if prior.get("ga4_property", {}).get("measurement_id"):
        return StepResult(
            name="ga4_property",
            status="complete",
            output={
                **prior["ga4_property"],
                "reused_prior_output": True,
            },
        )
    site_name = _derive_site_name(domain)
    try:
        gc = GoogleClient.from_env()
        ga4_account_id = gc.ga4_account_id
        prop = gc.ga4_property_create(domain=domain, site_name=site_name)
    except GoogleAuthError as e:
        return StepResult(
            name="ga4_property",
            status="failed",
            error=(
                f"GA4 service-account not configured: {e}. Set "
                "GOOGLE_SA_JSON (path to a service-account JSON key) "
                "and GA4_ACCOUNT_ID env vars to enable GA4 provisioning."
            ),
        )
    except GoogleError as e:
        return StepResult(
            name="ga4_property",
            status="failed",
            error=f"GA4 Admin API error: {e}",
        )
    except Exception as e:
        return StepResult(
            name="ga4_property",
            status="failed",
            error=f"Unexpected error creating GA4 property: {e!r}",
        )
    return StepResult(
        name="ga4_property",
        status="complete",
        output={
            "property_name": prop.name,
            "property_id": prop.property_id,
            "measurement_id": prop.measurement_id,
            "data_stream_name": prop.data_stream_name,
            "ga4_account_id": ga4_account_id,
            "service_account": gc.service_account_email,
            "site_name": site_name,
        },
    )


def _derive_site_name(domain: str) -> str:
    """Convert `example.com` -> `Example` for the GA4 display name."""
    base = domain.split(".", 1)[0]
    return base[:1].upper() + base[1:]
