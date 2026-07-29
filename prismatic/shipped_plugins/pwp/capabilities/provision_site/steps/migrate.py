"""step migrate — trigger operator_migrate.run() for one site.

This calls into the existing `pwp_kpi_tracker.operator_migrate.run()`
to bootstrap (or merge into) the per-site `<slug>.kpi.json`.

For Phase 1 we don't have the canonical `config/seo_sites.json`
write path (it's in another lane), so we point the migrate operator
at the appendix file written by the `register_in_registry` step.
That's a Phase 2 improvement — for now we wrap a hand-built
minimal registry that points at the appendix.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _resolve_registry_path() -> Path:
    """Find the canonical `config/seo_sites.json` for the repo."""
    # Walk up from this file looking for `config/seo_sites.json`.
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        candidate = p / "config" / "seo_sites.json"
        if candidate.exists():
            return candidate
    # No canonical registry found on disk. The migrate step doesn't
    # actually need it — the minimal registry below is built from the
    # sites.json appendix instead. Return a relative anchor so the
    # path is portable across environments.
    return Path("config/seo_sites.json")


def _build_minimal_registry(slug: str, appendix_path: Path) -> Dict[str, Any]:
    """Build a minimal v1-shape registry with just one site entry.

    `operator_migrate.run()` validates the registry as v1 (`version: 1`)
    before doing anything else. Phase 1 builds a v1-compatible registry
    inline from the sites.json appendix; Phase 2 will wire the appendix
    into the canonical v1/v2 registry loader properly.

    v1 shape (see `pwp_kpi_site_registry.validate_registry_shape`):
      {
        "version": 1,
        "sites": [{"slug": ..., "name": ..., "domain": ..., ...}],
        "default_metric_specs": {...}
      }
    """
    if not appendix_path.exists():
        return {"version": 1, "sites": [], "default_metric_specs": {}}
    appendix = json.loads(appendix_path.read_text(encoding="utf-8"))
    site_entries: List[Dict[str, Any]] = []
    for domain, entry in appendix.items():
        if entry.get("slug") != slug:
            continue
        site_entries.append({
            "slug": entry["slug"],
            "name": entry.get("name", entry["slug"]),
            "domain": entry["domain"],
            "owner": entry.get("owner", ""),
            "ga4_measurement_env": entry.get("ga4_measurement_env", ""),
            "expected_data_layer_events": entry.get("expected_data_layer_events", []),
            "expected_ga4_recommended_events": entry.get("expected_ga4_recommended_events", []),
            "pwp_kpi_override": entry.get("pwp_kpi_override", {"enabled": True}),
        })
    return {
        "version": 1,
        "sites": site_entries,
        "default_metric_specs": {},
        "pwp_kpi_capability": {
            "enabled": True,
            "operator": "ned",
            "shares": {
                "google_sheet_id_env": "HDE_KPI_SHEET_ID",
                "credential_file_env": "HDE_GOOGLE_SERVICE_ACCOUNT_JSON",
                "email_to_env": "HDE_KPI_EMAIL_TO",
                "email_to_default": "mbgulden@gmail.com",
            },
        },
        "default_metric_specs": {},
        "sites": site_entries,
    }


def trigger_migrate(slug: str, publish_root: Optional[Path] = None) -> Dict[str, Any]:
    """Run `operator_migrate.run()` for `slug` against the appendix.

    Args:
      slug: the site's slug to bootstrap.
      publish_root: where to read the sites.json appendix from. If
        None, falls back to $PWP_PROVISIONING_ROOT then /tmp/pwp-
        provisioning/. The orchestrator passes the actual publish
        root so the appendix written by register_in_registry matches
        the file trigger_migrate reads from.
    """
    if publish_root is not None:
        appendix = Path(publish_root) / "sites.json"
    else:
        appendix_env = os.environ.get("PWP_PROVISIONING_ROOT", "").strip()
        if appendix_env:
            appendix = Path(appendix_env) / "sites.json"
        else:
            appendix = Path("/tmp/pwp-provisioning/sites.json")
    minimal_registry = _build_minimal_registry(slug, appendix)

    # Write the minimal registry to a temp file and point
    # operator_migrate.run() at it.
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="pwp-migrate-registry-"
    ) as f:
        json.dump(minimal_registry, f, indent=2)
        tmp_registry = Path(f.name)

    try:
        # Ensure the plugins path is importable.
        plugins_root = Path(__file__).resolve().parents[5] / "plugins"
        if str(plugins_root) not in sys.path:
            sys.path.insert(0, str(plugins_root))

        from plugins.pwp.capabilities.publish_kpi_tracker import operator_migrate

        manifest = operator_migrate.run(
            dry_run=False,
            registry_path=tmp_registry,
            force=False,
            merge=False,  # first-time bootstrap — fresh `.kpi.json`
        )
        return manifest
    finally:
        try:
            tmp_registry.unlink()
        except FileNotFoundError:
            pass
