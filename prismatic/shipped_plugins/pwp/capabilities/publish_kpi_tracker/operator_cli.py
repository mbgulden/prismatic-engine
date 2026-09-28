#!/usr/bin/env python3
"""pwp.publish_kpi_tracker operator CLI.

Three subcommands:

  pwp-kpi-tracker build-dashboard
    Build the multi-site KPI dashboard. Reads registered *.kpi.json files
    under the capability's sites/ dir, applies optional runtime values,
    writes HTML pages + dashboard_data.json to the publish root.

  pwp-kpi-tracker migrate
    Derive per-site *.kpi.json files from the registry
    (config/seo_sites.json) and the registry's default_metric_specs +
    per-site pwp_kpi_metric_specs.

  pwp-kpi-tracker list-sites
    List all registered sites with their metric counts.

  pwp-kpi-tracker show <slug>
    Print the resolved (parent + child) collection for one site.

  pwp-kpi-tracker validate
    Run the canonical validator over every registered *.kpi.json file.

Used by:
  - Prismatic Engine cron via `python3 scripts/seo/pwp-kpi-tracker.py ...`
  - Local ad-hoc operator runs

Run with --help for per-command options.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PWP_REPO_OVERRIDE = os.environ.get("PWP_REPO_ROOT")

if PWP_REPO_OVERRIDE:
    REPO_ROOT = Path(PWP_REPO_OVERRIDE)
else:
    REPO_ROOT = None
    for p in [HERE] + list(HERE.parents):
        if (p / "prismatic").is_dir() or (p / "config" / "seo_sites.json").is_file():
            REPO_ROOT = p
            break
    if REPO_ROOT is None:
        REPO_ROOT = HERE.parents[4]

if str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))

SHIPPED_PLUGINS = REPO_ROOT / "prismatic" / "shipped_plugins"
if SHIPPED_PLUGINS.is_dir():
    if str(SHIPPED_PLUGINS) in sys.path:
        sys.path.remove(str(SHIPPED_PLUGINS))
    sys.path.insert(1, str(SHIPPED_PLUGINS))

try:
    from prismatic.shipped_plugins.pwp.capabilities import publish_kpi_tracker as kpi  # noqa: E402
except ImportError:
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker as kpi  # noqa: E402


def _resolve_publish_root(args) -> Path:
    if args.publish_root:
        return Path(args.publish_root)
    return Path("/tmp/pwp-kpi-dashboard")


def cmd_build_dashboard(args) -> int:
    """Build the dashboard. When --runtime-values-path is not given, the
    runtime values pipeline (runtime_values.build_runtime_values) is
    invoked automatically so the dashboard shows real values from
    per-site snapshots + live-mode adapters."""
    runtime = None  # default; build_dashboard will trigger the pipeline
    if args.runtime_values_path:
        runtime = kpi.read_runtime_values(args.runtime_values_path)
    manifest = kpi.build_dashboard(
        publish_root=str(_resolve_publish_root(args)),
        runtime_values=runtime,
        window=args.window,
        write_snapshot=True,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def cmd_list_sites(args) -> int:
    """List registered sites with their headline metrics. By default the
    runtime values pipeline is invoked so the headline_value field
    reflects current values from <slug>.runtime.json + live adapters.
    Pass --no-runtime-values to skip the pipeline (raw headline=None)."""
    runtime = None  # default; pipeline runs
    if getattr(args, "no_runtime_values", False):
        runtime = {}
    elif args.runtime_values_path:
        runtime = kpi.read_runtime_values(args.runtime_values_path)
    if runtime is None:
        # Run the pipeline ourselves so headline_value is populated.
        try:
            from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import runtime_values as rv
        except ImportError:
            from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import runtime_values as rv
        runtime = rv.build_runtime_values()
    summaries = kpi.build_all_site_summaries(runtime_values=runtime)
    print(json.dumps(summaries, indent=2, sort_keys=True))
    return 0


def cmd_show(args) -> int:
    try:
        flat = kpi.resolve_collection(args.slug)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # Drop the _parent_slug marker for a cleaner public dump.
    flat.pop("_parent_slug", None)
    flat.pop("_runtime_overrides", None)
    print(json.dumps(flat, indent=2, sort_keys=True))
    return 0


def cmd_validate(args) -> int:
    errs_total = 0
    for slug in kpi.list_sites():
        try:
            coll = kpi.load_site(slug)
        except FileNotFoundError as exc:
            print(f"  {slug}: missing - {exc}")
            errs_total += 1
            continue
        errs = kpi.validate(coll)
        if errs:
            errs_total += len(errs)
            for e in errs:
                print(f"  {slug}: {e}")
        else:
            print(f"  {slug}: ok")
    return 0 if errs_total == 0 else 1


def cmd_migrate(args) -> int:
    """Derive per-site *.kpi.json files from the registry."""
    try:
        from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import operator_migrate as migrate
    except ImportError:
        from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import operator_migrate as migrate
    rc = 1
    try:
        manifest = migrate.run(
            dry_run=args.dry_run,
            registry_path=Path(args.registry) if args.registry else None,
            sites_dir=Path(args.sites_dir) if args.sites_dir else None,
            force=args.force,
            merge=args.merge,
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        rc = 1 if manifest.get("validation_errors") else 0
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return rc


def cmd_snapshot(args) -> int:
    """Bootstrap a `<slug>.runtime.json` template for each registered site.

    The template contains every metric_key in the resolved collection with
    `null` placeholders, so the operator can fill in values manually (or
    have a live-mode adapter fill them). Existing files are NOT
    overwritten unless --force is passed.
    """
    try:
        from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import runtime_values as rv
    except ImportError:
        from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import runtime_values as rv
    sites_dir = Path(args.sites_dir) if args.sites_dir else rv.default_sites_dir()
    sites_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"sites": [], "sites_dir": str(sites_dir), "force": args.force}
    for slug in sorted(kpi.list_sites()):
        try:
            flat = kpi.resolve_collection(slug)
        except (FileNotFoundError, ValueError) as exc:
            manifest["sites"].append({"slug": slug, "status": "error", "error": str(exc)})
            continue
        target = sites_dir / f"{slug}.runtime.json"
        if target.exists() and not args.force:
            manifest["sites"].append({"slug": slug, "status": "skipped (exists)"})
            continue
        template = {key: None for key in flat.get("metrics", {}).keys()}
        target.write_text(json.dumps(template, indent=2, sort_keys=True), encoding="utf-8")
        manifest["sites"].append({
            "slug": slug,
            "status": "written",
            "path": str(target),
            "metric_count": len(template),
        })
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def cmd_cron(args) -> int:
    """GAP-#7: dispatch the unified cron orchestrator for one cadence.

    Walks every registered site in the registry and invokes the
    per-site launcher (cron_launcher.py) with each site's share-targets
    env vars loaded. Sites whose `delivery_cadence` doesn't match the
    requested kind are skipped — this lets a single Prismatic Engine
    cron entry drive daily/weekly/monthly runs without per-site wiring.
    """
    from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import cron_orchestrator as orch
    try:
        manifest = orch.run(
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
    return 1 if any(s.get("status") == "failed" for s in manifest["sites"]) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pwp-kpi-tracker",
        description="Operator CLI for the PWP publish-kpi-tracker capability.",
    )
    p.add_argument(
        "--publish-root",
        help="Directory the dashboard pages land in (default /tmp/pwp-kpi-dashboard)",
    )
    p.add_argument(
        "--runtime-values-path",
        help="JSON file with {slug: {metric_key: value}} overrides.",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sb = sub.add_parser("build-dashboard", help="Render the dashboard to a publish root.")
    sb.add_argument("--window", default="last24h",
                    help="Window label written into the dashboard header (default last24h).")
    sb.set_defaults(func=cmd_build_dashboard)

    sl = sub.add_parser("list-sites", help="List registered sites with their headline metrics.")
    sl.add_argument("--no-runtime-values", action="store_true",
                    help="Skip the runtime values pipeline (headline_value=None).")
    sl.set_defaults(func=cmd_list_sites)

    sh = sub.add_parser("show", help="Show the resolved collection for one site.")
    sh.add_argument("slug")
    sh.set_defaults(func=cmd_show)

    sv = sub.add_parser("validate", help="Validate every registered *.kpi.json.")
    sv.set_defaults(func=cmd_validate)

    sm = sub.add_parser(
        "migrate",
        help="Derive per-site *.kpi.json files from the registry (config/seo_sites.json).",
    )
    sm.add_argument("--registry",
                    help="Path to seo_sites.json (default inferred from PWP_REPO_ROOT).")
    sm.add_argument("--sites-dir",
                    help="Output directory for *.kpi.json (default: plugins/pwp/.../sites/).")
    sm.add_argument("--dry-run", action="store_true",
                    help="Don't write files; print the manifest instead.")
    sm.add_argument("--force", action="store_true",
                    help="Overwrite existing per-site *.kpi.json files. Default is to skip sites whose file already exists.")
    sm.add_argument("--merge", action="store_true",
                    help="Merge registry-derived metrics into existing per-site files. Curated entries ALWAYS win (registry never overwrites curated values). New events in the registry that aren't already in the curated file are added.")
    sm.set_defaults(func=cmd_migrate)

    ss = sub.add_parser(
        "snapshot",
        help="Bootstrap a <slug>.runtime.json template for each registered site.",
    )
    ss.add_argument("--sites-dir",
                    help="Output directory for *.runtime.json (default: plugins/pwp/.../sites/).")
    ss.add_argument("--force", action="store_true",
                    help="Overwrite existing <slug>.runtime.json files. Default is to skip sites whose file already exists.")
    ss.set_defaults(func=cmd_snapshot)

    sc = sub.add_parser(
        "cron",
        help=(
            "GAP-#7: Unified cron orchestrator. Walks config/seo_sites.json and "
            "dispatches the per-site launcher (cron_launcher.py) for every site "
            "whose delivery_cadence matches the requested kind (daily/weekly/monthly). "
            "Per-site share-targets env vars are loaded from each <slug>.kpi.json."
        ),
    )
    sc.add_argument("kind", choices=["daily", "weekly", "monthly"],
                    help="The cron cadence to dispatch.")
    sc.add_argument("--registry",
                    help="Path to seo_sites.json (default inferred from PWP_REPO_ROOT).")
    sc.add_argument("--publish-root",
                    help="Directory the per-site runs land in (default /tmp/pwp-kpi-runs/<kind>).")
    sc.add_argument("--launcher",
                    help="Path to cron_launcher.py (default PWP_KPI_CRON_LAUNCHER or HDE_KPI_REPO_ROOT).")
    sc.add_argument("--timeout", type=int, default=120,
                    help="Per-site subprocess timeout in seconds (default 120).")
    sc.set_defaults(func=cmd_cron)

    # Provisioning (Phase 1 Cloudflare-first MVP) — delegates to the
    # provision_site capability's operator_cli.attach_subparser().
    try:
        from prismatic.shipped_plugins.pwp.capabilities.provision_site import operator_cli as prov_cli
    except ImportError:
        import prismatic.shipped_plugins.pwp.capabilities.provision_site.operator_cli as prov_cli
    prov_cli.attach_subparser(sub)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
