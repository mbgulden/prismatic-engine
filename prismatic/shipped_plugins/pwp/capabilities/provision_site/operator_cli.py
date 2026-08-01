"""operator_cli — Phase 1 provisioner CLI.

Usage:
  pwp-kpi-tracker provision --domain example.com --owner me@example.com
  pwp-kpi-tracker provision-status --domain example.com
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _publish_root_default_for() -> Path:
    """Resolve the PWP plugin root in a portable way.

    Defaults to PRISMATIC_REPO_ROOT if set, otherwise walks up from
    __file__ to find the plugin manifest.
    """
    env_root = os.environ.get("PRISMATIC_REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    # __file__ = .../prismatic/shipped_plugins/pwp/capabilities/provision_site/operator_cli.py
    return Path(__file__).resolve().parents[4]


def cmd_provision(args) -> int:
    """Run the provisioning flow for a domain."""
    # Lazy import — the orchestrator depends on `requests`.
    from plugins.pwp.capabilities.provision_site import orchestrator

    publish_root = Path(args.publish_root) if args.publish_root else None
    run = orchestrator.run(
        domain=args.domain,
        owner=args.owner,
        publish_root=publish_root,
        resume=args.resume,
        step_filter=args.step_filter.split(",") if args.step_filter else None,
    )
    print(json.dumps(run.to_dict(), indent=2, sort_keys=True))
    return 0 if run.overall_status == "complete" else 1


def cmd_provision_status(args) -> int:
    """Print the current status of a provisioning run."""
    from plugins.pwp.capabilities.provision_site import orchestrator

    publish_root = Path(args.publish_root) if args.publish_root else None
    state = orchestrator.status(args.domain, publish_root=publish_root)
    if state is None:
        print(json.dumps({"error": "no run found for this domain"}))
        return 2
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def cmd_provision_list(args) -> int:
    """List all known provisioning runs."""
    from plugins.pwp.capabilities.provision_site import orchestrator

    publish_root = Path(args.publish_root) if args.publish_root else None
    publish_root = publish_root or Path("/tmp/pwp-provisioning")
    if not publish_root.exists():
        print(json.dumps({"runs": []}))
        return 0
    runs: List[Dict[str, Any]] = []
    for f in sorted(publish_root.glob("*.json")):
        if f.name == "sites.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            runs.append({
                "domain": data.get("domain"),
                "owner": data.get("owner"),
                "overall_status": data.get("overall_status"),
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
            })
        except Exception:
            continue
    print(json.dumps({"runs": runs}, indent=2, sort_keys=True))
    return 0


def cmd_funnel_config(args) -> int:
    """Phase 4: dispatch a funnel-config form to Linear.

    Reads the JSON form from --from <path>, validates it, finds the
    PE-KPI-FUNNEL parent epic, dedupes by site_slug, and creates
    (or updates) a Linear task. The task ID + URL are written back
    into /tmp/pwp-provisioning/funnel-config/<site>.json and printed
    to stdout.
    """
    from plugins.pwp.capabilities.provision_site import funnel_config
    from plugins.pwp.capabilities.provision_site.linear_client import LinearClient

    form_path = Path(args.from_form)
    if not form_path.exists():
        print(json.dumps({"error": f"form file not found: {form_path}"}))
        return 2

    try:
        form = json.loads(form_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON: {exc}"}))
        return 2

    # Optional: embed current kpi-collections snapshot into the issue body
    site_context: Dict[str, Any] = {}
    sites_root = Path(
        args.sites_root
        or _publish_root_default_for() / "prismatic/shipped_plugins/pwp/capabilities/publish_kpi_tracker/sites"
    )
    slug = form.get("site_slug")
    if slug:
        kpi_path = sites_root / f"{slug}.kpi.json"
        if kpi_path.exists():
            try:
                site_context["kpi_collections"] = json.loads(
                    kpi_path.read_text(encoding="utf-8")
                )
            except Exception:
                pass

    try:
        client = LinearClient.from_env() if args.linear_client else None
    except Exception as exc:
        print(json.dumps({"error": f"LinearClient: {exc}"}))
        return 2

    try:
        result = funnel_config.dispatch(
            form,
            client=client,
            site_context=site_context or None,
            log_dir=Path(args.log_dir) if args.log_dir else None,
        )
    except funnel_config.FunnelConfigError as exc:
        print(json.dumps({"error": str(exc), "errors": exc.errors}))
        return 1
    except Exception as exc:  # network / unexpected
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 2

    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def cmd_funnel_status(args) -> int:
    """Phase 4: read the latest funnel-config submission log + Linear task
    status for a given site_slug."""
    from plugins.pwp.capabilities.provision_site import funnel_config
    from plugins.pwp.capabilities.provision_site.linear_client import LinearClient

    sub = funnel_config.FunnelConfigSubmission.load(args.slug)
    if sub is None:
        print(json.dumps({"error": f"no submission log for {args.slug}"}))
        return 2
    out = sub.to_dict()
    # Optionally augment with live Linear status
    if sub.linear_issue_id and not args.no_linear:
        try:
            client = LinearClient.from_env()
            issue = client.get_issue_status(sub.linear_issue_id)
            out["linear_status"] = issue.to_dict()
        except Exception as exc:
            out["linear_status_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def attach_subparser(sub) -> None:
    """Attach the provision* subcommands to an existing subparser."""
    sp = sub.add_parser(
        "provision",
        help=(
            "Phase 1: provision a new (or existing) website end-to-end. "
            "Verifies domain ownership via DNS TXT, creates/looks up the "
            "Cloudflare zone, registers in the sites.json appendix, and "
            "bootstraps the per-site <slug>.kpi.json. Re-run after "
            "creating the DNS TXT record to complete the flow."
        ),
    )
    sp.add_argument("--domain", required=True, help="Bare domain (e.g. example.com)")
    sp.add_argument(
        "--owner", required=True,
        help="Email of the person who owns the site (recorded in the registry)",
    )
    sp.add_argument(
        "--publish-root",
        help="Where provisioning state lives (default /tmp/pwp-provisioning)",
    )
    sp.add_argument(
        "--resume", action="store_true", default=True,
        help="Resume from a prior partial run (default: true)",
    )
    sp.add_argument(
        "--no-resume", dest="resume", action="store_false",
        help="Start fresh, ignoring any prior partial run",
    )
    sp.add_argument(
        "--step-filter",
        help="Comma-separated list of steps to run (e.g. verify_domain,migrate_kpi)",
    )
    sp.set_defaults(func=cmd_provision)

    ss = sub.add_parser(
        "provision-status",
        help="Print the current state of a provisioning run.",
    )
    ss.add_argument("--domain", required=True)
    ss.add_argument("--publish-root")
    ss.set_defaults(func=cmd_provision_status)

    sl = sub.add_parser(
        "provision-list",
        help="List all known provisioning runs.",
    )
    sl.add_argument("--publish-root")
    sl.set_defaults(func=cmd_provision_list)

    # Phase 4: funnel-config dispatcher (Configure website KPIs / Edit funnel)
    fc = sub.add_parser(
        "funnel-config",
        help=(
            "Phase 4: dispatch a funnel-config form to Linear. Reads the "
            "JSON form from --from-form, validates it, finds the "
            "PE-KPI-FUNNEL parent epic, and creates (or updates) a child "
            "Linear task. The task ID + URL are written to "
            "/tmp/pwp-provisioning/funnel-config/<site>.json."
        ),
    )
    fc.add_argument(
        "--from-form", required=True,
        help="Path to the JSON form payload (form_version=1).",
    )
    fc.add_argument(
        "--log-dir",
        help="Where to persist the submission log "
             "(default /tmp/pwp-provisioning/funnel-config).",
    )
    fc.add_argument(
        "--sites-root",
        help="Override the sites/ root for embedding kpi-collections "
             "snapshots in the issue body.",
    )
    fc.add_argument(
        "--linear-client", action="store_true",
        help="Force construction of a LinearClient from env (default: auto).",
    )
    fc.set_defaults(func=cmd_funnel_config)

    fs = sub.add_parser(
        "funnel-status",
        help=(
            "Phase 4: read the latest funnel-config submission log + "
            "live Linear task status for a given site_slug."
        ),
    )
    fs.add_argument("--slug", required=True, help="site slug (e.g. ezshare)")
    fs.add_argument(
        "--no-linear", action="store_true",
        help="Skip the live Linear API status fetch (offline-safe).",
    )
    fs.set_defaults(func=cmd_funnel_status)
