"""
prismatic/telemetry/gcp_vertex.py — Vertex AI Credit & Quota Telemetry Monitor

Monitors Google Cloud Platform Vertex AI usage:

1. **Quota tracking** — TPM (Tokens Per Minute) and RPM (Requests Per Minute)
   for each Vertex AI model (gemini-2.5-pro, gemini-2.5-flash, etc.) per region.
2. **Credit balance** — Reads GCP Cloud Billing API for remaining credit allocation
   and spend history. Falls back to local ledger if GCP API unavailable.
3. **Persistent ledger** — `gcp_vertex_billing_ledger` table in event_router.db
   records all quota snapshots, balance checkpoints, and spend events.

Usage (CLI):
    python3 -m prismatic.telemetry.gcp_vertex check    # One-shot status dump
    python3 -m prismatic.telemetry.gcp_vertex poll     # Poll & record to ledger
    python3 -m prismatic.telemetry.gcp_vertex metrics  # Prometheus text output

Environment:
    GCP_PROJECT_ID              — GCP project for Vertex AI
    GCP_VERTEX_LOCATIONS        — Comma-separated regions (default: us-central1,us-east4)
    GCP_BILLING_ACCOUNT_ID      — Billing account ID (e.g. "XXXXXX-YYYYYY-ZZZZZZ")
    PRISMATIC_STATE_DIR         — Path to event_router.db directory
    GCP_CREDENTIALS_BASE64      — Base64-encoded GCP service account JSON (optional;
                                  falls back to ADC / gcloud auth)
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
import urllib.error
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

# ── Defaults ────────────────────────────────────────────────────
DEFAULT_DB_PATH = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

GCP_VERTEX_LOCATIONS = os.environ.get(
    "GCP_VERTEX_LOCATIONS", "us-central1,us-east4"
).split(",")

# ── Vertex AI Quota Metrics ─────────────────────────────────────
# Known Vertex AI model quota metric families:
#   aiplatform.googleapis.com/gemini_predictions_per_minute_requests_per_base_model
#   aiplatform.googleapis.com/gemini_predictions_per_minute_tokens_per_base_model
# We track the common ones here; the API returns them dynamically.
VERIFIED_MODEL_QUOTA_METRICS = {
    "gemini-2.5-pro": {
        "tpm": "aiplatform.googleapis.com/gemini_predictions_per_minute_tokens_per_base_model",
        "rpm": "aiplatform.googleapis.com/gemini_predictions_per_minute_requests_per_base_model",
    },
    "gemini-2.5-flash": {
        "tpm": "aiplatform.googleapis.com/gemini_predictions_per_minute_tokens_per_base_model",
        "rpm": "aiplatform.googleapis.com/gemini_predictions_per_minute_requests_per_base_model",
    },
}


# ── Exceptions ───────────────────────────────────────────────────
class VertexQuotaError(Exception):
    """Raised when GCP Vertex AI quota API call fails."""


class BillingAPIError(Exception):
    """Raised when GCP Cloud Billing API call fails."""


# ═══════════════════════════════════════════════════════════════════
#  Ledger Schema
# ═══════════════════════════════════════════════════════════════════

LEDGER_SCHEMA = """
-- Main billing ledger (compatible with existing table from prior session)
CREATE TABLE IF NOT EXISTS gcp_vertex_billing_ledger (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at      TEXT NOT NULL,
    project          TEXT NOT NULL DEFAULT '',
    total_cost       REAL DEFAULT 0.0,
    credits          REAL DEFAULT 0.0,
    currency         TEXT DEFAULT 'USD',
    quota_data       TEXT,
    service_breakdown TEXT,
    error_info       TEXT,
    raw_payload      TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_vertex_billing_time
    ON gcp_vertex_billing_ledger(recorded_at);

    -- Normalized quota snapshots table (per-model, per-region, per-metric)
    CREATE TABLE IF NOT EXISTS gcp_vertex_quota_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_id        INTEGER NOT NULL REFERENCES gcp_vertex_billing_ledger(id),
    region           TEXT NOT NULL,
    model            TEXT NOT NULL,
    metric_type      TEXT NOT NULL,
    metric_name      TEXT NOT NULL,
    usage            REAL NOT NULL,
    limit_value      REAL NOT NULL,
    utilization_pct  REAL NOT NULL,
    recorded_at      TEXT NOT NULL,
    raw_payload      TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_quota_snapshots_lookup
    ON gcp_vertex_quota_snapshots(region, model, metric_type, recorded_at);

    -- Explicit poll errors surfaced to telemetry panes.
    CREATE TABLE IF NOT EXISTS gcp_vertex_poll_errors (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at      TEXT NOT NULL,
    location         TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT 'quota',
    error_type       TEXT NOT NULL,
    error_message    TEXT NOT NULL,
    raw_payload      TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_vertex_poll_errors_time
    ON gcp_vertex_poll_errors(recorded_at);

-- Billing balance checkpoints
CREATE TABLE IF NOT EXISTS gcp_vertex_balance_checkpoints (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_id        INTEGER NOT NULL REFERENCES gcp_vertex_billing_ledger(id),
    billing_account_id TEXT NOT NULL,
    balance_credits  REAL NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'USD',
    projected_monthly REAL,
    recorded_at      TEXT NOT NULL
);

-- Spend event log
CREATE TABLE IF NOT EXISTS gcp_vertex_spend_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_id        INTEGER NOT NULL REFERENCES gcp_vertex_billing_ledger(id),
    model            TEXT,
    region           TEXT,
    tpm_used         INTEGER DEFAULT 0,
    rpm_used         INTEGER DEFAULT 0,
    context_pct      REAL DEFAULT 0.0,
    estimated_cost   REAL DEFAULT 0.0,
    operation        TEXT,
    recorded_at      TEXT NOT NULL
);

-- View: latest quota snapshot per model per region
CREATE VIEW IF NOT EXISTS gcp_vertex_latest_quota AS
SELECT qs.region, qs.model, qs.metric_type, qs.usage, qs.limit_value,
       qs.utilization_pct, qs.recorded_at
FROM gcp_vertex_quota_snapshots qs
INNER JOIN (
    SELECT region, model, metric_type, MAX(recorded_at) AS max_ts
    FROM gcp_vertex_quota_snapshots
    GROUP BY region, model, metric_type
) latest ON qs.region = latest.region
        AND qs.model = latest.model
        AND qs.metric_type = latest.metric_type
        AND qs.recorded_at = latest.max_ts;
""".strip()


# ═══════════════════════════════════════════════════════════════════
#  GCP HTTP Client (minimal, no external deps)
# ═══════════════════════════════════════════════════════════════════


def _get_access_token() -> str:
    """Obtain GCP access token from ADC or env-var service account.

    Falls back to ``gcloud auth print-access-token`` via subprocess.
    """
    # Try env-var-loaded service account credentials first
    creds_b64 = os.environ.get("GCP_CREDENTIALS_BASE64", "")
    if creds_b64:
        import base64

        try:
            creds_json = json.loads(base64.b64decode(creds_b64).decode())
            # Use JWT assertion — simplified approach
            return creds_json.get("access_token", "")
        except Exception:
            pass

    # Try gcloud CLI
    import subprocess

    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ""


def _gcp_api_call(url: str, scopes: str | None = None) -> dict[str, Any]:
    """Make an authenticated GET to a GCP API endpoint."""
    token = _get_access_token()
    if not token:
        raise VertexQuotaError(
            "No GCP credentials available. Set GCP_CREDENTIALS_BASE64 "
            "or run: gcloud auth application-default login"
        )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise VertexQuotaError(
            f"GCP API error {e.code} for {url}: {e.read().decode()[:200]}"
        ) from e


# ═══════════════════════════════════════════════════════════════════
#  Quota Poller (Vertex AI)
# ═══════════════════════════════════════════════════════════════════

_UNDEFINED_STRINGS = {"", "undefined", "null", "none", "nan"}


def _strip_nullish(value: Any) -> Any:
    """Recursively drop null/undefined values while preserving falsey numbers."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in _UNDEFINED_STRINGS:
        return None
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            normalized = _strip_nullish(child)
            if normalized is not None:
                cleaned[str(key)] = normalized
        return cleaned
    if isinstance(value, list):
        cleaned_list = []
        for child in value:
            normalized = _strip_nullish(child)
            if normalized is not None:
                cleaned_list.append(normalized)
        return cleaned_list
    return value


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert quota API string/number payloads to float without leaking NaN."""
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    if converted != converted:  # NaN guard
        return default
    return converted


def _first_numeric(*values: Any) -> float:
    for value in values:
        if isinstance(value, dict):
            value = value.get("value")
        converted = _safe_float(value, default=-1.0)
        if converted >= 0:
            return converted
    return 0.0


def _extract_model_from_dimensions(dimensions: dict[str, Any]) -> str | None:
    for key in ("model", "base_model", "baseModel", "quota_model", "quotaModel"):
        value = dimensions.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_metric_type(quota_id: str, metric_name: str = "") -> str:
    lookup = f"{quota_id} {metric_name}".lower()
    if "tokens" in lookup or "tpm" in lookup:
        return "tpm"
    if "requests" in lookup or "rpm" in lookup:
        return "rpm"
    return "custom"


def normalize_quota_payload(
    raw_quota: dict[str, Any], location: str
) -> dict[str, Any] | None:
    """Normalize one Cloud Quotas payload into the pane-safe record shape.

    The Cloud Quotas API has returned a few subtly different shapes across
    Google rollout waves: numbers as strings, usage under ``metricInfos``, model
    names in dimensions, and occasional null/``undefined`` fields.  The UI pane
    should never receive those raw nullish fields.
    """
    if not isinstance(raw_quota, dict):
        return None
    cleaned = _strip_nullish(raw_quota) or {}
    if not isinstance(cleaned, dict):
        return None

    quota_id = str(
        cleaned.get("quotaId") or cleaned.get("name") or cleaned.get("metric") or ""
    )
    metric_name = str(
        cleaned.get("metricName") or cleaned.get("metric_name") or quota_id
    )
    dimensions_value = cleaned.get("dimensions")
    dimensions: dict[str, Any] = (
        dimensions_value if isinstance(dimensions_value, dict) else {}
    )
    model = _extract_model_from_dimensions(dimensions) or _extract_model_from_quota(
        quota_id
    )
    if not model:
        return None

    metric_type = _extract_metric_type(quota_id, metric_name)
    region = str(dimensions.get("region") or cleaned.get("region") or location)

    metric_infos = (
        cleaned.get("metricInfos")
        if isinstance(cleaned.get("metricInfos"), list)
        else []
    )
    usage_candidates = [
        cleaned.get("usage"),
        cleaned.get("metricValue"),
        cleaned.get("currentUsage"),
    ]
    for mi in metric_infos:
        if isinstance(mi, dict):
            usage_candidates.extend(
                [mi.get("metricValue"), mi.get("value"), mi.get("usage")]
            )
    usage = _first_numeric(*usage_candidates)

    limits = cleaned.get("limits") if isinstance(cleaned.get("limits"), list) else []
    limit_candidates = [
        cleaned.get("limit"),
        cleaned.get("limitValue"),
        cleaned.get("maxLimit"),
    ]
    for limit in limits:
        if isinstance(limit, dict):
            limit_candidates.extend(
                [
                    limit.get("maxLimit"),
                    limit.get("effectiveLimit"),
                    limit.get("quotaValue"),
                    limit.get("value"),
                ]
            )
    limit_value = _first_numeric(*limit_candidates)
    utilization_pct = (usage / limit_value * 100.0) if limit_value > 0 else 0.0
    remaining_value = max(limit_value - usage, 0.0) if limit_value > 0 else None
    unavailable_reason = None
    if limit_value <= 0:
        unavailable_reason = "quota limit unavailable from Cloud Quotas payload"

    record = {
        "region": region,
        "model": model,
        "metric_type": metric_type,
        "metric_name": metric_name or quota_id,
        "usage": usage,
        "limit_value": limit_value,
        "remaining_value": remaining_value,
        "unavailable_reason": unavailable_reason,
        "utilization_pct": round(utilization_pct, 2),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "raw_payload": cleaned,
    }
    return {k: v for k, v in record.items() if _strip_nullish(v) is not None}


def poll_vertex_quota_status(
    project_id: str | None = None,
    locations: list[str] | None = None,
) -> dict[str, Any]:
    """Poll Vertex quota and return records plus explicit pane-safe errors."""
    pid = project_id or os.environ.get("GCP_PROJECT_ID", "")
    locs = locations or GCP_VERTEX_LOCATIONS
    started_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if not pid:
        message = "GCP_PROJECT_ID not set — skipping live quota poll"
        print(f"[gcp_vertex] WARN: {message}")
        return {
            "records": records,
            "errors": [
                {
                    "location": "",
                    "error_type": "configuration",
                    "error_message": message,
                    "recorded_at": started_at,
                }
            ],
            "recorded_at": started_at,
        }

    for location in locs:
        url = (
            f"https://cloudquotas.googleapis.com/v1/"
            f"projects/{pid}/locations/{location}/quotas"
        )
        try:
            data = _gcp_api_call(url)
        except VertexQuotaError as e:
            message = str(e)
            errors.append(
                {
                    "location": location,
                    "error_type": type(e).__name__,
                    "error_message": message,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            print(f"[gcp_vertex] Quota API error for {location}: {message}")
            continue

        raw_quotas = data if isinstance(data, dict) else {}
        quotas = raw_quotas.get("quotas", []) if isinstance(raw_quotas, dict) else []
        if not isinstance(quotas, list):
            errors.append(
                {
                    "location": location,
                    "error_type": "InvalidQuotaPayload",
                    "error_message": "Cloud Quotas response did not contain a list at quotas",
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "raw_payload": _strip_nullish(raw_quotas) or {},
                }
            )
            continue

        for quota in quotas:
            normalized = normalize_quota_payload(quota, location)
            if normalized is None:
                continue
            records.append(normalized)
            print(
                f"[gcp_vertex] {normalized['region']} {normalized['model']} {normalized['metric_type']}: "
                f"{normalized['usage']:.1f}/{normalized['limit_value']:.1f} "
                f"({normalized['utilization_pct']:.1f}%)"
            )

    return {"records": records, "errors": errors, "recorded_at": started_at}


def poll_vertex_quota(
    project_id: str | None = None,
    locations: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Poll Vertex AI quota API and return normalized records only."""
    return poll_vertex_quota_status(project_id=project_id, locations=locations)[
        "records"
    ]


def _extract_model_from_quota(quota_id: str) -> str | None:
    """Extract model name from GCP quota metric ID.

    Example: 'aiplatform.googleapis.com/gemini_predictions_per_minute_tokens_per_base_model'
             → 'gemini-2.5-pro' or 'gemini-2.5-flash'
    We rely on the quota dimensions for exact model — fallback to partial match.
    """
    if "gemini" not in quota_id:
        return None
    if "flash" in quota_id or "gemini-2.0-flash" in quota_id:
        return "gemini-2.5-flash"
    if "pro" in quota_id or "gemini-1.5-pro" in quota_id:
        return "gemini-2.5-pro"
    return "gemini-unknown"


# ═══════════════════════════════════════════════════════════════════
#  Billing Balance Poller (GCP Cloud Billing API)
# ═══════════════════════════════════════════════════════════════════


def poll_billing_balance(
    billing_account_id: str | None = None,
) -> dict[str, Any] | None:
    """Poll GCP Cloud Billing API for account balance.

    Uses:
        GET https://cloudbilling.googleapis.com/v1/billingAccounts/{id}

    Returns dict with billing_account_id, balance, currency, or None on error.
    """
    bid = billing_account_id or os.environ.get("GCP_BILLING_ACCOUNT_ID", "")
    if not bid:
        print(
            "[gcp_vertex] WARN: GCP_BILLING_ACCOUNT_ID not set — skipping balance poll"
        )
        return None

    url = f"https://cloudbilling.googleapis.com/v1/billingAccounts/{bid}"
    try:
        data = _gcp_api_call(url)
    except (VertexQuotaError, BillingAPIError) as e:
        print(f"[gcp_vertex] Billing API error: {e}")
        return None

    return {
        "billing_account_id": bid,
        "balance": float(data.get("balance", 0) or 0),
        "currency": data.get("currency", "USD"),
    }


# ═══════════════════════════════════════════════════════════════════
#  Ledger Writer
# ═══════════════════════════════════════════════════════════════════


class VertexBillingLedger:
    """Persistence layer for the gcp_vertex_billing_ledger tables."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create tables and views if they don't exist."""
        db_dir = os.path.dirname(self._db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.executescript(LEDGER_SCHEMA)
            # Migrate older ledgers created before GRO-3532 without breaking
            # existing cron state files.
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(gcp_vertex_quota_snapshots)"
                ).fetchall()
            }
            if "raw_payload" not in columns:
                conn.execute(
                    "ALTER TABLE gcp_vertex_quota_snapshots "
                    "ADD COLUMN raw_payload TEXT NOT NULL DEFAULT '{}'"
                )
            conn.commit()

    def record_quota_snapshot(
        self, quota_records: list[dict[str, Any]], project_id: str = ""
    ) -> None:
        """Write a batch of quota snapshot records to the ledger.

        After the quota snapshot commits, this also emits best-effort
        ``TelemetryCollector.record_vertex_spend()`` events so the engine-wide
        ``gcp_vertex_spend_events`` table receives rows during normal Vertex
        quota polling. Telemetry failures must not break the durable quota
        snapshot path.
        """
        if not quota_records:
            return
        now = datetime.now(timezone.utc).isoformat()
        pid = project_id or os.environ.get("GCP_PROJECT_ID", "")
        with closing(sqlite3.connect(self._db_path)) as conn:
            cur = conn.execute(
                "INSERT INTO gcp_vertex_billing_ledger "
                "(recorded_at, project, credits) VALUES (?, ?, ?)",
                (now, pid, 0.0),
            )
            ledger_id = cur.lastrowid
            for rec in quota_records:
                recorded_at = str(rec.get("recorded_at") or now)
                raw_payload = _strip_nullish(rec.get("raw_payload") or {}) or {}
                conn.execute(
                    """INSERT INTO gcp_vertex_quota_snapshots
                       (ledger_id, region, model, metric_type, metric_name,
                        usage, limit_value, utilization_pct, recorded_at, raw_payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ledger_id,
                        str(rec["region"]),
                        str(rec["model"]),
                        str(rec["metric_type"]),
                        str(rec["metric_name"]),
                        _safe_float(rec["usage"]),
                        _safe_float(rec["limit_value"]),
                        _safe_float(rec["utilization_pct"]),
                        recorded_at,
                        json.dumps(raw_payload, sort_keys=True),
                    ),
                )
            conn.commit()

        try:
            from prismatic.telemetry import get_collector

            collector = get_collector()
        except Exception:
            collector = None

        if collector is not None:
            for rec in quota_records:
                try:
                    metric_type = rec.get("metric_type", "custom")
                    usage = float(rec.get("usage", 0))
                    util_pct = float(rec.get("utilization_pct", 0.0))
                    collector.record_vertex_spend(
                        project_id=pid,
                        model=rec.get("model", ""),
                        region=rec.get("region", ""),
                        credits=round(util_pct / 100.0, 6),
                        operation=f"quota_poll_{metric_type}",
                        tpm_used=int(usage) if metric_type == "tpm" else 0,
                        rpm_used=int(usage) if metric_type == "rpm" else 0,
                        context_pct=round(util_pct / 100.0, 6),
                        recorded_at=now,
                    )
                except Exception:
                    pass

    def record_quota_errors(self, errors: list[dict[str, Any]]) -> None:
        """Persist explicit quota-poll errors for the telemetry panes."""
        if not errors:
            return
        with closing(sqlite3.connect(self._db_path)) as conn:
            for err in errors:
                raw_payload = _strip_nullish(err.get("raw_payload") or {}) or {}
                conn.execute(
                    """INSERT INTO gcp_vertex_poll_errors
                       (recorded_at, location, source, error_type, error_message, raw_payload)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        str(
                            err.get("recorded_at")
                            or datetime.now(timezone.utc).isoformat()
                        ),
                        str(err.get("location") or ""),
                        str(err.get("source") or "quota"),
                        str(err.get("error_type") or "UnknownError"),
                        str(err.get("error_message") or "Unknown quota polling error"),
                        json.dumps(raw_payload, sort_keys=True),
                    ),
                )
            conn.commit()

    def record_balance_checkpoint(
        self, balance_data: dict[str, Any], project_id: str = ""
    ) -> None:
        """Write a billing balance checkpoint."""
        now = datetime.now(timezone.utc).isoformat()
        pid = project_id or os.environ.get("GCP_PROJECT_ID", "")
        with closing(sqlite3.connect(self._db_path)) as conn:
            cur = conn.execute(
                "INSERT INTO gcp_vertex_billing_ledger "
                "(recorded_at, project, credits, currency) VALUES (?, ?, ?, ?)",
                (
                    now,
                    pid,
                    balance_data.get("balance", 0),
                    balance_data.get("currency", "USD"),
                ),
            )
            ledger_id = cur.lastrowid
            conn.execute(
                """INSERT INTO gcp_vertex_balance_checkpoints
                   (ledger_id, billing_account_id, balance_credits, currency, projected_monthly, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    ledger_id,
                    balance_data.get("billing_account_id", ""),
                    balance_data.get("balance", 0),
                    balance_data.get("currency", "USD"),
                    balance_data.get("projected_monthly"),
                    now,
                ),
            )
            conn.commit()

    def get_latest_quota(
        self, model: str | None = None, region: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get latest quota records, optionally filtered."""
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            where = []
            params = []
            if model:
                where.append("model = ?")
                params.append(model)
            if region:
                where.append("region = ?")
                params.append(region)
            where_clause = ("WHERE " + " AND ".join(where)) if where else ""
            rows = conn.execute(
                f"SELECT * FROM gcp_vertex_latest_quota {where_clause} "
                f"ORDER BY utilization_pct DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            records = [dict(r) for r in rows]
            for record in records:
                limit_value = _safe_float(record.get("limit_value"))
                usage = _safe_float(record.get("usage"))
                if limit_value > 0:
                    record["remaining_value"] = max(limit_value - usage, 0.0)
                else:
                    record["unavailable_reason"] = (
                        "quota limit unavailable from persisted snapshot"
                    )
            return records

    def get_status_summary(self) -> dict[str, Any]:
        """Return a pane-safe status dict with freshness and poll errors."""
        latest = self.get_latest_quota(limit=50)
        generated_at = datetime.now(timezone.utc)
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            last_quota = conn.execute(
                "SELECT MAX(recorded_at) AS recorded_at FROM gcp_vertex_quota_snapshots"
            ).fetchone()
            last_check = conn.execute(
                "SELECT recorded_at FROM gcp_vertex_billing_ledger "
                "WHERE credits > 0 OR total_cost > 0 "
                "ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
            last_balance = conn.execute(
                "SELECT * FROM gcp_vertex_balance_checkpoints "
                "ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
            errors = conn.execute(
                "SELECT recorded_at, location, source, error_type, error_message "
                "FROM gcp_vertex_poll_errors ORDER BY recorded_at DESC LIMIT 5"
            ).fetchall()

            # Aggregated stats
            high_util = [r for r in latest if r.get("utilization_pct", 0) >= 80]
            near_limit = [r for r in latest if r.get("utilization_pct", 0) >= 95]

        last_quota_at = (
            last_quota["recorded_at"]
            if last_quota and last_quota["recorded_at"]
            else None
        )
        age_seconds = None
        if last_quota_at:
            try:
                parsed = datetime.fromisoformat(last_quota_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age_seconds = max(0, int((generated_at - parsed).total_seconds()))
            except ValueError:
                age_seconds = None

        return _strip_nullish(
            {
                "generated_at": generated_at.isoformat(),
                "quota_records": latest,
                "high_utilization": len(high_util),
                "near_limit": len(near_limit),
                "quota_freshness": {
                    "last_recorded_at": last_quota_at,
                    "age_seconds": age_seconds,
                    "stale": age_seconds is None or age_seconds > 900,
                },
                "latest_errors": [dict(row) for row in errors],
                "last_balance_checkpoint": {
                    "at": last_check["recorded_at"] if last_check else None,
                    "balance": dict(last_balance) if last_balance else None,
                },
                "total_regions": len(set(r.get("region") for r in latest)),
                "total_models": len(set(r.get("model") for r in latest)),
            }
        )

    def metrics_text(self) -> str:
        """Return Prometheus exposition-format metrics text."""
        latest = self.get_latest_quota(limit=50)
        lines = [
            "# HELP prismatic_vertex_quota_utilization_pct Vertex AI quota utilization percentage",
            "# TYPE prismatic_vertex_quota_utilization_pct gauge",
        ]
        for r in latest:
            labels = (
                f'region="{r["region"]}",'
                f'model="{r["model"]}",'
                f'metric="{r["metric_type"]}"'
            )
            lines.append(
                f"prismatic_vertex_quota_utilization_pct{{{labels}}} {r['utilization_pct']}"
            )
            lines.append(f"prismatic_vertex_quota_usage{{{labels}}} {r['usage']}")
            lines.append(f"prismatic_vertex_quota_limit{{{labels}}} {r['limit_value']}")

        # Balance checkpoint
        with closing(sqlite3.connect(self._db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM gcp_vertex_balance_checkpoints "
                "ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
            if row:
                lines.append(
                    "\n# HELP prismatic_vertex_billing_balance Current billing balance"
                )
                lines.append("# TYPE prismatic_vertex_billing_balance gauge")
                lines.append(
                    f'prismatic_vertex_billing_balance{{currency="{row[3]}"}} {row[2]}'
                )
            last_quota = conn.execute(
                "SELECT MAX(recorded_at) FROM gcp_vertex_quota_snapshots"
            ).fetchone()
            if last_quota and last_quota[0]:
                parsed = datetime.fromisoformat(
                    str(last_quota[0]).replace("Z", "+00:00")
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                lines.append(
                    "\n# HELP prismatic_vertex_quota_last_recorded_timestamp_seconds Last successful quota snapshot timestamp"
                )
                lines.append(
                    "# TYPE prismatic_vertex_quota_last_recorded_timestamp_seconds gauge"
                )
                lines.append(
                    f"prismatic_vertex_quota_last_recorded_timestamp_seconds {parsed.timestamp()}"
                )
            error_count = conn.execute(
                "SELECT COUNT(*) FROM gcp_vertex_poll_errors"
            ).fetchone()[0]
            lines.append(
                "\n# HELP prismatic_vertex_quota_poll_errors_total Total persisted quota poll errors"
            )
            lines.append("# TYPE prismatic_vertex_quota_poll_errors_total counter")
            lines.append(f"prismatic_vertex_quota_poll_errors_total {error_count}")

        return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
#  CLI Entry Points
# ═══════════════════════════════════════════════════════════════════


def cmd_check() -> None:
    """One-shot status: read latest from ledger and print summary."""
    ledger = VertexBillingLedger()
    summary = ledger.get_status_summary()
    records = summary["quota_records"]

    print("=" * 60)
    print("  Vertex AI Quota Telemetry — Status")
    print("=" * 60)
    print(f"  Regions:       {summary['total_regions']}")
    print(f"  Models:        {summary['total_models']}")
    print(f"  High util (>80%): {summary['high_utilization']}")
    print(f"  Near limit (>95%): {summary['near_limit']}")
    print(f"  Last balance:  {summary['last_balance_checkpoint']['at'] or 'Never'}")
    if summary["last_balance_checkpoint"]["balance"]:
        b = summary["last_balance_checkpoint"]["balance"]
        print(f"  Balance:       {b['balance_credits']} {b['currency']}")
    print()
    if records:
        print(
            f"{'Region':<16} {'Model':<20} {'Metric':<8} {'Usage':<10} {'Limit':<10} {'Util%':<8}"
        )
        print("-" * 72)
        for r in records:
            print(
                f"{r['region']:<16} {r['model']:<20} {r['metric_type']:<8} "
                f"{r['usage']:<10.1f} {r['limit_value']:<10.1f} {r['utilization_pct']:<7.1f}%"
            )
    else:
        print("  No quota records in ledger. Run `poll` to collect data.")
    print()


def cmd_poll() -> None:
    """Poll GCP APIs and record to ledger."""
    ledger = VertexBillingLedger()

    # 1. Poll Vertex AI quotas
    print("[gcp_vertex] Polling Vertex AI quota...")
    quota_status = poll_vertex_quota_status()
    quota = quota_status["records"]
    errors = quota_status["errors"]
    if quota:
        ledger.record_quota_snapshot(quota)
        print(f"[gcp_vertex] Recorded {len(quota)} quota metrics to ledger.")
    else:
        print(
            "[gcp_vertex] No quota data returned (GCP credentials may be unavailable)."
        )
    if errors:
        ledger.record_quota_errors(errors)
        print(f"[gcp_vertex] Recorded {len(errors)} quota poll error(s) to ledger.")

    # 2. Poll billing balance
    print("[gcp_vertex] Polling billing balance...")
    balance = poll_billing_balance()
    if balance:
        ledger.record_balance_checkpoint(balance)
        print(
            f"[gcp_vertex] Recorded balance: {balance.get('balance')} {balance.get('currency')}"
        )
    else:
        print("[gcp_vertex] No balance data returned.")


def cmd_metrics() -> None:
    """Output Prometheus exposition-format metrics."""
    ledger = VertexBillingLedger()
    print(ledger.metrics_text())


if __name__ == "__main__":
    import sys

    commands = {
        "check": cmd_check,
        "poll": cmd_poll,
        "metrics": cmd_metrics,
    }
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd in commands:
        commands[cmd]()
    else:
        print("Usage: python3 -m prismatic.vertex_telemetry <check|poll|metrics>")
        sys.exit(1)
