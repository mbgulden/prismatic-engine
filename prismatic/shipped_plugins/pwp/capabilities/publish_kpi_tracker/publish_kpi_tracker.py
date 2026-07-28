"""pwp.publish_kpi_tracker — PWP capability for standardized KPI tracking + visibility.

Public API:
    validate(collection_dict) -> list[str]
    load_site(slug) / load_all_collections() / list_sites()
    resolve_collection(slug) -> dict             # merges parent metrics + site overrides
    aggregate(runtime_values=...) -> dict        # multi-site shape used by the dashboards
    render_index(aggregated) / render_detail(slug, aggregated) / render_accordion(aggregated) -> str (HTML)

Plus hooks consumed by plugins/pwp/plugin.py:
    register_publish_kpi_plugin(plugin)
    publish_publish_kpi_dashboard(publish_root, runtime_values=None)

Storage layout:
    <capability>/schemas/kpi-collection.schema.json   JSON Schema 2020-12
    <capability>/sites/<slug>.kpi.json               per-site site files
    <capability>/templates/pwp-publish-kpi.css
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schemas" / "kpi-collection.schema.json"
SITES_DIR = HERE / "sites"
DEFAULT_PARENT_SLUG = "hd-engine"

ALLOWED_SOURCES = {
    "ga4",
    "stripe",
    "telegram",
    "internal",
    "derived",
    "gsc",
    "mcp",
    "sheets",
    "ci",
    "verifier",
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"pwp.publish_kpi_tracker: missing file {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema() -> dict:
    return _load_json(SCHEMA_PATH)


def load_site(slug: str) -> dict:
    return _load_json(SITES_DIR / f"{slug}.kpi.json")


def list_sites() -> List[str]:
    return sorted(p.stem[:-4] for p in SITES_DIR.glob("*.kpi.json"))


def load_all_collections() -> Dict[str, dict]:
    return {slug: load_site(slug) for slug in list_sites()}


# ── Validation (no third-party deps; stdlib + simple checks) ───────────────────
def validate(collection: dict, parent: dict | None = None) -> List[str]:
    errs: List[str] = []
    name = collection.get("name") or "<unnamed>"
    for fld in ("schema_version", "name", "owner", "metrics"):
        if fld not in collection:
            errs.append(f"{name}: missing required field '{fld}'")
    if "site_slug" in collection and not isinstance(collection["site_slug"], str):
        errs.append(f"{name}: site_slug must be string")
    for m_id, m in collection.get("metrics", {}).items():
        if not isinstance(m_id, str) or not all(
            c.isalnum() or c in "._*" for c in m_id
        ):
            errs.append(f"{name}.metrics: key '{m_id}' must match ^[a-z0-9._*]+$")
        if not isinstance(m, dict):
            errs.append(f"{name}.metrics.{m_id}: must be object")
            continue
        for f in ("id", "label", "source"):
            if f not in m:
                errs.append(f"{name}.metrics.{m_id}: missing required field '{f}'")
        if "source" in m and m["source"] not in ALLOWED_SOURCES:
            errs.append(
                f"{name}.metrics.{m_id}: source '{m['source']}' not in {sorted(ALLOWED_SOURCES)}"
            )
        for fld in ("format",):
            if fld in m and m[fld] not in {"number", "percent", "currency", "duration"}:
                errs.append(f"{name}.metrics.{m_id}: format '{m[fld]}' invalid")
        # Cross-check metric 'id' inside the metric matches the trailing segment of the key
        if "id" in m and "." in m_id:
            tail = m_id.split(".", 1)[1]
            if m["id"] != tail:
                errs.append(
                    f"{name}.metrics.{m_id}: inner id='{m['id']}' must match tail of key ('{tail}')"
                )
    return errs


def resolve_collection(slug: str) -> dict:
    """Flatten parent metrics + site metrics, overriding on key collision."""
    site = load_site(slug)
    parent_slug = site.get("extends") or (
        DEFAULT_PARENT_SLUG if slug != DEFAULT_PARENT_SLUG else None
    )
    parent = (
        load_site(parent_slug)
        if parent_slug and (SITES_DIR / f"{parent_slug}.kpi.json").exists()
        else None
    )
    merged_metrics = dict(site.get("metrics", {}))
    if parent:
        merged_metrics = {**parent.get("metrics", {}), **merged_metrics}
    flat = dict(site)
    flat["metrics"] = merged_metrics
    flat["_parent_slug"] = parent_slug
    return flat


# ── Aggregation ─────────────────────────────────────────────────────────────
def aggregate(
    runtime_values: Dict[str, Dict[str, Any]] | None = None,
    sources: Dict[str, Dict[str, str]] | None = None,
    window: str = "last24h",
) -> dict:
    """Multi-site shape used by the dashboards.

    runtime_values: slug → {metric_key → value}
    sources: slug → {metric_key → delta_pct text}
    """
    runtime_values = runtime_values or {}
    sources = sources or {}
    out = {"window": window, "sites": []}
    for slug in list_sites():
        flat = resolve_collection(slug)
        rv = runtime_values.get(slug, {})
        cards = []
        for m_id, m in flat.get("metrics", {}).items():
            if not m.get("front_of_card"):
                continue
            value = rv.get(m_id)
            cards.append(
                {
                    "metric_key": m_id,
                    "id": m.get("id"),
                    "label": m.get("label"),
                    "value": value,
                    "format": m.get("format", "number"),
                    "delta_pct": sources.get(slug, {}).get(m_id),
                }
            )
        out["sites"].append(
            {
                "slug": slug,
                "name": flat.get("name"),
                "domain": flat.get("domain"),
                "extends": flat.get("extends"),
                "owner": flat.get("owner"),
                "metric_count": len(flat.get("metrics", {})),
                "front_of_card": cards,
            }
        )
    return out


# ── HTML rendering ──────────────────────────────────────────────────────────
def _esc(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_index(agg: dict) -> str:
    rows = []
    for s in agg.get("sites", []):
        cards = "".join(
            f'<td class="num">{_esc(c.get("label"))}<br><span class="muted">{_esc(c.get("value"))}</span></td>'
            for c in s.get("front_of_card", [])
        )
        detail = f'<a href="/pwp/kpi/{_esc(s["slug"])}.html">View details</a>'
        rows.append(
            f"<tr><td><strong>{_esc(s['name'])}</strong><br>"
            f'<span class="muted">{_esc(s["domain"])}</span></td>'
            f'<td><a href="/pwp/kpi/{_esc(s["slug"])}.html">{_esc(s["slug"])}</a></td>'
            f"<td>{_esc(s['owner'])}</td>"
            f"<td>{detail}</td>"
            f"<td>{s.get('metric_count', 0)}</td>"
            f"<td>{cards or '<span class=muted>—</span>'}</td></tr>"
        )
    table = "\n".join(rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PWP Publish KPI Tracker — Multi-site Index</title>
  <meta name="robots" content="noindex">
  <link rel="stylesheet" href="/pwp/pwp-dashboard.css">
  <link rel="stylesheet" href="/pwp/kpi/pwp-publish-kpi.css">
</head>
<body>
<header class="pwp-header">
  <h1>PWP Publish KPI Tracker</h1>
  <p class="muted">Window: {_esc(agg.get("window"))} · Sites tracked: {len(agg.get("sites", []))}</p>
</header>
<main>
  <section class="pwp-section">
    <h2>Multi-site index</h2>
    <table class="pwp-kpi-table">
      <thead>
        <tr><th>Site</th><th>Slug</th><th>Owner</th><th>Detail</th><th># Metrics</th><th>Front-of-card metrics</th></tr>
      </thead>
      <tbody>{table}</tbody>
    </table>
  </section>
  <section class="pwp-section">
    <h2>Accordion view</h2>
    {render_accordion(agg)}
  </section>
</main>
<footer><p class="muted">Generated by <code>pwp.publish_kpi_tracker</code> · {len(agg.get("sites", []))} sites</p></footer>
</body>
</html>
"""


