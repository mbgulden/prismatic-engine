"""Site-builder operator: build a per-site *.kpi.json from a source of truth.

The site-builder extracts the canonical schema fields from a "source of truth"
configuration (a dict, an in-process inventory, or a JSON file produced by
Prowl/Operator migrations) and produces a well-formed per-site collection
that passes the validator.

This is a small, explicit operator used by the Prismatic Engine cron, the
PWP dashboard build pipeline, and ad-hoc migrations from `config/seo_sites.json`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ALLOWED_SOURCES = {
    "ga4", "stripe", "telegram", "internal",
    "derived", "gsc", "mcp", "sheets",
    "ci", "verifier",
}


def build_site_collection(
    *,
    slug: str,
    domain: str,
    name: str,
    tracking_property: str = "",
    metric_specs: list[dict[str, Any]] | None = None,
    extends: str | None = None,
    share_targets: dict[str, str] | None = None,
    delivery_cadence: dict[str, dict[str, str]] | None = None,
    site_title: str | None = None,
    extra_globally_required: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a per-site *.kpi.json collection dict.

    Args:
        slug: site slug, e.g. "active-oahu".
        domain: site domain, e.g. "activeoahutours.com".
        name: collection name (also used as the human-readable title).
        tracking_property: GA4 measurement ID (e.g. "G-XXXXXXX"). Optional.
        metric_specs: list of dicts, each with at least {id, label, source} and
            the standard optional fields (event, metric, field, filter, format, formula,
            front_of_card, source_urls, alert_threshold).
        extends: optional parent slug for inheritance.
        share_targets: optional dict mapping canonical env-name keys to env-var names.
        delivery_cadence: optional dict with daily/weekly/monthly sub-dicts.
        site_title: optional human-readable site title; defaults to name.
        extra_globally_required: optional dict merged into globally_required.

    Returns:
        The collection dict. Save with `json.dumps(..., indent=2)` to disk.
    """
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,63}$", slug):
        raise ValueError(f"invalid slug: {slug!r}")
    spec_list = list(metric_specs or [])
    metrics_obj: dict[str, dict[str, Any]] = {}
    expected_events: list[str] = []
    ga4_recommended: list[str] = []
    for m in spec_list:
        if not isinstance(m, dict):
            raise ValueError(f"metric spec must be a dict; got {type(m).__name__}")
        for required in ("id", "label", "source"):
            if required not in m:
                raise ValueError(f"metric missing {required!r}: {m}")
        if m["source"] not in ALLOWED_SOURCES:
            raise ValueError(f"metric {m['id']!r} source {m['source']!r} not in {sorted(ALLOWED_SOURCES)}")
        # Note: the metric key in the per-site *.kpi.json is the bare metric id.
        # Inheritance is handled at the *collection* level (resolve_collection
        # merges parent + child metrics on key collision), not at the metric
        # key level. Prefixing here would also introduce a dash when the
        # parent slug has a dash, which would fail the metric-key regex
        # `^[a-z0-9._*]+$`.
        metrics_obj[m["id"]] = m
        if m.get("event"):
            expected_events.append(m["event"])
        # Treat ga4 source as the recommended-events class.
        if m["source"] == "ga4" and m.get("event"):
            ga4_recommended.append(m["event"])
    site_events: list[str] = list(expected_events)
    if extra_globally_required:
        # Only the dataLayer events propagate to the site's own list. The
        # ga4_recommended_events stay in globally_required only.
        extra = extra_globally_required.get("expected_data_layer_events")
        if isinstance(extra, list):
            site_events.extend(extra)
    site: dict[str, Any] = {
        "title": site_title or name,
        "expected_dataLayer_events": list(dict.fromkeys(site_events)),
    }
    out: dict[str, Any] = {
        "schema_version": "1.0",
        "name": name,
        "owner": "ned",
        "site_slug": slug,
        "domain": domain,
        "site": site,
        "metrics": metrics_obj,
    }
    if tracking_property:
        out["tracking_property"] = tracking_property
    if extends:
        out["extends"] = extends
    # The top-level expected_data_layer_events and ga4_recommended_events keys
    # are the de-facto standard for the existing canonical sites (hd-engine,
    # active-oahu). The site_builder keeps them at the top level so ad-hoc
    # readers and the canonical operator_cli see them in the same place.
    # If the caller passed extra_globally_required with these keys, lift them
    # to the top level too.
    lifted_events: list[str] = list(expected_events)
    if extra_globally_required:
        for k in ("expected_data_layer_events", "ga4_recommended_events"):
            extra = extra_globally_required.get(k)
            if isinstance(extra, list):
                lifted_events.extend(extra)
    if lifted_events:
        out["expected_data_layer_events"] = list(dict.fromkeys(lifted_events))
    lifted_recommended: list[str] = list(ga4_recommended)
    if extra_globally_required:
        for k in ("expected_data_layer_events", "ga4_recommended_events"):
            extra = extra_globally_required.get(k)
            if isinstance(extra, list):
                lifted_recommended.extend(extra)
    if lifted_recommended:
        out["ga4_recommended_events"] = list(dict.fromkeys(lifted_recommended))
    globally_required: dict[str, Any] = {
        "expected_loader_on_every_page": True,
        "expected_data_layer_events": list(dict.fromkeys(expected_events)),
    }
    if tracking_property:
        globally_required["tracking_property"] = tracking_property
    if extra_globally_required:
        globally_required.update(extra_globally_required)
    if ga4_recommended:
        globally_required["ga4_recommended_events"] = list(dict.fromkeys(ga4_recommended))
    out["globally_required"] = globally_required
    out["share_targets"] = share_targets or {
        "google_sheet_id_env": f"{slug.upper().replace('-', '_')}_KPI_SHEET_ID",
        "credential_file_env":  f"{slug.upper().replace('-', '_')}_GOOGLE_SERVICE_ACCOUNT_JSON",
        "email_to_env":         f"{slug.upper().replace('-', '_')}_KPI_EMAIL_TO",
        "email_to_default":     "mbgulden@gmail.com",
        "dashboard_route":      "/pwp/kpi/",
    }
    out["delivery_cadence"] = delivery_cadence or {
        "daily":   {"kind": "daily"},
        "weekly":  {"kind": "weekly"},
        "monthly": {"kind": "monthly"},
    }
    return out


