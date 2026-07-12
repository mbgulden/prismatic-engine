"""
prismatic/gateway/watchdog.py — Daily Gateway Health Watchdog

Verifies the health and daily restart status of the Prismatic Gateway server.
Designed to run shortly after the nightly restart window (e.g. at 06:15 UTC).
"""

from __future__ import annotations

import os
import sys
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, time as datetime_time, timezone, timedelta
from typing import Any

from prismatic.gateway.alert_manager import AlertRouter

logger = logging.getLogger("prismatic.gateway.watchdog")


def check_gateway_health(
    gateway_url: str | None = None,
    now: datetime | None = None,
    window_start_hour: int = 3,
    window_end_hour: int = 5,
    router: AlertRouter | None = None,
) -> dict[str, Any]:
    """
    Check the gateway status at /health.

    The live /health endpoint is the source of truth for gateway liveness.
    Service-manager status and heartbeat-file checks are diagnostics owned by
    the shell watchdog path; they must not override a successful live gateway
    response into a false-red health report.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if gateway_url is None:
        port = int(os.environ.get("PRISMATIC_PORT", 9000))
        gateway_url = f"http://127.0.0.1:{port}/health"

    if router is None:
        router = AlertRouter()

    logger.info("Watchdog checking gateway health at %s", gateway_url)

    # 1. Fetch /health
    try:
        req = urllib.request.Request(
            gateway_url, headers={"User-Agent": "Prismatic-Watchdog"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            status_code = resp.status
            body = resp.read().decode("utf-8")
    except Exception as exc:
        logger.error("Watchdog verification: gateway is down. Error: %s", exc)
        alert = {
            "name": "GatewayDown",
            "severity": "critical",
            "summary": "Gateway is down — connection refused or timeout",
            "details": f"gateway_url={gateway_url} error={str(exc)}",
        }
        router.route(alert)
        return {
            "status": "down",
            "source_of_truth": "live_gateway_health_endpoint",
            "error": str(exc),
            "alerts_fired": ["GatewayDown"],
        }

    if status_code != 200:
        logger.error("Watchdog verification: gateway returned HTTP %d", status_code)
        alert = {
            "name": "GatewayDown",
            "severity": "critical",
            "summary": f"Gateway is down — HTTP status {status_code}",
            "details": f"gateway_url={gateway_url} status_code={status_code}",
        }
        router.route(alert)
        return {
            "status": "down",
            "source_of_truth": "live_gateway_health_endpoint",
            "status_code": status_code,
            "alerts_fired": ["GatewayDown"],
        }

    # 2. Parse response
    try:
        data = json.loads(body)
    except Exception as exc:
        logger.error(
            "Watchdog verification: failed to parse gateway health JSON: %s", exc
        )
        alert = {
            "name": "GatewayDown",
            "severity": "critical",
            "summary": "Gateway returned malformed health JSON",
            "details": f"body={body[:200]} error={str(exc)}",
        }
        router.route(alert)
        return {
            "status": "down",
            "source_of_truth": "live_gateway_health_endpoint",
            "error": f"JSON parse error: {exc}",
            "alerts_fired": ["GatewayDown"],
        }

    started_at = data.get("started_at")
    if not started_at:
        logger.error(
            "Watchdog verification: gateway health JSON missing started_at timestamp"
        )
        alert = {
            "name": "GatewayDown",
            "severity": "critical",
            "summary": "Gateway health is missing started_at timestamp",
            "details": f"response={data}",
        }
        router.route(alert)
        return {
            "status": "down",
            "source_of_truth": "live_gateway_health_endpoint",
            "error": "missing started_at",
            "alerts_fired": ["GatewayDown"],
        }

    # Convert started_at to timezone-aware UTC datetime
    started_dt = datetime.fromtimestamp(started_at, tz=timezone.utc)

    # 3. Determine the expected nightly restart window of the current check cycle.
    # The cron job typically runs after the restart window on the current day.
    # If the current time is after window_end_hour, the expected window is today's.
    # Otherwise, it's yesterday's.
    today = now.date()
    today_window_end = datetime.combine(
        today, datetime_time(window_end_hour, 0), tzinfo=timezone.utc
    )

    if now >= today_window_end:
        window_start = datetime.combine(
            today, datetime_time(window_start_hour, 0), tzinfo=timezone.utc
        )
        window_end = today_window_end
    else:
        yesterday = today - timedelta(days=1)
        window_start = datetime.combine(
            yesterday, datetime_time(window_start_hour, 0), tzinfo=timezone.utc
        )
        window_end = datetime.combine(
            yesterday, datetime_time(window_end_hour, 0), tzinfo=timezone.utc
        )

    # 4. Verify start time against window
    if started_dt < window_start:
        logger.error(
            "Watchdog verification: gateway failed to restart during nightly window (last start: %s)",
            started_dt.isoformat(),
        )
        alert = {
            "name": "GatewayRestartFailure",
            "severity": "critical",
            "summary": "Gateway failed to restart during nightly window",
            "details": f"started_at={started_dt.isoformat()} expected_window={window_start.isoformat()} to {window_end.isoformat()}",
        }
        router.route(alert)
        return {
            "status": "failed_restart",
            "source_of_truth": "live_gateway_health_endpoint",
            "started_at": started_dt.isoformat(),
            "expected_window": (window_start.isoformat(), window_end.isoformat()),
            "alerts_fired": ["GatewayRestartFailure"],
        }
    elif started_dt > window_end:
        logger.error(
            "Watchdog verification: gateway restarted unexpectedly outside nightly window (last start: %s)",
            started_dt.isoformat(),
        )
        alert = {
            "name": "GatewayUnexpectedRestart",
            "severity": "critical",
            "summary": "Gateway restarted unexpectedly outside nightly window",
            "details": f"started_at={started_dt.isoformat()} expected_window={window_start.isoformat()} to {window_end.isoformat()}",
        }
        router.route(alert)
        return {
            "status": "unexpected_restart",
            "source_of_truth": "live_gateway_health_endpoint",
            "started_at": started_dt.isoformat(),
            "expected_window": (window_start.isoformat(), window_end.isoformat()),
            "alerts_fired": ["GatewayUnexpectedRestart"],
        }

    logger.info(
        "Watchdog verification: gateway is active and healthy (started at %s within nightly window)",
        started_dt.isoformat(),
    )
    return {
        "status": "ok",
        "source_of_truth": "live_gateway_health_endpoint",
        "started_at": started_dt.isoformat(),
        "expected_window": (window_start.isoformat(), window_end.isoformat()),
        "alerts_fired": [],
    }

def main() -> None:
    from prismatic.observability import init_logging

    init_logging(level=logging.INFO)
    res = check_gateway_health()
    if res["alerts_fired"]:
        print(f"Watchdog failed: {res['status']}. Alerts fired: {res['alerts_fired']}")
        sys.exit(1)
    else:
        print(f"Watchdog passed: gateway status is OK (started at {res['started_at']})")
        sys.exit(0)


if __name__ == "__main__":
    main()
