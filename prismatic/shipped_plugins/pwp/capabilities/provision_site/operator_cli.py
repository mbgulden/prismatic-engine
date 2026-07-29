"""operator_cli — Phase 1 provisioner CLI.

Usage:
  pwp-kpi-tracker provision --domain example.com --owner me@example.com
  pwp-kpi-tracker provision-status --domain example.com
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


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