def render_detail(slug: str, agg: dict) -> str:
    flat = resolve_collection(slug)
    metrics = flat.get("metrics", {})
    cards = "".join(
        f'<details class="pwp-kpi-detail" open>'
        f"<summary><strong>{_esc(m.get('label'))}</strong> "
        f'<span class="muted">{_esc(m.get("source"))} · '
        f"{_esc(m.get('event') or m.get('metric') or m.get('field') or m.get('filter') or '')}</span></summary>"
        f"<dl>"
        f"<dt>id</dt><dd><code>{_esc(mid)}</code></dd>"
        f"<dt>label</dt><dd>{_esc(m.get('label'))}</dd>"
        f"<dt>source</dt><dd>{_esc(m.get('source'))}</dd>"
        f"<dt>format</dt><dd>{_esc(m.get('format', 'number'))}</dd>"
        + (
            f"<dt>event</dt><dd><code>{_esc(m.get('event'))}</code></dd>"
            if m.get("event")
            else ""
        )
        + (
            f"<dt>filter</dt><dd><code>{_esc(m.get('filter'))}</code></dd>"
            if m.get("filter")
            else ""
        )
        + (
            f"<dt>formula</dt><dd><code>{_esc(m.get('formula'))}</code></dd>"
            if m.get("formula")
            else ""
        )
        + "</dl></details>"
        for mid, m in metrics.items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PWP KPI · {_esc(flat.get("name"))}</title>
  <meta name="robots" content="noindex">
  <link rel="stylesheet" href="/pwp/pwp-dashboard.css">
  <link rel="stylesheet" href="/pwp/kpi/pwp-publish-kpi.css">
</head>
<body>
<header class="pwp-header">
  <h1>{_esc(flat.get("name"))}</h1>
  <p class="muted"><a href="/pwp/kpi/">← Back to multi-site index</a></p>
  <p>{_esc(flat.get("description", ""))}</p>
  <p class="muted">Domain: {_esc(flat.get("domain", ""))} · Owner: {_esc(flat.get("owner", ""))} · Extends: {_esc(flat.get("extends", "—"))}</p>
</header>
<main>
  <section class="pwp-section">
    <h2>All metrics ({len(metrics)})</h2>
    {cards}
  </section>
</main>
</body>
</html>
"""


def render_accordion(agg: dict) -> str:
    blocks = []
    for s in agg.get("sites", []):
        cards = "".join(
            f"<li><strong>{_esc(c.get('label'))}</strong>: "
            f'<span class="num">{_esc(c.get("value"))}</span>'
            + (
                f' <span class="muted">{_esc(c.get("delta_pct"))}</span>'
                if c.get("delta_pct")
                else ""
            )
            + "</li>"
            for c in s.get("front_of_card", [])
        )
        blocks.append(
            f'<details class="pwp-kpi-accordion"><summary>'
            f"<strong>{_esc(s['name'])}</strong> "
            f'<span class="muted">({_esc(s["slug"])} · {_esc(s["domain"])} · {s.get("metric_count", 0)} metrics)</span>'
            f"</summary>"
            f'<ul class="pwp-kpi-list">{cards}</ul>'
            f'<p><a href="/pwp/kpi/{_esc(s["slug"])}.html">Open detail page</a></p>'
            f"</details>"
        )
    return '<section class="pwp-kpi-accordions">' + "\n".join(blocks) + "</section>"
