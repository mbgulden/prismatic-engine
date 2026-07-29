"""pwp.publish_kpi_tracker — public surface for plugins/pwp integration."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schemas" / "kpi-collection.schema.json"
SITES_DIR = HERE / "sites"

from .publish_kpi_tracker import (  # noqa: F401, E402  # SITES_DIR constant is needed above
    validate,
    load_schema,
    load_site,
    load_all_collections,
    list_sites,
    resolve_collection,
    aggregate,
    render_index,
    render_detail,
    render_accordion,
)

# ── Public names consumed by plugins/pwp/plugin.py via lazy import ──────────
PUBLISH_KPI_TRACKER_CAPABILITY_ID = "pwp.publish-kpi-tracker"
PUBLISH_KPI_TRACKER_VERSION = "1.0.0"
PUBLISH_KPI_TRACKER_SITES_DIR = SITES_DIR

# Stable aliases: integration tests + the parent plugin prefer these names.
aggregate_publish_kpi = aggregate
list_publish_kpi_sites = list_sites
load_publish_kpi_site = load_site
load_publish_kpi_schema = load_schema
validate_publish_kpi_collection = validate
render_publish_kpi_index = render_index
render_publish_kpi_detail = render_detail
render_publish_kpi_accordion = render_accordion


def register_publish_kpi_plugin(plugin) -> None:
    """Adapter that records capability metadata on the host plugin instance."""
    plugin._publish_kpi_tracker_capability = {
        "id": PUBLISH_KPI_TRACKER_CAPABILITY_ID,
        "version": PUBLISH_KPI_TRACKER_VERSION,
        "sites_dir": str(PUBLISH_KPI_TRACKER_SITES_DIR),
        "schema_path": str(SCHEMA_PATH),
    }


def publish_publish_kpi_dashboard(publish_root, runtime_values=None) -> dict:
    """Render multi-site + per-site + accordion HTML into publish_root.

    Also writes a static `dashboard_data.json` snapshot alongside the HTML
    pages so the PWP dashboard can hydrate from a pre-aggregated source
    rather than calling out at render time.

    Returns the build manifest with site slugs, the publish root, the
    dashboard snapshot path, and the runtime window.
    """
    return build_dashboard(publish_root=publish_root, runtime_values=runtime_values)


def build_dashboard(*, publish_root, runtime_values=None, window="last24h",
                    write_snapshot=True, sites_dir=None) -> dict:
    """Single entry point used by both the FastAPI endpoint and ad-hoc CLI runs.

    Args:
        publish_root: directory the PWP dashboard host expects HTML in.
        runtime_values: optional dict slug -> {metric_key: value} for front-of-card
            metrics. The function is permissive: missing values render as "—".
            When None (the default), the runtime values pipeline
            (`runtime_values.RuntimeValuesBuilder`) populates the dict
            from per-site snapshots (`<slug>.runtime.json`) and any
            live-mode adapters that have credentials. The pipeline walks
            every registered site and emits one row per site.
        window: label written into the dashboard's "Window" header.
        write_snapshot: when true, write a JSON snapshot beside the HTML pages.
        sites_dir: directory that holds `<slug>.kpi.json` + the optional
            `<slug>.runtime.json` snapshots. Default is the canonical
            plugins/pwp/.../sites/ path.

    Returns:
        manifest dict with `sites`, `output_dir`, `snapshot_path`, `window`,
        and `runtime_values_path` (the file that was read, if any).
    """
    publish_root = Path(publish_root)
    publish_root.mkdir(parents=True, exist_ok=True)

    # If no runtime_values were passed, run the pipeline. The pipeline
    # walks every registered site, applies the canonical snapshot
    # (`<slug>.runtime.json`) first, then dispatches each metric to
    # its source adapter (ga4, stripe, gsc, telegram, internal,
    # verifier), and finally computes derived metrics. Sites with no
    # data produce no row.
    if runtime_values is None:
        from . import runtime_values as rv
        runtime_values = rv.build_runtime_values(sites_dir=sites_dir)
        runtime_values_path = None  # auto-built; not from a file
    else:
        runtime_values_path = None  # caller-supplied; not from a file

    # Write CSS (single source of truth for visual treatment).
    (publish_root / "pwp-publish-kpi.css").write_text(
        Path(__file__).parent.joinpath("templates", "pwp-publish-kpi.css").read_text(),
        encoding="utf-8",
    )

    # Aggregate once; render all four surfaces from the same in-memory snapshot.
    agg = aggregate(runtime_values=runtime_values)
    agg["window"] = window
    (publish_root / "index.html").write_text(render_index(agg), encoding="utf-8")
    (publish_root / "accordion.html").write_text(render_index(agg), encoding="utf-8")

    manifest = {
        "sites": [],
        "window": window,
        "output_dir": str(publish_root),
        "snapshot_path": None,
        "runtime_values_path": runtime_values_path,
    }
    for site in agg["sites"]:
        slug = site["slug"]
        (publish_root / f"{slug}.html").write_text(
            render_detail(slug, agg), encoding="utf-8"
        )
        manifest["sites"].append(slug)

    if write_snapshot:
        snapshot_path = publish_root / "dashboard_data.json"
        snapshot_path.write_text(
            json.dumps(agg, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        manifest["snapshot_path"] = str(snapshot_path)
    return manifest


def build_site_collection(slug: str, **overrides) -> dict:
    """Read the site JSON, validate it, optionally overlay runtime values, and
    return a normalized structure ready to feed the dashboard aggregator.

    Raises FileNotFoundError if the slug isn't registered.
    """
    site = load_site(slug)
    errs = validate(site)
    if errs:
        raise ValueError(
            f"site {slug!r} failed validation: " + "; ".join(errs)
        )
    out = dict(site)
    out["_runtime_overrides"] = dict(overrides)
    return out


def build_all_site_summaries(runtime_values=None) -> list:
    """Return one summary dict per registered site for use by the PWP dashboard
    'published websites' table."""
    summaries = []
    for slug in list_sites():
        try:
            site = load_site(slug)
            flat = resolve_collection(slug)
        except (FileNotFoundError, ValueError) as exc:
            summaries.append({"slug": slug, "error": str(exc)})
            continue
        # Pick a canonical "headline" metric (first front_of_card if any, else first metric).
        headline_id = None
        for mid, m in flat["metrics"].items():
            if m.get("front_of_card"):
                headline_id = mid
                break
        if headline_id is None and flat["metrics"]:
            headline_id = next(iter(flat["metrics"]))
        rv = (runtime_values or {}).get(slug, {})
        headline_value = rv.get(headline_id) if headline_id else None
        summaries.append({
            "slug": slug,
            "name": site.get("name"),
            "domain": site.get("domain"),
            "extends": site.get("extends"),
            "tracking_property": site.get("tracking_property"),
            "metric_count": len(flat["metrics"]),
            "headline_metric_id": headline_id,
            "headline_metric_label": flat["metrics"][headline_id]["label"] if headline_id else None,
            "headline_value": headline_value,
        })
    return summaries


def read_runtime_values(path: str | None) -> dict:
    """Convenience reader: load a JSON runtime-values snapshot from disk.

    The shape is `{slug: {metric_key: value}}` — same as the in-memory shape
    accepted by `aggregate(runtime_values=...)` and `build_dashboard(...)`.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


__all__ = [
    "validate",
    "load_site",
    "load_all_collections",
    "list_sites",
    "resolve_collection",
    "aggregate",
    "render_index",
    "render_detail",
    "render_accordion",
    "SCHEMA_PATH",
    "SITES_DIR",
    "HERE",
    "PUBLISH_KPI_TRACKER_CAPABILITY_ID",
    "PUBLISH_KPI_TRACKER_VERSION",
    "PUBLISH_KPI_TRACKER_SITES_DIR",
    "aggregate_publish_kpi",
    "list_publish_kpi_sites",
    "load_publish_kpi_site",
    "load_publish_kpi_schema",
    "validate_publish_kpi_collection",
    "render_publish_kpi_index",
    "render_publish_kpi_detail",
    "render_publish_kpi_accordion",
    "register_publish_kpi_plugin",
    "publish_publish_kpi_dashboard",
    "build_dashboard",
    "build_site_collection",
    "build_all_site_summaries",
    "read_runtime_values",
]
