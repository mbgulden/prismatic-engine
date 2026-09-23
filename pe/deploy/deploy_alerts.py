"""Alert-log emission for the post-merge deploy pipeline.

Mirrors the entry schema of ``AlertRouter._log_alert``
(``prismatic/gateway/alert_manager.py``) so deploy-pipeline events --
health-check failures, rollback start/complete/fail, deploy terminal
states -- land in the same alert log a sweep would check.

Stdlib only -- no prismatic imports -- so this module stays importable in
the same minimal environments as ``pe.deploy.gateway_redeploy``.

Additive only: emission never raises and never changes deploy, rollback,
phase, metric, or merge-authority behavior. Every failure is swallowed
into ``logging`` and reported as a boolean.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pe.deploy.config import alert_log_path

logger = logging.getLogger(__name__)

#: Severities shared with AlertRouter.
SEVERITIES = ("critical", "warning", "info")

#: Schema keys every entry carries -- identical to AlertRouter._log_alert.
ENTRY_KEYS = ("timestamp", "name", "severity", "summary", "details")


def default_alert_log_path() -> Path:
    """Resolve the alert-log path.

    Precedence mirrors ``AlertRouter``:
      1. ``$PRISMATIC_ALERT_LOG`` when set -- converging both writers.
      2. ``$PRISMATIC_STATE_DIR/alerts.log`` when set.
      3. ``~/.prismatic/alerts.log`` -- the deploy pipeline's home, where
         all its other state lives. Deliberately not the CWD-relative
         ``./prismatic_state/alerts.log`` default: the receiver runs under
         systemd and a CWD-relative path is not deterministic there.
    """
    return alert_log_path()


def build_alert_entry(
    name: str,
    severity: str,
    summary: str,
    details: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build one alert-log entry matching the AlertRouter schema."""
    sev = severity if severity in SEVERITIES else "info"
    if sev != severity:
        logger.warning("deploy-alerts: unknown severity %r coerced to 'info'", severity)
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "name": name,
        "severity": sev,
        "summary": summary,
        "details": details,
    }


def emit_deploy_alert(
    name: str,
    severity: str,
    summary: str,
    details: str = "",
    *,
    log_path: Path | str | None = None,
    timestamp: str | None = None,
) -> bool:
    """Append one JSONL entry to the alert log. Never raises.

    Returns True when the entry was written, False otherwise. ``log_path``
    and ``timestamp`` are injectable for tests.
    """
    entry = build_alert_entry(name, severity, summary, details, timestamp=timestamp)
    path = Path(log_path) if log_path is not None else default_alert_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.error("deploy-alerts: failed to write %s: %s", path, exc)
        return False
    return True
