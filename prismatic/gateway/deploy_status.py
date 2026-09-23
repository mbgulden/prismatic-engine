"""Real deploy status for ``GET /api/deploy/status`` (Portal Phase 1, P0 #4).

Assembles read-only deploy state from three sources:

1. **Receiver health** — read-only probe of the deploy receiver on port 9460
   (``GET /health``, falling back to a TCP connect check). Never restarts,
   never modifies, never writes anything.
2. **Active release per repo** — reads the per-repo release symlinks under
   ``<state>/releases/<slug> -> <state>/versions/<version_dir>``.
   **READ-ONLY: never creates, updates, or deletes release links.**
3. **Last deploy per repo** — parses ``<state>/alerts.log`` JSON lines for
   ``PostMergeDeploySucceeded`` / ``PostMergeDeployFailed`` entries and keeps
   the newest entry attributed to each repo.

Everything in this module is stdlib-only and side-effect free (reads and
outbound localhost probes only) so it is safe to unit-test without the
gateway app.

Response schema (v1) — consumed by the Phase 2 Deploys UI::

    {
      "status": "active",            # kept for the deploy-status shadowing test
      "mode": "standalone",
      "has_deploys": True,          # kept: any deploy alert on record
      "latest_deploy_id": "deploy-69275273",  # kept: newest deploy id seen
      "receiver": {
        "reachable": True,          # False when port 9460 refuses/timeout
        "port": 9460,
        "http_status": 200,         # /health HTTP status when reachable
        "body": {"status": "ok", ...},  # parsed /health body, else None
        "latency_ms": 3.2,
        "error": None,              # human-readable probe error, else None
      },
      "repos": {
        "<slug>": {
          "repo": "mbgulden/prismatic-engine",  # full name when known
          "active_release": "prismatic-engine-69275273acfb",  # None if link missing/broken
          "release_target": "/home/ubuntu/.prismatic/versions/...",  # None if unreadable
          "release_target_exists": True,
          "last_deploy": {          # newest alerts.log entry for this repo, else None
            "name": "PostMergeDeploySucceeded",
            "success": True,
            "timestamp": "2026-09-23T03:12:44.474202+00:00",
            "summary": "deploy deploy-69275273 succeeded: ...",
            "deploy_id": "deploy-69275273",
            "pr_sha": "69275273acfb...",
            "version_dir": "prismatic-engine-69275273acfb",
          },
        },
      },
      "timestamp": 1758595664.37,
    }

``repos`` is keyed by the release-link slug (e.g. ``prismatic-engine``); the
``repo`` field carries the full ``owner/name`` when it can be resolved from
``deploy-repos.json``.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RECEIVER_PORT = 9460
PROBE_TIMEOUT_S = 2.0
_ALERT_NAMES = ("PostMergeDeploySucceeded", "PostMergeDeployFailed")

_VERSION_DIR_RE = re.compile(r"version_dir=([^\s,;]+)")
_DEPLOY_ID_RE = re.compile(r"deploy_id=([^\s,;]+)")
_PR_SHA_RE = re.compile(r"pr_sha=([^\s,;]+)")
_REPO_PATH_RE = re.compile(r"repos/([\w.\-]+)/([\w.\-]+)")


def state_base() -> Path:
    """State dir for deploy state; ``PRISMATIC_STATE_DIR`` overrides ``~/.prismatic``."""
    return Path(
        os.environ.get("PRISMATIC_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".prismatic")
    )


def receiver_port() -> int:
    """Deploy receiver port; ``PRISMATIC_DEPLOY_RECEIVER_PORT`` overrides 9460."""
    try:
        return int(os.environ.get("PRISMATIC_DEPLOY_RECEIVER_PORT", DEFAULT_RECEIVER_PORT))
    except (TypeError, ValueError):
        return DEFAULT_RECEIVER_PORT


def probe_receiver_health(port: int = DEFAULT_RECEIVER_PORT, timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    """Read-only receiver health probe.

    Tries ``GET http://127.0.0.1:<port>/health``; on HTTP-level failure falls
    back to a raw TCP connect. Never sends anything but the health GET.
    """
    started = time.monotonic()
    result: dict[str, Any] = {
        "reachable": False,
        "port": port,
        "http_status": None,
        "body": None,
        "latency_ms": None,
        "error": None,
    }
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — localhost probe
            raw = resp.read(65536)
            result["reachable"] = True
            result["http_status"] = resp.status
            try:
                result["body"] = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                result["body"] = None
    except urllib.error.HTTPError as exc:
        # Got an HTTP response (server alive) but non-2xx: still "reachable".
        result["reachable"] = True
        result["http_status"] = exc.code
        result["error"] = f"health endpoint returned HTTP {exc.code}"
    except Exception as exc:  # URLError, TimeoutError, ConnectionError, ...
        # TCP fallback: distinguish "port closed" from "HTTP layer broken".
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                result["reachable"] = True
                result["error"] = f"port open but /health failed: {exc}"
        except OSError as sock_exc:
            result["error"] = f"receiver unreachable: {sock_exc}"
    finally:
        result["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    return result


def read_active_releases(base: Path | None = None) -> dict[str, dict[str, Any]]:
    """Read per-repo release links under ``<base>/releases/``.

    Only symlinks are release links; plain version directories that share the
    ``releases/`` directory are skipped (they are not per-repo links).
    **READ-ONLY** — uses ``os.readlink`` only; never writes links.
    Returns ``{slug: {"active_release": <version_dir|None>, "release_target": <path|None>,
    "release_target_exists": bool}}``.
    """
    base = base or state_base()
    releases: dict[str, dict[str, Any]] = {}
    releases_dir = base / "releases"
    try:
        entries = sorted(releases_dir.iterdir())
    except OSError:
        return releases
    for entry in entries:
        if not entry.is_symlink():
            continue
        target: str | None = None
        version_dir: str | None = None
        try:
            raw_target = os.readlink(entry)
            target_path = Path(raw_target)
            if not target_path.is_absolute():
                target_path = releases_dir / target_path
            target = str(target_path)
            version_dir = target_path.name or None
        except OSError:
            target = None
        releases[entry.name] = {
            "active_release": version_dir,
            "release_target": target,
            "release_target_exists": bool(target) and Path(target).exists(),
        }
    return releases


def _parse_alert_line(line: str) -> dict[str, Any] | None:
    try:
        entry = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(entry, dict) or entry.get("name") not in _ALERT_NAMES:
        return None
    details = str(entry.get("details", "") or "")
    return {
        "name": entry.get("name"),
        "success": entry.get("name") == "PostMergeDeploySucceeded",
        "timestamp": entry.get("timestamp"),
        "summary": entry.get("summary"),
        "deploy_id": _first_group(_DEPLOY_ID_RE, details),
        "pr_sha": _first_group(_PR_SHA_RE, details),
        "version_dir": _first_group(_VERSION_DIR_RE, details),
        "details": details,
    }


def _first_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _attribute_repo(alert: dict[str, Any], slugs: list[str]) -> str | None:
    """Attribute an alerts.log entry to a repo slug.

    1. ``version_dir`` prefix match (``<slug>-<sha>`` or exact slug).
    2. ``repos/<owner>/<name>`` path in the entry details, tail-matched to a slug.
    """
    version_dir = alert.get("version_dir") or ""
    for slug in sorted(slugs, key=len, reverse=True):
        if version_dir == slug or version_dir.startswith(slug + "-"):
            return slug
    details = alert.get("details") or ""
    match = _REPO_PATH_RE.search(details)
    if match:
        name = match.group(2)
        if name in slugs:
            return name
    return None


def _alert_sort_key(alert: dict[str, Any]) -> float:
    ts = alert.get("timestamp")
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    return 0.0


def read_last_deploys(base: Path | None = None, slugs: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Newest ``PostMergeDeploy*`` alerts.log entry per repo slug.

    Returns ``{slug: alert}`` (alerts without the raw ``details`` field).
    """
    base = base or state_base()
    alerts_path = base / "alerts.log"
    alerts: list[dict[str, Any]] = []
    try:
        with alerts_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parsed = _parse_alert_line(line)
                if parsed is not None:
                    alerts.append(parsed)
    except OSError:
        return {}
    if slugs is None:
        slugs = sorted(read_active_releases(base).keys())
    latest: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        slug = _attribute_repo(alert, slugs)
        if slug is None:
            continue
        current = latest.get(slug)
        if current is None or _alert_sort_key(alert) >= _alert_sort_key(current):
            latest[slug] = alert
    result: dict[str, dict[str, Any]] = {}
    for slug, alert in latest.items():
        result[slug] = {key: value for key, value in alert.items() if key != "details"}
    return result