def write_site_collection(
    sites_dir: Path,
    *,
    slug: str,
    domain: str,
    name: str,
    tracking_property: str = "",
    metric_specs: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> Path:
    """Build the collection dict and write it to sites_dir / <slug>.kpi.json.

    Returns the path of the written file.
    """
    sites_dir = Path(sites_dir)
    sites_dir.mkdir(parents=True, exist_ok=True)
    coll = build_site_collection(
        slug=slug, domain=domain, name=name,
        tracking_property=tracking_property, metric_specs=metric_specs, **kwargs,
    )
    target = sites_dir / f"{slug}.kpi.json"
    target.write_text(json.dumps(coll, indent=2, sort_keys=True), encoding="utf-8")
    return target


def build_sites_from_inventory(
    inventory: list[dict[str, Any]],
    sites_dir: Path,
    *,
    per_site_metric_specs: dict[str, list[dict[str, Any]]] | None = None,
) -> list[Path]:
    """Write one *.kpi.json per inventory entry, using per-site metric specs.

    `inventory` is a list of dicts each with at least: {slug, domain, name,
    tracking_property, [extends]}. `per_site_metric_specs` maps slug -> list of
    metric specs (so the operator can build different metrics per site from
    a single source).
    """
    out = []
    for entry in inventory:
        slug = entry["slug"]
        specs = (per_site_metric_specs or {}).get(slug, [])
        target = write_site_collection(
            sites_dir,
            slug=slug,
            domain=entry["domain"],
            name=entry.get("name", slug),
            tracking_property=entry.get("tracking_property", ""),
            metric_specs=specs,
            extends=entry.get("extends"),
        )
        out.append(target)
    return out
