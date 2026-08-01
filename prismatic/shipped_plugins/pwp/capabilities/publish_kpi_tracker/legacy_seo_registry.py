"""legacy_seo_registry — adapt v1 config/seo_sites.json to the v2 PWP registry schema.

The real-world `config/seo_sites.json` is the v1 shape: a flat array of sites
with `gsc_property`, `sitemap_url`, `site_dir_candidates`, `expected_data_layer_events`,
and direct GA4/GTM fields. The PWP `kpi-registry.schema.json` (inside
plugins/pwp/capabilities/publish_kpi_tracker/schemas/) is the v2
shape: required top-level `pwp_kpi_capability`, `default_metric_specs`, and per-site
`pwp_kpi_override` / `pwp_kpi_metric_specs` blocks.

This module is the seam between the two. `adapt_v1_to_v2(registry_v1)` produces a
v2-shaped dict that the canonical `pwp_kpi_site_registry.load_registry()` and
`operator_migrate.run()` consume. The adapter:

  - infers `pwp_kpi_capability: {enabled: true, operator: "ned", shares: {}}`
    so every site is enabled by default
  - builds `default_metric_specs` from the union of `expected_data_layer_events`
    and `expected_ga4_recommended_events` across all sites, one metric per event
    with `source: "ga4"` and `front_of_card: false`
  - preserves each v1 site entry under `sites` verbatim (additionalProperties
    is `true` on the v2 schema) and additionally maps v1 GA4 fields onto the v2
    `ga4_property_env` / `ga4_property_id` / `ga4_measurement_id` /
    `ga4_measurement_env` shape so the registry loader's
    `_resolve_tracking_property()` works unchanged
  - preserves non-PWP fields (gsc_property, sitemap_url, site_dir_candidates,
    booking_provider, booking_revenue_notes) on each site so existing readers
    that scan the registry for those fields keep working
  - is idempotent: passing a v2 registry through `adapt_v1_to_v2` returns it
    essentially unchanged (with explicit defaults filled in)

This is the only file that knows the v1→v2 mapping. Everything downstream
(`pwp_kpi_site_registry.py`, `operator_migrate.py`) consumes only v2.
"""

from __future__ import annotations

from typing import Any

V2_DEFAULT_CAPABILITY: dict[str, Any] = {
    "enabled": True,
    "operator": "ned",
    "shares": {
        "google_sheet_id_env": "HDE_KPI_SHEET_ID",
        "credential_file_env": "HDE_GOOGLE_SERVICE_ACCOUNT_JSON",
        "email_to_env":        "HDE_KPI_EMAIL_TO",
        "email_to_default":    "mbgulden@gmail.com",
    },
}


def _is_v1(registry: dict) -> bool:
    """Heuristic: v1 has no `pwp_kpi_capability` block and no top-level
    `default_metric_specs`. v1 sites may carry `gsc_property` directly."""
    if not isinstance(registry, dict):
        return False
    if "pwp_kpi_capability" in registry:
        return False
    if "default_metric_specs" in registry:
        return False
    sites = registry.get("sites") or []
    if not sites:
        return False
    # The strongest signal: a v1 site has at least one of the legacy fields
    # directly on the entry, OR `ga4_property_env` without a nested
    # `pwp_kpi_override` (v2 nests these).
    for s in sites:
        if isinstance(s, dict):
            if any(k in s for k in ("gsc_property", "sitemap_url",
                                    "site_dir_candidates", "booking_provider",
                                    "expected_data_layer_events",
                                    "ga4_property_env", "ga4_measurement_env")):
                return True
    return False


def _collect_v1_event_names(sites: list[dict]) -> list[str]:
    """Union of all `expected_data_layer_events` and
    `expected_ga4_recommended_events` across v1 sites, deduplicated, sorted."""
    seen: list[str] = []
    for s in sites:
        if not isinstance(s, dict):
            continue
        for k in ("expected_data_layer_events", "expected_ga4_recommended_events"):
            for name in (s.get(k) or []):
                if isinstance(name, str) and name not in seen:
                    seen.append(name)
    # Stable order: insertion order from the registry, not alphabetical
    return seen


