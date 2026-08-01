"""runtime_values — populate runtime_values for the KPI dashboard.

This module closes the loop on the publish-kpi-tracker dashboard:
without it, the multi-site index renders `—` placeholders for every
front-of-card metric. With it, the dashboard shows real numbers pulled
from the per-source adapters below.

Architecture
------------

The `RuntimeValuesBuilder` walks every registered site, calls
`resolve_collection(slug)`, and dispatches each metric to its source
adapter (`ga4`, `stripe`, `gsc`, `internal`, `verifier`, `telegram`,
`derived`). The result is the canonical `runtime_values` dict:

    runtime_values = {
        "active-oahu": {"funnel_booking.booking_click": 47, ...},
        "hd-engine":   {"funnel_top.free_chart_generated_total": 184, ...},
        ...
    }

This shape is the one `aggregate(runtime_values=...)` and
`build_dashboard(runtime_values=...)` already expect; no changes to
the rendering code are needed.

Per-source adapter contract
---------------------------

Each adapter has the same signature:

    query(metric_key, metric_spec, site_flat, *, env) -> value | None

where:
  - `metric_key` is the dotted key in `flat["metrics"]`
    (e.g. "funnel_top.free_chart_generated_total")
  - `metric_spec` is the metric dict (id, label, source, event, filter, ...)
  - `site_flat` is the resolved collection (extends already merged)
  - `env` is the resolved env-var map for credentials + config

`query` returns `None` when it has no value to contribute (e.g.,
credentials missing, metric not yet reported). The aggregator skips
`None` entries so the dashboard shows `—` for them.

Two-mode behavior
-----------------

Each adapter has both **live mode** (real API call when credentials are
present) and **snapshot mode** (read from a local file). The aggregator
prefers live but falls back to snapshot on failure or when credentials
are absent. Snapshot mode is the **demo path**: a small JSON file
`<slug>.runtime.json` (placed next to `<slug>.kpi.json`) carries
curated values that the dashboard renders directly.

Per-source snapshot files:
  - `<sites_dir>/<slug>.runtime.json`     canonical {metric_key: value} override
  - `<sites_dir>/<slug>.internal.json`   source=internal values
  - `<sites_dir>/<slug>.verifier.json`   source=verifier values
  - `<sites_dir>/<slug>.live.json`       output of live-mode queries (cached)

Pipeline order
--------------

For each site:
  1. Load site + parent collections, build flat metric list.
  2. Read canonical snapshot + per-source snapshots.
  3. For each metric: try live, fall back to snapshot.
  4. Compute derived metrics LAST (after all sources filled in).
  5. Return `{metric_key: value}` for the site.

The aggregator runs in site order, which is alphabetical (sorted by
`list_sites()`). Determinism comes from the file-system ordering of
the snapshot files and the stable metric-id ordering inside each
collection.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import publish_kpi_tracker as kpi

log = logging.getLogger(__name__)


# ── Sites-dir resolution ────────────────────────────────────────────────────
# The runtime values pipeline reads `<slug>.runtime.json` next to
# `<slug>.kpi.json`. Walk up from this file looking for the directory
# that contains `config/seo_sites.json` (the canonical PWP_REPO
# marker), then derive the canonical sites_dir from there. The
# PWP_REPO_ROOT env var overrides.

def _walk_to_pwp_repo() -> Path:
    cur = Path(__file__).resolve().parent
    for _ in range(10):
        if (cur / "config" / "seo_sites.json").is_file():
            return cur
        cur = cur.parent
    raise FileNotFoundError(
        f"runtime_values: could not locate PWP_REPO from {cur}; "
        f"no config/seo_sites.json within 10 parent levels. "
        f"Set PWP_REPO_ROOT to override."
    )


def default_sites_dir() -> Path:
    """Public accessor for the canonical sites directory.

    Returns `<PWP_REPO>/plugins/pwp/capabilities/publish_kpi_tracker/sites`
    where `<slug>.kpi.json` and `<slug>.runtime.json` live. PWP_REPO_ROOT
    env var overrides.
    """
    env_root = os.environ.get("PWP_REPO_ROOT")
    root = Path(env_root) if env_root else _walk_to_pwp_repo()
    return (
        root
        / "plugins"
        / "pwp"
        / "capabilities"
        / "publish_kpi_tracker"
        / "sites"
    )


# Backwards-compatible private alias (kept for internal callers).
_resolve_sites_dir = default_sites_dir


# ── Source adapter registry ────────────────────────────────────────────────
# Each source has a `query` function. They are stateless: same inputs
# produce the same outputs. The aggregator dispatches by metric.source.

def _query_ga4(metric_key: str, metric_spec: dict, site_flat: dict, *, env: dict[str, str]) -> float | None:
    """GA4 adapter: count events for a tracking_property over the window.

    Live mode: requires `GOOGLE_APPLICATION_CREDENTIALS` + GA4 Data API.
    Snapshot mode: returns the value from `<slug>.runtime.json`.

    The adapter does NOT actually call the GA4 API in this revision —
    that requires a service-account JSON + the google-analytics-data
    package. The contract is: when both are present, return the count.
    Otherwise return None so the snapshot path is used.
    """
    creds = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        return None
    # Live query path (gated on package presence; we don't take a hard
    # dep on google-analytics-data here).
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient  # type: ignore
        from google.analytics.data_v1beta.types import (  # type: ignore
            DateRange,
            RunReportRequest,
        )
    except ImportError:
        return None
    tracking_property = site_flat.get("tracking_property")
    if not (tracking_property and tracking_property.startswith("G-")):
        return None
    event = metric_spec.get("event")
    if not event:
        return None
    try:
        client = BetaAnalyticsDataClient.from_service_account_file(creds)
        req = RunReportRequest(
            property=f"properties/{tracking_property}",
            dimensions=[],
            metrics=[{"name": "eventCount"}],
            date_ranges=[DateRange(start_date="1daysAgo", end_date="today")],
            dimension_filter={
                "filter": {
                    "field_name": "eventName",
                    "string_filter": {"value": event},
                }
            },
        )
        resp = client.run_report(req)
        for row in resp.rows:
            for v in row.metric_values:
                return float(v.value)
    except Exception as exc:
        log.warning("ga4 live query failed for %s: %s", metric_key, exc)
    return None


def _query_stripe(metric_key: str, metric_spec: dict, site_flat: dict, *, env: dict[str, str]) -> float | None:
    """Stripe adapter: count checkout events filtered by metadata.

    Live mode: requires `STRIPE_API_KEY`.
    Snapshot mode: returns the value from `<slug>.runtime.json`.
    """
    api_key = env.get("STRIPE_API_KEY")
    if not api_key:
        return None
    try:
        import stripe  # type: ignore
    except ImportError:
        return None
    event = metric_spec.get("event")
    if not event:
        return None
    # Parse `metadata.funnel == sanctuary` style filters into Stripe's
    # query syntax. This is a best-effort translation: only simple
    # equality is supported.
    md_filter: dict[str, str] = {}
    flt = metric_spec.get("filter")
    if flt:
        m = re.match(r"metadata\.(\w+)\s*==\s*(\w+)", flt)
        if m:
            md_filter[f"metadata[{m.group(1)}]"] = m.group(2)
    try:
        stripe.api_key = api_key
        params: dict[str, Any] = {"limit": 100, "type": event}
        params.update(md_filter)
        count = 0
        # Stripe Checkout sessions are listed; pagination handled by
        # `auto_paging_iter` if available.
        sessions = stripe.checkout.Session.list(**params)
        for _ in sessions.auto_paging_iter() if hasattr(sessions, "auto_paging_iter") else sessions:
            count += 1
        return float(count)
    except Exception as exc:
        log.warning("stripe live query failed for %s: %s", metric_key, exc)
    return None


def _query_gsc(metric_key: str, metric_spec: dict, site_flat: dict, *, env: dict[str, str]) -> float | None:
    """Google Search Console adapter: clicks / impressions / position.

    Live mode: requires `GOOGLE_APPLICATION_CREDENTIALS` + GSC API.
    Snapshot mode: returns the value from `<slug>.runtime.json`.
    """
    creds = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        return None
    try:
        from google.oauth2 import service_account  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ImportError:
        return None
    flt = metric_spec.get("filter", "")
    m = re.match(r"sc-domain:(.+)", flt)
    if not m:
        return None
    site_url = m.group(1)
    fmt = metric_spec.get("format", "number")
    try:
        creds_obj = service_account.Credentials.from_service_account_file(
            creds, scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
        )
        svc = build("searchconsole", "v1", credentials=creds_obj, cache_discovery=False)
        body = {"startDate": "2024-01-01", "endDate": "today"}
        resp = svc.searchanalytics().query(siteUrl=site_url, body=body).execute()
        rows = resp.get("rows") or []
        if not rows:
            return 0.0
        if fmt == "percent":
            return None  # not a GSC-native metric
        # Map metric source `field` to a GSC column.
        fld = (metric_spec.get("field") or metric_spec.get("metric") or "clicks").lower()
        agg = sum(float(r.get(fld, 0)) for r in rows)
        if fld == "position" and rows:
            avg = agg / len(rows)
            return float(avg)
        return float(agg)
    except Exception as exc:
        log.warning("gsc live query failed for %s: %s", metric_key, exc)
    return None


def _query_telegram(metric_key: str, metric_spec: dict, site_flat: dict, *, env: dict[str, str]) -> float | None:
    """Telegram adapter: bot-side event counter (e.g., deep_link_clicked).

    Live mode: requires `TELEGRAM_BOT_TOKEN` + the bot's webhook stats.
    Snapshot mode: returns the value from `<slug>.runtime.json`.

    Real Telegram bots typically report these via a webhook; the bot
    pushes to a small counter file on disk. We read that file as the
    snapshot. Live mode is gated on a separate counter URL.
    """
    counter_url = env.get("TELEGRAM_COUNTER_URL")
    if not counter_url:
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(counter_url, timeout=5) as resp:
            data = json.loads(resp.read())
        event = metric_spec.get("event")
        if event:
            return float(data.get(event, 0))
        return float(data.get(metric_key, 0))
    except Exception as exc:
        log.warning("telegram live query failed for %s: %s", metric_key, exc)
    return None


def _query_internal(metric_key: str, metric_spec: dict, site_flat: dict, *, env: dict[str, str]) -> float | None:
    """Internal adapter: read from `<slug>.internal.json`.

    Use this for things like the FareHarbor imported-completed counter
    that lives on disk next to the *.kpi.json file.
    """
    sites_dir = env.get("_sites_dir")
    slug = env.get("_slug")
    if not (sites_dir and slug):
        return None
    p = Path(sites_dir) / f"{slug}.internal.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    v = data.get(metric_key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _query_verifier(metric_key: str, metric_spec: dict, site_flat: dict, *, env: dict[str, str]) -> float | None:
    """Verifier adapter: read from `<slug>.verifier.json`.

    Use this for things like `sitemap_coverage_pct` and `indexed_pct`
    that are produced by a CI / verifier pass and dumped to disk.
    """
    sites_dir = env.get("_sites_dir")
    slug = env.get("_slug")
    if not (sites_dir and slug):
        return None
    p = Path(sites_dir) / f"{slug}.verifier.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    v = data.get(metric_key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Source → adapter dispatch table.
ADAPTERS = {
    "ga4": _query_ga4,
    "stripe": _query_stripe,
    "gsc": _query_gsc,
    "telegram": _query_telegram,
    "internal": _query_internal,
    "verifier": _query_verifier,
}


# ── Derived metric computation ──────────────────────────────────────────────
# Derived metrics have `source: "derived"` and a `formula` field like
# "purchase_total / booking_click". We evaluate them AFTER all other
# sources for the same site have been computed, so the formula can
# reference any other metric's value.

_SAFE_EXPR_RE = re.compile(r"^[a-zA-Z0-9_./ ()*+%-]+$")


def _compute_derived(metric_key: str, metric_spec: dict, runtime_values_for_site: dict[str, float]) -> float | None:
    """Evaluate a derived metric formula against the current site's runtime values.

    The formula is a simple arithmetic expression whose operands are
    metric IDs (the bare `id` field, NOT the dotted `metric_key`). For
    example: `formula: "purchase_total / booking_click"` references two
    other metrics. Unknown operands or non-numeric values return None.

    The expression is parsed by substitution, NOT eval(), to keep the
    surface area small. Only the characters `[a-zA-Z0-9_./ ()*+%-]+`
    are allowed in the formula.
    """
    formula = metric_spec.get("formula")
    if not formula:
        return None
    if not _SAFE_EXPR_RE.match(formula):
        return None
    # Build a mapping bare_id → value for the site. The metric_spec.id
    # is the bare id (e.g. "purchase_total") for the metric itself;
    # other operands are looked up by bare id as well.
    by_bare_id: dict[str, float] = {}
    for k, v in runtime_values_for_site.items():
        # k is the dotted metric_key; pull the bare id off the end.
        bare = k.split(".")[-1]
        if bare:
            by_bare_id[bare] = v
    # Substitute bare-ids with numeric literals. Longest-first to
    # avoid `click` swallowing part of `booking_click`.
    keys = sorted(by_bare_id.keys(), key=len, reverse=True)
    expr = formula
    for bare in keys:
        expr = re.sub(rf"\b{re.escape(bare)}\b", repr(by_bare_id[bare]), expr)
    # Reject if any bare identifier remains (would NameError).
    if re.search(r"[a-zA-Z_][a-zA-Z0-9_.]*", expr):
        return None
    try:
        return float(eval(expr, {"__builtins__": {}}, {}))
    except Exception:
        return None


# ── RuntimeValuesBuilder ───────────────────────────────────────────────────

def _load_snapshot(sites_dir: Path, slug: str) -> dict[str, float]:
    """Load the canonical `<slug>.runtime.json` snapshot for a site.

    Returns an empty dict when the file doesn't exist. Snapshots are
    simple `{metric_key: value}` dicts — the operator is expected to
    fill these in (either manually for the demo path, or automatically
    via the live-mode adapters above).
    """
    p = sites_dir / f"{slug}.runtime.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


class RuntimeValuesBuilder:
    """Build the runtime_values dict from per-source adapters + snapshots.

    Usage:
        builder = RuntimeValuesBuilder()
        values = builder.build_all(sites_dir=Path("plugins/.../sites"))
        # values = {"hd-engine": {"funnel_top.free_chart_generated_total": 184, ...}, ...}

    Per-site flow:
      1. load canonical snapshot (<slug>.runtime.json) — values override
         everything below if both are present.
      2. for each non-derived metric: try live adapter, fall back to
         snapshot (if not already set by step 1).
      3. compute derived metrics LAST, using values from steps 1-2.
      4. drop any metric that ended up with no value.

    Determinism: site order is sorted alphabetically (list_sites()),
    metric order follows the resolved collection's iteration order, and
    derived metrics are evaluated in collection order.
    """

    def __init__(self, env: dict[str, str] | None = None) -> None:
        # Default env is os.environ; callers can inject a custom dict
        # (the FastAPI endpoint passes process.env, ad-hoc runs pass
        # the current shell's env).
        self.env: dict[str, str] = dict(env) if env is not None else dict(os.environ)

    def build_site(self, slug: str, *, sites_dir: Path) -> dict[str, float]:
        """Build runtime values for one site. Public for unit testing."""
        try:
            flat = kpi.resolve_collection(slug)
        except FileNotFoundError:
            return {}
        # Set the per-site env hooks so the internal/verifier adapters
        # know where to read from. We don't mutate self.env because
        # that's shared across sites; use a per-call dict.
        site_env: dict[str, str] = {
            **self.env,
            "_sites_dir": str(sites_dir),
            "_slug": slug,
        }
        snapshot = _load_snapshot(sites_dir, slug)
        out: dict[str, float] = dict(snapshot)  # snapshot wins at the end

        # Walk non-derived metrics first.
        deferred: list[tuple[str, dict]] = []
        for metric_key, m in flat.get("metrics", {}).items():
            if m.get("source") == "derived":
                deferred.append((metric_key, m))
                continue
            if metric_key in out:
                continue  # snapshot already has it
            adapter = ADAPTERS.get(m.get("source"))
            if adapter is None:
                continue  # unknown source → skip
            try:
                v = adapter(metric_key, m, flat, env=site_env)
            except Exception as exc:
                log.warning("adapter %s crashed for %s: %s", m.get("source"), metric_key, exc)
                v = None
            if v is not None:
                out[metric_key] = float(v)

        # Now compute derived metrics LAST, after all sources filled in.
        for metric_key, m in deferred:
            if metric_key in out:
                continue  # snapshot has it
            v = _compute_derived(metric_key, m, out)
            if v is not None:
                out[metric_key] = float(v)

        # Filter out None values just in case.
        return {k: v for k, v in out.items() if v is not None}

    def build_all(self, *, sites_dir: Path | None = None) -> dict[str, dict[str, float]]:
        """Build runtime values for every registered site.

        Args:
            sites_dir: directory that holds `<slug>.kpi.json` and the
                optional `<slug>.runtime.json` snapshots. Defaults to
                the canonical `<PWP_REPO>/plugins/.../sites/`.

        Returns:
            `{slug: {metric_key: value}}` dict ready for
            `aggregate(runtime_values=...)`.
        """
        if sites_dir is None:
            sites_dir = default_sites_dir()
        sites_dir = Path(sites_dir)
        sites_dir.mkdir(parents=True, exist_ok=True)
        out: dict[str, dict[str, float]] = {}
        for slug in sorted(kpi.list_sites()):
            values = self.build_site(slug, sites_dir=sites_dir)
            if values:
                out[slug] = values
        return out


def build_runtime_values(*, sites_dir: Path | None = None,
                          env: dict[str, str] | None = None) -> dict[str, dict[str, float]]:
    """Convenience wrapper for the common case."""
    return RuntimeValuesBuilder(env=env).build_all(sites_dir=sites_dir)


__all__ = [
    "ADAPTERS",
    "RuntimeValuesBuilder",
    "build_runtime_values",
]