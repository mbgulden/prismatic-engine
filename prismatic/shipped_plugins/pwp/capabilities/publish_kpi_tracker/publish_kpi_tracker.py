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
    """Flatten parent metrics + site metrics, overriding on key collision.

    `front_of_card` is site-local: an inherited metric's `front_of_card` flag
    is *not* surfaced on the child unless the child re-declares the metric and
    explicitly sets it. This keeps the per-site "headline" cards meaningful
    instead of inheriting whatever the parent chose to highlight.
    """
    site = load_site(slug)
    parent_slug = site.get("extends") or (
        DEFAULT_PARENT_SLUG if slug != DEFAULT_PARENT_SLUG else None
    )
    parent = (
        load_site(parent_slug)
        if parent_slug and (SITES_DIR / f"{parent_slug}.kpi.json").exists()
        else None
    )
    site_metrics = dict(site.get("metrics", {}))
    if parent:
        # Parent metrics not overridden by the child are inherited but
        # *stripped of front_of_card* so the child's headline list is
        # always derived from metrics the child explicitly highlighted.
        merged = {}
        for mid, m in parent.get("metrics", {}).items():
            mm = dict(m)
            if mid not in site_metrics:
                mm.pop("front_of_card", None)
            merged[mid] = mm
        # Then layer the child's own metrics on top.
        merged.update(site_metrics)
        flat_metrics = merged
    else:
        flat_metrics = site_metrics
    flat = dict(site)
    flat["metrics"] = flat_metrics
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


def _format_value(value: Any, fmt: str = "number") -> str:
    """Format a runtime value for display.

    - None / missing  -> "—"  (placeholder, not silent collapse)
    - numbers         -> localized digits
    - percent         -> 2dp + "%"  (stored as a fraction: 0.0638 -> "6.38%")
    - currency        -> "$" + 2dp + locale grouping
    - duration        -> seconds, rounded

    Percent contract: the metric is stored as a fraction (0 ≤ value ≤ 1).
    `format: "percent"` multiplies by 100 and appends "%". So a metric
    with `formula: "purchase_total / page_view"` returning 0.0638
    renders as "6.38%" (NOT "0.06%"). This matches the canonical
    `active-oahu.kpi.json` formula `booking_complete / booking_click`,
    which produces a fraction in [0, 1] and is displayed as a percentage.

    The function is deterministic: same input -> same output.
    """
    if value is None:
        return "—"
    try:
        if fmt == "percent":
            # Stored as fraction. Multiply by 100 for display.
            return f"{float(value) * 100:.2f}%"
        if fmt == "currency":
            return f"${float(value):,.2f}"
        if fmt == "duration":
            return f"{int(round(float(value)))}s"
        # default: number
        if isinstance(value, float) and value.is_integer():
            return f"{int(value):,}"
        if isinstance(value, (int, float)):
            return f"{float(value):,.2f}".rstrip("0").rstrip(".")
        return str(value)
    except (TypeError, ValueError):
        return str(value)


def render_index(agg: dict) -> str:
    """Multi-site index page.

    Layout: one **per-site row** (`<section class="pwp-kpi-site-row">`) per
    registered site. Each row contains:

      - a header with the site name, slug, domain, owner, metric count,
        and a link to the per-site detail page
      - a card grid: one card per front-of-card metric, with the metric
        label and a placeholder value (`—`) when `runtime_values` is not
        provided for that metric

    This is the **smallest visual unit that closes the loop**: even when
    no runtime values are supplied (e.g., the dashboard is being rendered
    ahead of the cron), each site still renders its full grid of headline
    metrics with `—` placeholders, so the operator can SEE that the
    site is registered and which metrics are tracked.

    Self-rendering: this function takes only the aggregated data shape
    (produced by `aggregate()`) and produces deterministic HTML. The
    data shape + rendering function live in the same file
    (`publish_kpi_tracker.py`), so they cannot drift.
    """
    sections = []
    for s in agg.get("sites", []):
        cards_html = "".join(
            f'<div class="pwp-kpi-card">'
            f'<div class="pwp-kpi-card-label">{_esc(c.get("label") or c.get("metric_key") or "?")}</div>'
            f'<div class="pwp-kpi-card-value">{_esc(_format_value(c.get("value"), c.get("format", "number")))}</div>'
            + (
                f'<div class="pwp-kpi-card-delta">{_esc(c.get("delta_pct"))}</div>'
                if c.get("delta_pct")
                else ""
            )
            + "</div>"
            for c in s.get("front_of_card", [])
        )
        # Per-site row: header + card grid. Always rendered, even when no
        # runtime values are present (cards_html may be empty if the site
        # has no front_of_card metrics; we still want the row visible).
        no_cards_msg = '<p class="muted">No front-of-card metrics registered.</p>'
        sections.append(
            f'<section class="pwp-kpi-site-row" id="site-{_esc(s["slug"])}">'
            f'<header class="pwp-kpi-site-header">'
            f'<h3>{_esc(s["name"])} <span class="muted">({_esc(s["slug"])})</span></h3>'
            f'<p class="muted">'
            f'{_esc(s["domain"])} · {_esc(s.get("owner") or "—")} · '
            f'{s.get("metric_count", 0)} metrics'
            + (
                f' · extends {_esc(s["extends"])}'
                if s.get("extends")
                else ""
            )
            + '</p>'
            f'<p><a href="/pwp/kpi/{_esc(s["slug"])}.html">Open detail page →</a></p>'
            f'</header>'
            f'<div class="pwp-kpi-card-grid">'
            f'{cards_html or no_cards_msg}'
            f'</div>'
            f'</section>'
        )
    sections_html = "\n".join(sections)
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
    {sections_html}
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
            f'<span class="num">{_esc(_format_value(c.get("value"), c.get("format", "number")))}</span>'
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
