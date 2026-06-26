"""
pwp.health — Capability health check for the PWP plugin.

Returns a list of ``HealthCheckResult`` dicts, one per declared
capability.  Each row tells the operator:

* what capability was checked
* whether it's present
* which env var / path is missing (when applicable)
* a short description the operator can read at a glance

The install command (``prismatic plugin install pwp``) and the plugin's
``on_init`` hook both call ``check()``.  The shape is JSON-serializable
so it can be returned from a tool call or written to the journal.

This module deliberately avoids importing the engine — it can run from
a vanilla Python REPL or a CI step.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List


def _resolve_workspace_path() -> str:
    """Where PWP scaffolds will be written. Falls back to ~/.prismatic/workspace/sites."""
    base = os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~/.prismatic")
    return os.path.join(base, "workspace", "sites")


def _resolve_okf_path() -> str:
    """Where the OKF lives. PRISMATIC_HOME is one directory up from the engine repo."""
    home = os.environ.get("PRISMATIC_HOME")
    if home:
        return os.path.normpath(os.path.join(home, "..", "growthwebdev-knowledge", "okf"))
    return os.path.normpath(
        os.path.join(os.path.expanduser("~"), "work", "growthwebdev-knowledge", "okf")
    )


# Capability table mirrors plugin-manifest.yaml so the runtime can detect
# drift between the declared contract and the actual environment.  When you
# add a row to the manifest, add it here too.
_REQUIRED: List[Dict[str, Any]] = [
    {
        "id": "cloudflare.api",
        "kind": "credential",
        "description": "Cloudflare API token with Pages/D1/R2/KV edit scope.",
        "env": ["CLOUDFLARE_GROWTHWEB_API_KEY", "CLOUDFLARE_PAGES_API_TOKEN"],
    },
    {
        "id": "cloudflare.account",
        "kind": "credential",
        "description": "Cloudflare account ID for Pages/D1/R2/KV operations.",
        "env": ["CLOUDFLARE_PAGES_ACCOUNT_ID"],
    },
    {
        "id": "filesystem.workspace",
        "kind": "resource",
        "description": "Local workspace directory for client-site scaffolds.",
        "path": _resolve_workspace_path(),
    },
    {
        "id": "okf.read",
        "kind": "resource",
        "description": "Read access to the OKF (operational knowledge fabric).",
        "path": _resolve_okf_path(),
    },
    {
        "id": "linear.api",
        "kind": "credential",
        "description": "Linear API key for issue comments and state transitions.",
        "env": ["LINEAR_API_KEY"],
    },
]

_OPTIONAL: List[Dict[str, Any]] = [
    {
        "id": "github.api",
        "kind": "credential",
        "description": "GitHub token for opening PRs against the client-site repo.",
        "env": ["GITHUB_TOKEN"],
    },
]


def _check_credential(row: Dict[str, Any]) -> Dict[str, Any]:
    """Verify all required env vars for a credential capability are non-empty."""
    env_vars = row.get("env", [])
    missing = [name for name in env_vars if not os.environ.get(name)]
    return {
        "id": row["id"],
        "kind": row["kind"],
        "description": row["description"],
        "status": "ok" if not missing else "fail",
        "missing": missing,
        "checked_env": env_vars,
    }


def _check_resource(row: Dict[str, Any]) -> Dict[str, Any]:
    """Verify a filesystem path capability exists (and is readable)."""
    path = row.get("path", "")
    missing: List[str] = []
    status = "ok"

    if not path:
        missing.append("path")
        status = "fail"
    else:
        p = Path(path)
        if not p.exists():
            missing.append(f"path:{path}")
            status = "fail"
        elif not os.access(str(p), os.R_OK):
            missing.append(f"readable:{path}")
            status = "fail"

    return {
        "id": row["id"],
        "kind": row["kind"],
        "description": row["description"],
        "status": status,
        "missing": missing,
        "checked_path": path,
    }


def check() -> List[Dict[str, Any]]:
    """Run all capability checks and return the result rows.

    Output is a list of dicts; the install / on_init code aggregates
    ``status == 'fail'`` rows into a single warning.
    """
    results: List[Dict[str, Any]] = []
    for row in _REQUIRED:
        if row["kind"] == "credential":
            results.append(_check_credential(row))
        elif row["kind"] == "resource":
            results.append(_check_resource(row))
        else:
            results.append({**row, "status": "unknown", "missing": ["unknown_kind"]})

    for row in _OPTIONAL:
        # Optional capabilities use status "skipped" when missing — not "fail".
        if row["kind"] == "credential":
            missing = [name for name in row.get("env", []) if not os.environ.get(name)]
            results.append(
                {
                    **row,
                    "status": "ok" if not missing else "skipped",
                    "missing": missing,
                    "checked_env": row.get("env", []),
                }
            )
        elif row["kind"] == "resource":
            results.append(_check_resource(row))
    return results


def summarize(rows: List[Dict[str, Any]] | None = None) -> Dict[str, int]:
    """Aggregate row counts by status. Used by CLI / install commands."""
    if rows is None:
        rows = check()
    out: Dict[str, int] = {"ok": 0, "fail": 0, "skipped": 0, "unknown": 0}
    for row in rows:
        status = row.get("status", "unknown")
        out[status] = out.get(status, 0) + 1
    return out


__all__ = ["check", "summarize"]