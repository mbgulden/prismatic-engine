"""pwp.publish_kpi_tracker — public surface for plugins/pwp integration."""

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
    """Render multi-site + per-site + accordion HTML into publish_root."""
    runtime_values = runtime_values or {}
    publish_root = Path(publish_root)
    publish_root.mkdir(parents=True, exist_ok=True)
    (publish_root / "pwp-publish-kpi.css").write_text(
        Path(__file__).parent.joinpath("templates", "pwp-publish-kpi.css").read_text(),
        encoding="utf-8",
    )
    agg = aggregate(runtime_values=runtime_values)
    (publish_root / "index.html").write_text(render_index(agg), encoding="utf-8")
    (publish_root / "accordion.html").write_text(render_index(agg), encoding="utf-8")
    manifest = {"sites": [], "window": agg["window"], "output_dir": str(publish_root)}
    for site in agg["sites"]:
        slug = site["slug"]
        (publish_root / f"{slug}.html").write_text(
            render_detail(slug, agg), encoding="utf-8"
        )
        manifest["sites"].append(slug)
    return manifest


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
]