def _resolve_full_repo_names(base: Path, slugs: list[str]) -> dict[str, str]:
    """Map release slug -> full ``owner/name`` from deploy-repos.json (tail match)."""
    mapping: dict[str, str] = {}
    try:
        registry = json.loads((base / "deploy-repos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return mapping
    if not isinstance(registry, dict):
        return mapping
    for full_name in registry:
        tail = str(full_name).split("/")[-1]
        if tail in slugs and tail not in mapping:
            mapping[tail] = str(full_name)
    return mapping


def build_deploy_status(
    base: Path | None = None,
    port: int | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    """Assemble the ``GET /api/deploy/status`` v1 payload.

    ``probe=False`` skips the receiver probe (useful for tests and for callers
    that already know the receiver state).
    """
    base = base or state_base()
    port = receiver_port() if port is None else port
    releases = read_active_releases(base)
    slugs = sorted(releases.keys())
    last_deploys = read_last_deploys(base, slugs)
    full_names = _resolve_full_repo_names(base, slugs)

    repos: dict[str, dict[str, Any]] = {}
    for slug in slugs:
        info = releases[slug]
        repos[slug] = {
            "repo": full_names.get(slug, slug),
            "active_release": info["active_release"],
            "release_target": info["release_target"],
            "release_target_exists": info["release_target_exists"],
            "last_deploy": last_deploys.get(slug),
        }

    has_deploys = bool(last_deploys)
    latest_deploy_id = ""
    newest = 0.0
    for alert in last_deploys.values():
        key = _alert_sort_key(alert)
        if key >= newest and alert.get("deploy_id"):
            newest = key
            latest_deploy_id = str(alert["deploy_id"])

    payload: dict[str, Any] = {
        "status": "active",  # backward-compat: deploy-status shadowing test
        "mode": "standalone",
        "has_deploys": has_deploys,  # backward-compat
        "latest_deploy_id": latest_deploy_id,  # backward-compat
        "repos": repos,
        "timestamp": time.time(),
    }
    payload["receiver"] = probe_receiver_health(port) if probe else {
        "reachable": None,
        "port": port,
        "http_status": None,
        "body": None,
        "latency_ms": None,
        "error": "probe skipped",
    }
    return payload