def _build_default_metric_specs(event_names: list[str]) -> dict[str, dict[str, Any]]:
    """One metric per event name. Source is ga4; format is number; not front_of_card.

    The metric `id` is the event name itself; `event` is the same string;
    `label` is a human-readable rendering derived from the event name.

    To avoid conflicts with curated entries that may use different metric_key
    prefixes (e.g. `funnel_booking.booking_click` vs `booking_click`), the
    default registry metrics use a `registry_default.` prefix. This ensures
    that when `migrate --merge` runs, these metrics are added cleanly and
    do not collide with curator-chosen bare IDs. The `registry_default.`
    prefix is stripped at render time for display to the user.
    """
    out: dict[str, dict[str, Any]] = {}
    for ev in event_names:
        out[ev] = {
            "id": ev,
            "label": ev.replace("_", " ").title(),
            "source": "ga4",
            "event": ev,
            "format": "number",
            "front_of_card": False,
        }
    return out


def _adapt_v1_site(site: dict) -> dict:
    """Map a v1 site entry onto the v2 site shape. Preserves all v1 fields.

    GAP-#5 FIX — env-var-only GA4 resolution:
    The static `ga4_measurement_id` literal is intentionally forced to
    `None` so the runtime always resolves the live GA4 property from the
    env-var named in `ga4_measurement_env`. This eliminates the
    tracking-property drift between the shipped config and the deployed
    loader (the static config lied about HDE's GA4 ID; the env-var held
    the truth). The migration operator's adapter continues to work
    unchanged because `_resolve_tracking_property()` always returns the
    env-var value when `ga4_measurement_id` is None.
    """
    out = dict(site)  # full passthrough of v1 fields
    # v2 contract: if the site has a GA4 measurement id literal or env name,
    # they're already named correctly in v1 (`ga4_measurement_id`,
    # `ga4_measurement_env`). Map the v1 `ga4_property_id`/`ga4_property_env`
    # onto v2's preferred names if missing.
    if "ga4_property_env" not in out and "ga4_property_env_v1" in out:
        out["ga4_property_env"] = out["ga4_property_env_v1"]
    # GAP-#5: force env-var-only resolution. Any literal GA4 ID in the
    # registry is ignored; the live GA4 property always comes from the
    # `ga4_measurement_env` env-var at runtime.
    if "ga4_measurement_id" in out:
        out["ga4_measurement_id"] = None
    # `expected_data_layer_events` and `expected_ga4_recommended_events` are
    # already on the v2 schema at the same paths, so no remapping needed.
    # v2 contract: every site must have a `pwp_kpi_override` block. Default
    # to `enabled: true` so legacy v1 sites opt into KPI tracking
    # automatically.
    out.setdefault("pwp_kpi_override", {"enabled": True})
    return out


def adapt_v1_to_v2(registry: dict) -> dict:
    """Adapt a v1 or v2 registry into the v2 shape.

    - If the input already looks like v2, return it with explicit defaults
      filled in.
    - If the input looks like v1, wrap it with v2 defaults and per-site
      mappings.
    """
    if not isinstance(registry, dict):
        raise ValueError(f"registry must be a dict; got {type(registry).__name__}")
    if not _is_v1(registry):
        # Already v2 (or unknown shape); fill in explicit defaults and return.
        v2 = dict(registry)
        v2.setdefault("pwp_kpi_capability", dict(V2_DEFAULT_CAPABILITY))
        v2.setdefault("default_metric_specs", {})
        for s in v2.get("sites") or []:
            if isinstance(s, dict):
                s.setdefault("pwp_kpi_override", {"enabled": True})
        return v2

    sites = registry.get("sites") or []
    v2: dict[str, Any] = {
        "version": registry.get("version", 1),
        "pwp_kpi_capability": dict(V2_DEFAULT_CAPABILITY),
        "default_metric_specs": _build_default_metric_specs(
            _collect_v1_event_names(sites)
        ),
        "sites": [_adapt_v1_site(s) for s in sites],
    }
    return v2


def detect_and_adapt(registry: dict) -> dict:
    """Convenience: same as adapt_v1_to_v2 but emits nothing if the input is
    empty/invalid. Returns the adapted dict."""
    return adapt_v1_to_v2(registry)