"""cron_orchestrator — GAP-#7 unified cron execution for the KPI Hub.

GAP-#7 (cron orchestration): a single Prismatic Engine cron entry
(`prismatic.core_crons` → `python3 .../cron_orchestrator.py daily`) walks
every registered site in `config/seo_sites.json` and dispatches the
per-site launcher with the right env vars loaded from each site's
`share_targets` block.

Why this exists:

  Before:  `hd-platform-staging` had a per-site `cron_launcher.py` that
           only knew about HDE. Adding a new site meant wiring a new
           cron entry, hard-coding the site's env-var names in
           `registry.json`, and keeping two `share_targets` declarations
           in sync (one in `*.kpi.json`, one in `registry.json`).

  After:   The PWP plugin's `cron_orchestrator` walks the registry,
           reads each site's `<slug>.kpi.json` for its `share_targets`
           block, and dispatches per-site with the env vars loaded at
           run-time. Adding a new site is one cron entry, one registry
           entry, and the orchestrator handles the rest.

The orchestrator is **idempotent and safe to run multiple times per
day** — it's a no-op for sites whose cadence doesn't match the requested
kind. A daily cron run only fires `daily` cadence sites; a weekly cron
run only fires `weekly` cadence sites; etc.

Output paths:

  Each per-site run lands a manifest at
  `<publish_root>/kpi-runs/<kind>-<timestamp>/<slug>.json` so multiple
  sites' runs don't clobber each other. The orchestrator returns a
  top-level manifest with per-site status.

Dispatch:

  The orchestrator calls the existing `cron_launcher.py` in-tree by
  default (passed via `--launcher`). It sets the per-site env vars
  in the child's environment and lets the launcher do its normal
  work — emit JSON/HTML report, sync Google Sheet, send email.

  This keeps `cron_launcher.py` as the single per-site implementation;
  `cron_orchestrator.py` is the multi-site dispatcher.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .pwp_kpi_site_registry import (
    iter_sites,
    load_registry,
    site_override_enabled,
)
from . import publish_kpi_tracker as kpi


# `publish_root` and `launcher` accept either `Path` or `str` so the CLI
# can pass argparse's string args directly without explicit coercion.
PathLike = Union[Path, str]


DEFAULT_LAUNCHER = Path(
    os.environ.get("PWP_KPI_CRON_LAUNCHER", "")
) or None  # if unset, fall back to bundled hd-platform-staging shape.


def _resolve_launcher(launcher_override: Optional[Path] = None) -> Path:
    """Locate the per-site launcher to invoke.

    Resolution order:
      1. Explicit `launcher_override` (from CLI `--launcher` flag)
      2. `PWP_KPI_CRON_LAUNCHER` env var (re-checked at call time so
         test monkeypatching works)
      3. `HDE_KPI_REPO_ROOT` env var + `/scripts/kpis/operators/cron_launcher.py`

    The orchestrator never assumes a default location; the env var
    must be set, the launcher path passed explicitly, or
    HDE_KPI_REPO_ROOT must point at a repo with the launcher.
    """
    if launcher_override is not None:
        return launcher_override
    pwp_l = os.environ.get("PWP_KPI_CRON_LAUNCHER", "")
    if pwp_l:
        candidate = Path(pwp_l)
        if candidate.exists():
            return candidate
    hde_root = os.environ.get("HDE_KPI_REPO_ROOT")
    if hde_root:
        candidate = Path(hde_root) / "scripts" / "kpis" / "operators" / "cron_launcher.py"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "cron_launcher.py not found. Pass --launcher, or set "
        "PWP_KPI_CRON_LAUNCHER, or set HDE_KPI_REPO_ROOT so the "
        "orchestrator can find the per-site launcher."
    )


def _resolve_share_targets_env(slug: str, flat: dict) -> Dict[str, str]:
    """Read the site's `share_targets` block and resolve env vars.

    Each env-var name in `share_targets` (e.g. `AOT_KPI_SHEET_ID`,
    `HDE_GOOGLE_SERVICE_ACCOUNT_JSON`, `HDE_KPI_EMAIL_TO`) is looked
    up in `os.environ` and added to the dispatch env. If an env-var is
    not set, it's silently skipped (the launcher will fall back to its
    own defaults — e.g. `email_to_default: mbgulden@gmail.com`).
    """
    share_targets = flat.get("share_targets") or {}
    env: Dict[str, str] = {}
    for key, env_name in share_targets.items():
        if not isinstance(env_name, str):
            continue
        v = os.environ.get(env_name)
        if v is not None:
            env[env_name] = v
    return env


def _cadence_matches(flat: dict, kind: str) -> bool:
    """Return True if the site's `delivery_cadence` includes `kind`.

    A site can declare its cadence in three shapes:
      - `delivery_cadence: "daily"` (string)
      - `delivery_cadence: ["daily", "weekly"]` (list of kinds)
      - `delivery_cadence: {"daily": {...}, "weekly": {...}}` (dict keyed by kind)

    In all three cases, the presence of `kind` as a key/value means
    the site is dispatched for that cadence. If the field is missing,
    the site is dispatched for all cadences by default — this preserves
    backward compatibility with curated files that predate the
    cadence field.
    """
    cadence = flat.get("delivery_cadence")
    if not cadence:
        return True  # missing → opt in to all cadences
    if isinstance(cadence, str):
        return cadence == kind
    if isinstance(cadence, list):
        return kind in cadence
    if isinstance(cadence, dict):
        return kind in cadence
    return False


def dispatch_one_site(
    slug: str,
    *,
    kind: str,
    launcher: Path,
    env_overrides: Dict[str, str],
    publish_root: Path,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Invoke the per-site launcher for `slug` with the right env vars.

    Returns a status dict with `status: dispatched|failed|skipped`,
    `elapsed_seconds`, and the launcher's stdout on success.
    """
    cmd = [sys.executable, str(launcher), kind]
    # Inherit the parent env, then layer per-site overrides on top so
    # the launcher sees everything it normally sees PLUS the per-site
    # env vars loaded from share_targets.
    child_env = dict(os.environ)
    child_env.update(env_overrides)
    # Tag the output so the launcher knows which site is running.
    child_env["PWP_KPI_SLUG"] = slug
    child_env["PWP_KPI_KIND"] = kind
    started = dt.datetime.now(dt.timezone.utc)
    try:
        proc = subprocess.run(
            cmd,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        elapsed = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
        if proc.returncode == 0:
            return {
                "slug": slug,
                "status": "dispatched",
                "elapsed_seconds": round(elapsed, 2),
                "stdout_tail": proc.stdout[-500:],
            }
        return {
            "slug": slug,
            "status": "failed",
            "elapsed_seconds": round(elapsed, 2),
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"slug": slug, "status": "failed", "error": "timeout"}
    except Exception as exc:  # pragma: no cover (defensive)
        return {"slug": slug, "status": "failed", "error": repr(exc)}


def run(
    *,
    kind: str,
    registry_path: Optional[PathLike] = None,
    publish_root: Optional[PathLike] = None,
    launcher: Optional[PathLike] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Walk every registered site, dispatch the launcher per-site.

    Returns a manifest with `kind`, `publish_root`, `launcher`, and a
    `sites` list. Each site entry has `status`, `dispatched_at`,
    `cadence_matched`, and (on dispatch) the elapsed_seconds and
    stdout_tail from the launcher process.
    """
    if kind not in ("daily", "weekly", "monthly"):
        raise ValueError(f"unknown cron kind: {kind!r}; expected daily|weekly|monthly")

    registry = load_registry(Path(registry_path) if registry_path else None)
    if publish_root is None or publish_root == "":
        publish_root_path = Path("/tmp/pwp-kpi-runs") / kind
    else:
        publish_root_path = Path(publish_root)
    publish_root_path.mkdir(parents=True, exist_ok=True)
    publish_root = publish_root_path

    launcher_path = Path(launcher) if launcher else _resolve_launcher()

    manifest: Dict[str, Any] = {
        "kind": kind,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "publish_root": str(publish_root),
        "launcher": str(launcher_path),
        "sites": [],
    }

    for site in iter_sites(registry):
        slug = site["slug"]
        if not site_override_enabled(registry, site):
            manifest["sites"].append(
                {"slug": slug, "status": "skipped (disabled)", "cadence_matched": False}
            )
            continue
        try:
            flat = kpi.resolve_collection(slug)
        except (FileNotFoundError, ValueError) as exc:
            manifest["sites"].append(
                {"slug": slug, "status": "error", "error": f"resolve_collection: {exc}"}
            )
            continue
        if not _cadence_matches(flat, kind):
            manifest["sites"].append(
                {"slug": slug, "status": "skipped (cadence)", "cadence_matched": False}
            )
            continue
        env = _resolve_share_targets_env(slug, flat)
        result = dispatch_one_site(
            slug,
            kind=kind,
            launcher=launcher_path,
            env_overrides=env,
            publish_root=publish_root,
            timeout=timeout,
        )
        result["cadence_matched"] = True
        result["env_overrides"] = sorted(env.keys())
        manifest["sites"].append(result)

    manifest["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return manifest


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pwp-kpi-orchestrator",
        description=(
            "GAP-#7 unified cron orchestrator. Walks config/seo_sites.json "
            "and dispatches the per-site launcher for every site whose "
            "delivery_cadence matches the requested kind."
        ),
    )
    p.add_argument(
        "kind",
        choices=["daily", "weekly", "monthly"],
        help="The cron cadence to dispatch.",
    )
    p.add_argument(
        "--registry",
        help="Path to seo_sites.json (default inferred from PWP_REPO_ROOT).",
    )
    p.add_argument(
        "--publish-root",
        help="Directory the per-site runs land in (default /tmp/pwp-kpi-runs/<kind>).",
    )
    p.add_argument(
        "--launcher",
        help="Path to cron_launcher.py (default PWP_KPI_CRON_LAUNCHER or HDE_KPI_REPO_ROOT).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-site subprocess timeout in seconds (default 120).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = run(
            kind=args.kind,
            registry_path=Path(args.registry) if args.registry else None,
            publish_root=Path(args.publish_root) if args.publish_root else None,
            launcher=Path(args.launcher) if args.launcher else None,
            timeout=args.timeout,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    rc = 1 if any(s.get("status") == "failed" for s in manifest["sites"]) else 0
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
