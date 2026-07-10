#!/usr/bin/env python3
"""Fixture-only PWP theme pipeline dry-run.

The dry-run models the Phase 9.5 handoff without touching production systems:
intake -> route/module plan -> Linear tree artifact -> theme scaffold -> verification report.

It intentionally writes files under an output directory only. It does not call Linear,
Cloudflare, Jules, dispatchers, or deployment adapters. Grimly boring. Correct.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_FIXTURE = Path(__file__).with_name("fixtures") / "sentinel_itad_fixture.json"
DEFAULT_OUTPUT_ROOT = Path("/tmp/pwp-theme-dry-run")
REQUIRED_INTAKE_FIELDS = (
    "client_id",
    "business_name",
    "industry",
    "markets",
    "services",
    "primary_goal",
)
MODULE_LIBRARY = {
    "hero": {"component": "Hero.astro", "variants": ["split-media", "centered", "service-detail"]},
    "warning-strip": {"component": "WarningStrip.astro", "variants": ["warning", "info", "proof-note"]},
    "card-grid": {"component": "CardGrid.astro", "variants": ["3-up", "4-up", "linked-card"]},
    "trust-panel": {"component": "TrustPanel.astro", "variants": ["numbered", "certificate-aware"]},
    "process-timeline": {"component": "ProcessTimeline.astro", "variants": ["steps", "compliance-chain"]},
    "split-section": {"component": "SplitSection.astro", "variants": ["service-proof", "local-proof"]},
    "lead-capture": {"component": "LeadCapture.astro", "variants": ["short-form", "phone-first"]},
    "faq": {"component": "FAQ.astro", "variants": ["accordion", "compliance"]},
    "local-seo-block": {"component": "LocalSEOBlock.astro", "variants": ["service-area", "city-page"]},
}


@dataclass(frozen=True)
class DryRunResult:
    run_id: str
    output_dir: Path
    route_plan: Path
    module_plan: Path
    linear_tree: Path
    theme_manifest: Path
    verification_report: Path


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "fixture-client"


def load_intake(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    missing = [field for field in REQUIRED_INTAKE_FIELDS if not data.get(field)]
    if missing:
        raise ValueError(f"fixture intake missing required field(s): {', '.join(missing)}")
    return data


def build_route_plan(intake: dict[str, Any]) -> dict[str, Any]:
    services = intake["services"]
    markets = intake.get("markets", [])
    service_routes = [
        {
            "path": f"/services/{_slug(service['name'])}/",
            "purpose": service.get("purpose", f"Convert demand for {service['name']}"),
            "primary_keyword": service.get("primary_keyword", service["name"]),
            "modules": ["hero", "trust-panel", "process-timeline", "lead-capture", "faq"],
        }
        for service in services
    ]
    market_routes = [
        {
            "path": f"/service-areas/{_slug(market)}/",
            "purpose": f"Local proof and contact path for {market}",
            "primary_keyword": f"{intake['industry']} {market}",
            "modules": ["hero", "card-grid", "local-seo-block", "lead-capture"],
        }
        for market in markets[:4]
    ]
    routes = [
        {
            "path": "/",
            "purpose": intake["primary_goal"],
            "primary_keyword": intake.get("primary_keyword", intake["industry"]),
            "modules": ["hero", "warning-strip", "card-grid", "trust-panel", "process-timeline", "lead-capture"],
        },
        *service_routes,
        *market_routes,
        {
            "path": "/contact/",
            "purpose": "Low-friction quote and pickup inquiry",
            "primary_keyword": f"contact {intake['business_name']}",
            "modules": ["hero", "lead-capture", "faq"],
        },
    ]
    return {"client_id": intake["client_id"], "route_count": len(routes), "routes": routes}


def build_module_plan(intake: dict[str, Any], route_plan: dict[str, Any]) -> dict[str, Any]:
    used = sorted({module for route in route_plan["routes"] for module in route["modules"]})
    modules = []
    for module_id in used:
        contract = MODULE_LIBRARY[module_id]
        modules.append(
            {
                "id": module_id,
                "component": contract["component"],
                "variants": contract["variants"],
                "editable_fields": _editable_fields_for(module_id),
                "a11y_rules": ["image-alt-required", "keyboard-focus-visible"],
                "content_safety_rules": _safety_rules_for(intake, module_id),
            }
        )
    return {"client_id": intake["client_id"], "theme_family": choose_theme_family(intake), "modules": modules}


def choose_theme_family(intake: dict[str, Any]) -> str:
    tags = {str(tag).lower() for tag in intake.get("style_tags", [])}
    if {"trust", "security"}.intersection(tags) or "compliance" in str(intake.get("industry", "")).lower():
        return "trust-light"
    return "local-service-light"


def _editable_fields_for(module_id: str) -> list[str]:
    base = {
        "hero": ["eyebrow", "title", "body", "ctas", "media"],
        "lead-capture": ["title", "body", "form_fields", "success_message"],
        "faq": ["questions"],
    }
    return base.get(module_id, ["title", "body", "items"])


def _safety_rules_for(intake: dict[str, Any], module_id: str) -> list[str]:
    rules = ["no-unsupported-certification-claims"]
    if "itad" in str(intake.get("industry", "")).lower() or "asset disposal" in str(intake.get("industry", "")).lower():
        rules.append("no-data-destruction-guarantee-without-certificate")
    if module_id == "lead-capture":
        rules.append("no-sensitive-asset-details-in-contact-form")
    return rules


def _component_name_for_module(module_id: str) -> str:
    return MODULE_LIBRARY[module_id]["component"].removesuffix(".astro")


def build_linear_tree(intake: dict[str, Any], route_plan: dict[str, Any], module_plan: dict[str, Any]) -> dict[str, Any]:
    prefix = intake.get("linear_prefix", "PWP-DRYRUN")
    tasks = [
        {
            "title": f"[{prefix}] Verify intake and compliance boundaries for {intake['business_name']}",
            "labels": ["plugin:pwp", "agent:ned-infra", "agent:needs-human-review"],
            "state": "Todo",
            "dispatch_ready": False,
        },
        {
            "title": f"[{prefix}] Scaffold {module_plan['theme_family']} theme package",
            "labels": ["plugin:pwp", "prismatic-engine", "agent:ned-infra"],
            "state": "Todo",
            "dispatch_ready": False,
        },
        {
            "title": f"[{prefix}] Render {route_plan['route_count']} fixture routes and collect verification artifacts",
            "labels": ["plugin:pwp", "qa", "agent:needs-human-review"],
            "state": "Todo",
            "dispatch_ready": False,
        },
    ]
    return {
        "mode": "artifact_only_no_linear_mutation",
        "parent_title": f"PWP fixture dry-run for {intake['business_name']}",
        "tasks": tasks,
        "assertions": {
            "no_dispatch_ready": all(not task["dispatch_ready"] for task in tasks),
            "no_production_deploy": True,
        },
    }


def build_theme_manifest(intake: dict[str, Any], module_plan: dict[str, Any]) -> dict[str, Any]:
    theme_family = module_plan["theme_family"]
    return {
        "$schema": "https://schemas.prismatic.dev/pwp/theme.schema.json",
        "id": f"pwp.theme.{theme_family}.{_slug(intake['client_id'])}",
        "name": f"{theme_family} fixture scaffold for {intake['business_name']}",
        "version": "0.1.0-dry-run",
        "engineCompatibility": ">=0.2.0",
        "framework": "astro",
        "editableWith": ["emdash"],
        "industryFit": [intake["industry"], *intake.get("markets", [])[:2]],
        "styleTags": intake.get("style_tags", ["trust", "local", "conversion"]),
        "entrypoints": {
            "tokens": "tokens/tokens.json",
            "css": "src/styles/theme.css",
            "layout": "src/layouts/BaseLayout.astro",
            "components": "src/components/index.ts",
            "contentSchema": "src/content.config.ts",
            "emdashMap": "emdash/fields.json",
        },
        "modules": [module["id"] for module in module_plan["modules"]],
        "verification": {
            "commands": ["python -m json.tool theme.json", "python -m json.tool route-plan.json"],
            "requiresVisual": False,
            "requiresA11y": True,
            "performanceBudget": "budgets/lighthouse.json",
        },
    }


def write_theme_scaffold(scaffold_dir: Path, manifest: dict[str, Any], module_plan: dict[str, Any]) -> None:
    (scaffold_dir / "tokens").mkdir(parents=True, exist_ok=True)
    (scaffold_dir / "src" / "components").mkdir(parents=True, exist_ok=True)
    (scaffold_dir / "src" / "layouts").mkdir(parents=True, exist_ok=True)
    (scaffold_dir / "src" / "styles").mkdir(parents=True, exist_ok=True)
    (scaffold_dir / "emdash").mkdir(parents=True, exist_ok=True)
    (scaffold_dir / "budgets").mkdir(parents=True, exist_ok=True)
    _write_json(scaffold_dir / "theme.json", manifest)
    _write_json(
        scaffold_dir / "tokens" / "tokens.json",
        {
            "color": {
                "background": {"page": {"$value": "#f7faf8", "$type": "color"}},
                "text": {"primary": {"$value": "#16211d", "$type": "color"}},
                "accent": {"primary": {"$value": "#1f7a4d", "$type": "color"}},
            },
            "font": {"body": {"family": {"$value": "Inter, system-ui, sans-serif", "$type": "fontFamily"}}},
            "space": {"md": {"$value": "1rem", "$type": "dimension"}},
        },
    )
    _write_json(
        scaffold_dir / "emdash" / "fields.json",
        {
            "contentType": "landingPage",
            "lockedFields": ["complianceClaims", "legalName", "schemaOrgType"],
            "blocks": [
                {"blockId": module["id"], "component": module["component"].replace(".astro", ""), "fields": module["editable_fields"]}
                for module in module_plan["modules"]
            ],
        },
    )
    _write_json(scaffold_dir / "budgets" / "lighthouse.json", {"performance": 0.85, "accessibility": 0.95, "seo": 0.9})
    (scaffold_dir / "src" / "styles" / "theme.css").write_text(
        ":root {\n  --pwp-color-background-page: #f7faf8;\n  --pwp-color-text-primary: #16211d;\n  --pwp-color-accent-primary: #1f7a4d;\n}\n",
        encoding="utf-8",
    )
    (scaffold_dir / "src" / "layouts" / "BaseLayout.astro").write_text(
        "---\nconst { title = 'PWP fixture' } = Astro.props;\n---\n<html lang=\"en\"><head><title>{title}</title></head><body><slot /></body></html>\n",
        encoding="utf-8",
    )
    component_exports = []
    for module in module_plan["modules"]:
        component_name = module["component"]
        (scaffold_dir / "src" / "components" / component_name).write_text(
            f"---\nconst {{ title = '{module['id']}' }} = Astro.props;\n---\n<section data-pwp-module=\"{module['id']}\"><h2>{{title}}</h2><slot /></section>\n",
            encoding="utf-8",
        )
        component_exports.append(f"export {{ default as {component_name.removesuffix('.astro')} }} from './{component_name}';")
    (scaffold_dir / "src" / "components" / "index.ts").write_text("\n".join(component_exports) + "\n", encoding="utf-8")


def verify_artifacts(output_dir: Path, route_plan: dict[str, Any], linear_tree: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    checks = []
    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    scaffold = output_dir / "theme-scaffold"
    add("route_plan_has_routes", route_plan.get("route_count", 0) >= 3, str(route_plan.get("route_count")))
    add("linear_tree_artifact_only", linear_tree.get("mode") == "artifact_only_no_linear_mutation", linear_tree.get("mode", ""))
    add("no_dispatch_ready", bool(linear_tree.get("assertions", {}).get("no_dispatch_ready")), "all generated tasks dispatch_ready=false")
    add("no_production_deploy", bool(linear_tree.get("assertions", {}).get("no_production_deploy")), "dry-run only")
    add("theme_manifest_has_astro", manifest.get("framework") == "astro", manifest.get("framework", ""))
    add("theme_scaffold_has_manifest", (scaffold / "theme.json").exists(), str(scaffold / "theme.json"))
    add("theme_scaffold_has_emdash_map", (scaffold / "emdash" / "fields.json").exists(), "locked fields present")
    generated_components = sorted(p.name for p in (scaffold / "src" / "components").glob("*.astro"))
    expected_components = sorted(f"{_component_name_for_module(module_id)}.astro" for module_id in manifest["modules"])
    add("components_match_manifest", generated_components == expected_components, "module components generated")
    ok = all(check["ok"] for check in checks)
    return {
        "ok": ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "checks": checks,
    }


def run_dry_run(intake_path: Path, output_root: Path = DEFAULT_OUTPUT_ROOT, run_id: str | None = None) -> DryRunResult:
    intake = load_intake(intake_path)
    digest = hashlib.sha256(json.dumps(intake, sort_keys=True).encode()).hexdigest()[:10]
    run_id = run_id or f"{_slug(intake['client_id'])}-{digest}"
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    route_plan = build_route_plan(intake)
    module_plan = build_module_plan(intake, route_plan)
    linear_tree = build_linear_tree(intake, route_plan, module_plan)
    manifest = build_theme_manifest(intake, module_plan)

    route_path = output_dir / "route-plan.json"
    module_path = output_dir / "module-plan.json"
    linear_path = output_dir / "linear-tree-artifact.json"
    scaffold_dir = output_dir / "theme-scaffold"
    report_path = output_dir / "verification-report.json"

    _write_json(output_dir / "intake-normalized.json", intake)
    _write_json(route_path, route_plan)
    _write_json(module_path, module_plan)
    _write_json(linear_path, linear_tree)
    write_theme_scaffold(scaffold_dir, manifest, module_plan)
    report = verify_artifacts(output_dir, route_plan, linear_tree, manifest)
    _write_json(report_path, report)
    if not report["ok"]:
        raise RuntimeError(f"dry-run verification failed: {report_path}")
    return DryRunResult(run_id, output_dir, route_path, module_path, linear_path, scaffold_dir / "theme.json", report_path)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixture-only PWP Phase 9.5 theme dry-run.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Fixture intake JSON path")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Directory for generated dry-run artifacts")
    parser.add_argument("--run-id", help="Optional deterministic run id")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_dry_run(args.fixture, args.output_root, args.run_id)
    payload = {
        "ok": True,
        "run_id": result.run_id,
        "output_dir": str(result.output_dir),
        "route_plan": str(result.route_plan),
        "module_plan": str(result.module_plan),
        "linear_tree": str(result.linear_tree),
        "theme_manifest": str(result.theme_manifest),
        "verification_report": str(result.verification_report),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"PWP dry-run complete: {result.output_dir}")
        print(f"verification_report={result.verification_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
