"""operator_migrate — derive *.kpi.json from config/seo_sites.json.

For each enabled site in the registry, build a per-site *.kpi.json and write
it to plugins/pwp/capabilities/publish_kpi_tracker/sites/<slug>.kpi.json. The
generated file passes the canonical validator() and the JSON Schema check.

The operator is idempotent: re-running it with the same registry leaves the
per-site file unchanged (modulo whitespace). It is also dry-run aware: with
--dry-run, no files are written; the manifest is printed to stdout instead.

Used by:
  - PWP publish-kpi-tracker operator_cli.py migrate   (CLI)
  - pwp-kpi-tracker-operator_cli.mjs migrate          (Node wrapper)
  - Prismatic Engine cron via the operator's run_daily job
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
# Resolve PWP_REPO by walking up from HERE until we find a directory that
# looks like the repo root. This is robust against symlink-vs-target loading
# because we anchor on the presence of `config/seo_sites.json` (the canonical
# registry file) rather than counting parent levels.
def _walk_to_pwp_repo(here: Path) -> Path:
    cur = here
    for _ in range(10):
        if (cur / "config" / "seo_sites.json").is_file():
            return cur
        cur = cur.parent
    raise FileNotFoundError(
        f"operator_migrate: could not locate PWP_REPO from {here}; "
        f"no config/seo_sites.json within 10 parent levels. "
        f"Set PWP_REPO_ROOT to override."
    )
PWP_REPO = Path(os.environ.get("PWP_REPO_ROOT") or _walk_to_pwp_repo(HERE))
for p in (PWP_REPO, PWP_REPO / "prismatic" / "shipped_plugins"):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from . import publish_kpi_tracker as kpi  # noqa: E402
from .pwp_kpi_site_registry import (  # noqa: E402
    _resolve_registry_path,
    iter_metric_specs_for_site,
    iter_sites,
    load_registry,
    load_schema,
    site_override_enabled,
    site_shares,
    validate_registry_shape,
)
from .site_builder import build_site_collection  # noqa: E402


def _resolve_tracking_property_for_migration(site: dict) -> Optional[str]:
    """Pulls the resolved tracking_property from iter_sites output."""
    return site.get("_tracking_property_resolved")


def _delivery_cadence() -> dict:
    return {
        "daily":   {"kind": "daily"},
        "weekly":  {"kind": "weekly"},
        "monthly": {"kind": "monthly"},
    }


def _site_label(slug: str) -> str:
    # Convert e.g. "active-oahu" -> "active-oahu-funnel"
    return f"{slug}-funnel"


def _build_site_metrics(registry: dict, site: dict, slug: str) -> List[dict]:
    """Build the list of metric specs for one site, in deterministic order.

    Order:
      1. Specs derived from registry.default_metric_specs (registry order)
      2. Site-specific overrides (registry.pwp_kpi_metric_specs order)
    """
    return iter_metric_specs_for_site(registry, site)


def _default_shares(slug: str) -> dict:
    upper = slug.upper().replace("-", "_")
    return {
        "google_sheet_id_env":  f"{upper}_KPI_SHEET_ID",
        "credential_file_env":  f"{upper}_GOOGLE_SERVICE_ACCOUNT_JSON",
        "email_to_env":         f"{upper}_KPI_EMAIL_TO",
        "email_to_default":     "mbgulden@gmail.com",
        "dashboard_route":      "/pwp/kpi/",
    }


def _share_target_overlay(registry: dict, site: dict, slug: str) -> dict:
    """Build the share_targets dict for one site."""
    base = site_shares(registry, site)
    out = dict(_default_shares(slug))
    # If the capability-level shares are set, those take precedence over the
    # env-var-name defaults. The runtime values come from the env at scheduler
    # invocation time.
    for k, v in base.items():
        if v:
            out[k] = v
    return out


def _build_collection(registry: dict, site: dict) -> dict:
    """Build the per-site *.kpi.json dict for one site."""
    slug = site["slug"]
    tracking_property = _resolve_tracking_property_for_migration(site) or ""
    metrics = _build_site_metrics(registry, site, slug)
    return build_site_collection(
        slug=slug,
        domain=site["domain"],
        name=site.get("name", _site_label(slug)),
        tracking_property=tracking_property,
        metric_specs=metrics,
        extends=(site.get("pwp_kpi_override") or {}).get("extends"),
        share_targets=_share_target_overlay(registry, site, slug),
        delivery_cadence=_delivery_cadence(),
        site_title=site.get("name", _site_label(slug)),
        extra_globally_required={
            "expected_data_layer_events": site.get("expected_data_layer_events", []),
            "ga4_recommended_events": site.get("expected_ga4_recommended_events", []),
        },
    )


def _filter_validated(collections: dict, slug_to_collection: dict) -> List[str]:
    """Run the canonical validator on every generated collection; return error list."""
    errs: List[str] = []
    for slug, coll in slug_to_collection.items():
        v = kpi.validate(coll)
        if v:
            errs.extend([f"{slug}: {e}" for e in v])
    return errs


def run(*, dry_run: bool = False, registry_path: Optional[Path] = None,
        sites_dir: Optional[Path] = None, force: bool = False) -> dict:
    """Migrate config/seo_sites.json into per-site *.kpi.json files.

    Returns a manifest with `sites` (list of {slug, status, path, error}) and
    `registry_path` / `sites_dir` so the operator can echo or write a summary.

    Safety: a site is skipped (status="skipped (exists)") if its target file
    already exists and `force=False`. This protects curated per-site
    *.kpi.json files from being clobbered by a registry re-run. Pass
    `force=True` to overwrite. Dry-run never writes; the manifest shows the
    would-be write set.
    """
    registry = load_registry(registry_path)
    errs = validate_registry_shape(registry, load_schema())
    if errs:
        raise ValueError("registry failed shape check: " + "; ".join(errs))

    sites_dir = sites_dir or (PWP_REPO / "plugins" / "pwp" / "capabilities" / "publish_kpi_tracker" / "sites")
    sites_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Any] = {"sites": [], "registry_path": str(_resolve_registry_path() if not registry_path else registry_path), "sites_dir": str(sites_dir), "dry_run": dry_run, "force": force}
    slug_to_collection: Dict[str, dict] = {}

    for site in iter_sites(registry):
        slug = site["slug"]
        if not site_override_enabled(registry, site):
            manifest["sites"].append({"slug": slug, "status": "skipped (disabled)"})
            continue
        target = sites_dir / f"{slug}.kpi.json"
        if target.exists() and not force and not dry_run:
            # Curated file already on disk; protect it. Re-running migrate is
            # safe. To intentionally re-bootstrap from the registry, pass
            # `force=True`.
            manifest["sites"].append({
                "slug": slug,
                "status": "skipped (exists)",
                "path": str(target),
            })
            continue
        try:
            coll = _build_collection(registry, site)
        except Exception as exc:
            manifest["sites"].append({"slug": slug, "status": "error", "error": str(exc)})
            continue
        slug_to_collection[slug] = coll
        if not dry_run:
            target.write_text(
                json.dumps(coll, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        manifest["sites"].append({
            "slug": slug,
            "status": "written" if not dry_run else "dry_run",
            "path": str(target),
            "metric_count": len(coll["metrics"]),
            "tracking_property": coll.get("tracking_property"),
        })

    # Always run the canonical validator over the in-memory set; even on
    # dry_run this is useful to surface schema drift before the next commit.
    errs = _filter_validated(registry, slug_to_collection)
    manifest["validation_errors"] = errs
    return manifest


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pwp-kpi-migrate",
        description="Migrate config/seo_sites.json into per-site *.kpi.json files.",
    )
    p.add_argument(
        "--registry",
        help="Path to seo_sites.json (default inferred from PWP_REPO_ROOT).",
    )
    p.add_argument(
        "--sites-dir",
        help="Output directory for *.kpi.json (default: plugins/pwp/.../sites/).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write files; print the manifest instead.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing per-site *.kpi.json files. Default is to skip sites whose file already exists (preserves curated metric specs).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = run(
        dry_run=args.dry_run,
        registry_path=Path(args.registry) if args.registry else None,
        sites_dir=Path(args.sites_dir) if args.sites_dir else None,
        force=args.force,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if manifest.get("validation_errors"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
