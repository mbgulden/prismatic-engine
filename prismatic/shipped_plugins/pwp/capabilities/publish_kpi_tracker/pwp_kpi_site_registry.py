"""pwp_kpi_site_registry — load and validate the PWP site registry.

The registry lives at `<PWP_REPO>/config/seo_sites.json` (the existing Prismatic
config file that already enumerates all sites and their canonical GA4 + GSC
info). This module:

  - reads + JSON-Schema-validates the registry
  - resolves the per-site `tracking_property` from `ga4_measurement_id` or
    the env-var named in `ga4_measurement_env`
  - exposes `iter_sites()` so the operator can build per-site *.kpi.json files
  - exposes `iter_metric_specs_for_site()` which merges default_metric_specs
    and per-site pwp_kpi_metric_specs into a single ordered list of metric
    definitions ready for `site_builder.write_site_collection(...)`

This module is read-only; it does NOT write any *.kpi.json file (that's the
operator's job in `operator_migrate.py`).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from .publish_kpi_tracker import ALLOWED_SOURCES

# Walk up from this file until we find a directory that contains
# config/seo_sites.json (the canonical registry). This is robust against
# loading via the plugins/ symlink vs the prismatic/shipped_plugins/ target,
# because we anchor on a concrete file rather than counting parent levels.
def _walk_to_pwp_repo() -> Path:
    cur = Path(__file__).resolve().parent
    for _ in range(10):
        if (cur / "config" / "seo_sites.json").is_file():
            return cur
        cur = cur.parent
    raise FileNotFoundError(
        f"pwp_kpi_site_registry: could not locate PWP_REPO from {cur}; "
        f"no config/seo_sites.json within 10 parent levels. "
        f"Set PWP_REPO_ROOT to override."
    )

REPO_ROOT = Path(os.environ.get("PWP_REPO_ROOT") or _walk_to_pwp_repo())

# Regex compiled at module import.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
GA4_MEAS_RE = re.compile(r"^G-[A-Z0-9]{4,12}$")


def _resolve_registry_path() -> Path:
    """Resolve the registry file location. The PWP_REPO env var overrides."""
    env_root = os.environ.get("PWP_REPO_ROOT")
    root = Path(env_root) if env_root else REPO_ROOT
    return root / "config" / "seo_sites.json"


def _resolve_schema_path() -> Path:
    """Resolve the schema file location. Lives inside the PWP plugin
    directory (Ned's lane) so the schema travels with the plugin code.
    The PWP_REPO env var overrides.
    """
    env_root = os.environ.get("PWP_REPO_ROOT")
    root = Path(env_root) if env_root else REPO_ROOT
    return (
        root
        / "plugins"
        / "pwp"
        / "capabilities"
        / "publish_kpi_tracker"
        / "schemas"
        / "kpi-registry.schema.json"
    )


def _resolve_sites_dir() -> Path:
    env_root = os.environ.get("PWP_REPO_ROOT")
    root = Path(env_root) if env_root else REPO_ROOT
    return root / "plugins" / "pwp" / "capabilities" / "publish_kpi_tracker" / "sites"


def _resolve_tracking_property(site: dict) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the GA4 measurement ID for a site.

    GAP-#5 FIX — env-var-only:
    The static `ga4_measurement_id` literal is intentionally ignored.
    The live GA4 property always comes from the env-var named in
    `ga4_measurement_env`. This eliminates the tracking-property drift
    between the shipped config and the deployed loader — the static
    config used to lie about HDE's GA4 ID; the env-var holds the truth.

    Order of preference:
      1. `os.environ.get(site['ga4_measurement_env'])` if the env name is set
      2. None

    Returns (measurement_id, source) where source ∈ {"env", "none"}.
    """
    env_name = site.get("ga4_measurement_env")
    if env_name:
        v = os.environ.get(env_name)
        if v and GA4_MEAS_RE.match(v):
            return v, "env"
    return None, "none"


def load_registry(path: Optional[Path] = None) -> dict:
    """Load the registry file. Caller may override `path` for tests.

    The returned dict is always in the v2 shape. If the on-disk file is the
    legacy v1 shape (no top-level `pwp_kpi_capability`, sites carrying
    `gsc_property`/`expected_data_layer_events` directly), the v1→v2 adapter
    `legacy_seo_registry.adapt_v1_to_v2` is applied transparently so
    downstream consumers always see v2 fields.
    """
    p = Path(path) if path else _resolve_registry_path()
    if not p.exists():
        raise FileNotFoundError(f"pwp_kpi_site_registry: registry not found at {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    # Imported here to avoid an import cycle at module load time. The adapter
    # is a pure function over a dict; it doesn't pull in anything else.
    from .legacy_seo_registry import adapt_v1_to_v2
    return adapt_v1_to_v2(raw)


def load_schema(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else _resolve_schema_path()
    return json.loads(p.read_text(encoding="utf-8"))


def validate_registry_shape(registry: dict, schema: dict) -> List[str]:
    """Tiny structural check: required keys present, slugs unique, expected events non-empty.

    Full JSON-Schema validation is left to the operator (and to the ad-hoc
    verifier) because adding a third-party dep for a 4-field check is overkill.
    """
    errs: List[str] = []
    if registry.get("version") != 1:
        errs.append("registry.version must be 1")
    sites = registry.get("sites") or []
    if not isinstance(sites, list) or not sites:
        errs.append("registry.sites must be a non-empty array")
    seen: set = set()
    for s in sites:
        if not isinstance(s, dict):
            errs.append(f"registry.sites: entry not a dict: {s!r}")
            continue
        slug = s.get("slug")
        if not slug or not SLUG_RE.match(slug):
            errs.append(f"registry.sites: bad slug: {slug!r}")
        elif slug in seen:
            errs.append(f"registry.sites: duplicate slug: {slug!r}")
        else:
            seen.add(slug)
        if "name" not in s:
            errs.append(f"site {slug!r}: missing 'name'")
        if "domain" not in s:
            errs.append(f"site {slug!r}: missing 'domain'")
    return errs


def iter_sites(registry: dict) -> Iterator[dict]:
    """Yield each site in the registry, with tracking_property resolved (env lookup or literal)."""
    for site in registry.get("sites") or []:
        resolved_id, source = _resolve_tracking_property(site)
        out = dict(site)
        out["_tracking_property_resolved"] = resolved_id
        out["_tracking_property_source"] = source
        yield out


def iter_metric_specs_for_site(registry: dict, site: dict) -> List[dict]:
    """Merge default_metric_specs + per-site pwp_kpi_metric_specs into one ordered list.

    Per-site specs override defaults by metric id. Specs are returned as a list
    (registry order preserved) so the operator can keep deterministic ordering.
    """
    defaults = registry.get("default_metric_specs") or {}
    site_overrides = site.get("pwp_kpi_metric_specs") or {}

    # First apply defaults in declared order, then overlay overrides.
    merged: Dict[str, dict] = {}
    for mid, m in defaults.items():
        merged[mid] = dict(m)
    for mid, m in site_overrides.items():
        merged[mid] = dict(m)

    # Validate sources and minimal fields (cheap shape check; full JSON-Schema
    # validation lives in the operator's pre-migration verifier).
    errs: List[str] = []
    for mid, m in merged.items():
        if m.get("source") not in ALLOWED_SOURCES:
            errs.append(f"site {site.get('slug')!r} metric {mid!r}: source {m.get('source')!r} invalid")
    if errs:
        raise ValueError("; ".join(errs))
    return list(merged.values())


def site_override_enabled(registry: dict, site: dict) -> bool:
    """A site is enabled unless its `pwp_kpi_override.enabled` is explicitly false."""
    override = site.get("pwp_kpi_override") or {}
    if "enabled" in override:
        return bool(override["enabled"])
    cap = registry.get("pwp_kpi_capability") or {}
    return bool(cap.get("enabled", True))


def site_shares(registry: dict, site: dict) -> dict:
    """Resolve share-target env vars for a site. The site may override the
    capability-level defaults.
    """
    cap = registry.get("pwp_kpi_capability") or {}
    base_shares = (cap.get("shares") or {})
    return dict(base_shares)  # callers may overlay per-site values
