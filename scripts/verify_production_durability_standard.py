#!/usr/bin/env python3
"""Verify the Prismatic Production Durability Standard.

Credential-free by default. The verifier enforces documentation/checklist
coverage and backend compilation everywhere. If a local gateway is reachable,
it also checks /health, route table, configured route behavior, and
workspace-tree-style path-safety probes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import py_compile
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
STANDARD_DOC = REPO_ROOT / "docs" / "prismatic-production-durability-standard.md"
CHECKLIST_DOC = REPO_ROOT / "docs" / "agent-production-route-checklist.md"
REVIEW_GATE_DOC = REPO_ROOT / "docs" / "production-durability-review-gate.md"
AGENT_BRIEF_DOC = REPO_ROOT / "docs" / "prompts" / "production-durability-agent-brief.md"
WORKTREE_PLAN_DOC = REPO_ROOT / "docs" / "production-worktree-durability-migration-plan.md"
GATEWAY_SERVER = REPO_ROOT / "prismatic" / "gateway" / "server.py"
FINAL_MARKER = "PRODUCTION_DURABILITY_VERIFIER_OK"

STANDARD_REQUIRED = [
    "PRODUCTION_DURABILITY_STANDARD_DOC_OK",
    "mutable live worktrees are unsafe",
    "production checkout / worktree model",
    "local-first verification ladder",
    "public/authenticated verification ladder",
    "Browser and screenshot evidence expectations",
    "CDN and frontend fallback robustness",
    "Path-safety and security expectations",
    "Deploy, restart, reload",
    "Rollback expectations",
    "PRODUCTION_DURABILITY_PROOF_PACKET",
    "ad_hoc_targeted",
    "canonical_suite_green",
    "PRODUCTION_DURABILITY_REVIEW_GATE_OK",
    "PRODUCTION_DURABILITY_AGENT_BRIEF_OK",
    "PRODUCTION_WORKTREE_DURABILITY_PLAN_OK",
    "PRISMATIC_PRODUCTION_DURABILITY_STANDARD_OK",
]

CHECKLIST_REQUIRED = [
    "AGENT_PRODUCTION_ROUTE_CHECKLIST_OK",
    "Branch / worktree gate",
    "Local reproduce gate",
    "Patch-scope gate",
    "Local route / API proof gate",
    "Security / path traversal proof gate",
    "Browser / screenshot proof gate",
    "Deploy / restart / reload proof gate",
    "Public / authenticated proof gate",
    "Rollback / cleanup proof gate",
    "verification_scope=ad_hoc_targeted",
    "PRODUCTION_DURABILITY_REVIEW_GATE_OK",
    "PRODUCTION_DURABILITY_AGENT_BRIEF_OK",
    "PRODUCTION_WORKTREE_DURABILITY_PLAN_OK",
]


REVIEW_GATE_REQUIRED = [
    "PRODUCTION_DURABILITY_REVIEW_GATE_OK",
    "Does this affect a live route/service/dashboard?",
    "production-safe branch/worktree proof",
    "local gateway/service proof",
    "public/authenticated proof",
    "screenshot/browser proof",
    "rollback path",
    "future GitHub PR templates",
]

AGENT_BRIEF_REQUIRED = [
    "PRODUCTION_DURABILITY_AGENT_BRIEF_OK",
    "do **not** work from the mutable production checkout",
    "Use a clean branch or clean production-safe worktree",
    "Verify locally first",
    "Deploy intentionally",
    "Attach browser or screenshot proof",
    "Do not claim production fixed from code/static checks alone",
]

WORKTREE_PLAN_REQUIRED = [
    "PRODUCTION_WORKTREE_DURABILITY_PLAN_OK",
    "WorkingDirectory: /home/ubuntu/work/prismatic-engine",
    "live service source != mutable multi-agent development checkout",
    "/home/ubuntu/.prismatic/runtime/prismatic-engine",
    "Why not implemented in this slice",
    "Required follow-up",
    "GRO-3942",
]

WORKSPACE_ROUTE_HINTS = ("workspace-tree", "workspace_tree", "workspace")
TRAVERSAL_PROBES = [
    "../../etc/passwd",
    "%2e%2e/%2e%2e/etc/passwd",
    "/etc/passwd",
]
BLOCKED_STATUSES = {400, 401, 403, 404, 422}
OK_STATUSES = {200, 204, 301, 302, 307, 308}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def add_check(checks: list[dict[str, Any]], name: str, result: str, **extra: Any) -> None:
    item: dict[str, Any] = {"name": name, "result": result}
    item.update(extra)
    checks.append(item)


def require_substrings(text: str, required: list[str]) -> tuple[bool, list[str]]:
    missing = [needle for needle in required if needle not in text]
    return not missing, missing


def http_get(base: str, path: str, timeout: float) -> dict[str, Any]:
    url = urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(15000)
            return {
                "url": url,
                "reachable": True,
                "status": int(response.status),
                "headers": dict(response.headers.items()),
                "body_preview": body.decode("utf-8", "replace")[:1000],
                "elapsed_ms": round((time.time() - started) * 1000, 2),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(15000)
        return {
            "url": url,
            "reachable": True,
            "status": int(exc.code),
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "body_preview": body.decode("utf-8", "replace")[:1000],
            "elapsed_ms": round((time.time() - started) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics need exact failure
        return {
            "url": url,
            "reachable": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "elapsed_ms": round((time.time() - started) * 1000, 2),
        }


def import_gateway_routes() -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location("prismatic.gateway.server", GATEWAY_SERVER)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load prismatic.gateway.server spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules["prismatic.gateway.server"] = module
        spec.loader.exec_module(module)
        app = getattr(module, "app")
        routes = []
        for route in getattr(app, "routes", []):
            routes.append(
                {
                    "path": getattr(route, "path", None),
                    "methods": sorted(getattr(route, "methods", []) or []),
                    "name": getattr(route, "name", None),
                }
            )
        return {"ok": True, "routes": routes}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(limit=8)}


def route_present(routes: list[dict[str, Any]], route: str) -> bool:
    route = route.split("?", 1)[0]
    return any(item.get("path") == route for item in routes)


def route_is_workspace_like(route: str) -> bool:
    return any(hint in route for hint in WORKSPACE_ROUTE_HINTS)


def build_route_path(route: str, safe_file: str) -> str:
    if "?" in route:
        return route
    if route_is_workspace_like(route):
        return f"{route}?file={urllib.parse.quote(safe_file)}"
    return route


def preview_path_for_probe(route: str, probe: str) -> str:
    encoded = probe if "%" in probe else urllib.parse.quote(probe, safe="/")
    if route.startswith("/api/workspace-tree/preview"):
        return f"/api/workspace-tree/preview?file={encoded}"
    return f"/api/workspace-tree/preview?file={encoded}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Prismatic Production Durability Standard")
    parser.add_argument("--route", default="/workspace-tree", help="Production route to check when local gateway is reachable")
    parser.add_argument("--local-base", default="http://127.0.0.1:9000", help="Local gateway base URL")
    parser.add_argument("--safe-file", default="docs/prismatic-production-durability-standard.md", help="Safe repo-relative file for workspace-tree checks")
    parser.add_argument("--timeout", type=float, default=2.5, help="HTTP timeout seconds")
    parser.add_argument("--require-local", action="store_true", help="Fail if local gateway is unreachable")
    parser.add_argument("--enforce-route", action="store_true", help="Fail when reachable local route/table/path-safety expectations do not pass; use for production route fix closeout")
    parser.add_argument("--screenshot-artifact", default="", help="Optional screenshot/browser proof artifact path")
    parser.add_argument("--public-base", default="", help="Optional public base URL; auth failures are reported, not required")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []

    for path, required, name in [
        (STANDARD_DOC, STANDARD_REQUIRED, "standard_doc"),
        (CHECKLIST_DOC, CHECKLIST_REQUIRED, "agent_checklist"),
        (REVIEW_GATE_DOC, REVIEW_GATE_REQUIRED, "review_gate"),
        (AGENT_BRIEF_DOC, AGENT_BRIEF_REQUIRED, "agent_brief"),
        (WORKTREE_PLAN_DOC, WORKTREE_PLAN_REQUIRED, "worktree_plan"),
    ]:
        if not path.exists():
            add_check(checks, name, "fail", path=str(path), error="missing")
            failures.append(f"{name}: missing {path}")
            continue
        text = read_text(path)
        ok, missing = require_substrings(text, required)
        add_check(checks, name, "pass" if ok else "fail", path=str(path.relative_to(REPO_ROOT)), missing=missing, bytes=len(text.encode()))
        if not ok:
            failures.append(f"{name}: missing {missing}")

    try:
        py_compile.compile(str(GATEWAY_SERVER), doraise=True)
        add_check(checks, "gateway_server_py_compile", "pass", path=str(GATEWAY_SERVER.relative_to(REPO_ROOT)))
    except Exception as exc:  # noqa: BLE001
        add_check(checks, "gateway_server_py_compile", "fail", path=str(GATEWAY_SERVER.relative_to(REPO_ROOT)), error=repr(exc))
        failures.append("gateway_server_py_compile failed")

    route_table = import_gateway_routes()
    add_check(checks, "gateway_route_table_import", "pass" if route_table.get("ok") else "fail", **route_table)
    if not route_table.get("ok"):
        failures.append("gateway route table import failed")

    if args.screenshot_artifact:
        screenshot = Path(args.screenshot_artifact)
        if screenshot.exists():
            add_check(checks, "screenshot_artifact", "pass", path=str(screenshot), bytes=screenshot.stat().st_size)
        else:
            add_check(checks, "screenshot_artifact", "fail", path=str(screenshot), error="missing")
            failures.append("screenshot artifact was requested but missing")
    else:
        add_check(checks, "screenshot_artifact", "skipped", reason="not provided; required for real production route fix closeout")

    health = http_get(args.local_base, "/health", args.timeout)
    local_reachable = bool(health.get("reachable"))
    if not local_reachable:
        add_check(checks, "local_gateway_health", "skipped" if not args.require_local else "fail", **health)
        message = f"local gateway unreachable at {args.local_base}"
        if args.require_local:
            failures.append(message)
        else:
            warnings.append(message)
            add_check(checks, "local_route_checks", "skipped", reason="local gateway unreachable", route=args.route)
            add_check(checks, "path_safety_checks", "skipped", reason="local gateway unreachable", route=args.route)
    else:
        health_ok = health.get("status") in OK_STATUSES
        add_check(checks, "local_gateway_health", "pass" if health_ok else "fail", **health)
        if not health_ok:
            failures.append(f"local /health returned {health.get('status')}")

        routes = route_table.get("routes", []) if route_table.get("ok") else []
        expected_route_present = route_present(routes, args.route)
        route_table_result = "pass" if expected_route_present else ("fail" if args.enforce_route else "needs_action")
        add_check(checks, "route_table_expected_route", route_table_result, route=args.route, present=expected_route_present, enforce_route=args.enforce_route)
        if not expected_route_present:
            message = f"route table missing {args.route}"
            (failures if args.enforce_route else warnings).append(message)

        route_path = build_route_path(args.route, args.safe_file)
        route_response = http_get(args.local_base, route_path, args.timeout)
        route_ok = route_response.get("reachable") and route_response.get("status") in OK_STATUSES
        local_route_result = "pass" if route_ok else ("fail" if args.enforce_route else "needs_action")
        add_check(checks, "local_route_response", local_route_result, route=route_path, enforce_route=args.enforce_route, **route_response)
        if not route_ok:
            message = f"local route {route_path} failed with {route_response.get('status') or route_response.get('error')}"
            (failures if args.enforce_route else warnings).append(message)

        if route_is_workspace_like(args.route):
            safe_preview = http_get(
                args.local_base,
                f"/api/workspace-tree/preview?file={urllib.parse.quote(args.safe_file)}",
                args.timeout,
            )
            safe_ok = safe_preview.get("reachable") and safe_preview.get("status") in OK_STATUSES
            safe_preview_result = "pass" if safe_ok else ("fail" if args.enforce_route else "needs_action")
            add_check(checks, "workspace_tree_safe_preview", safe_preview_result, file=args.safe_file, enforce_route=args.enforce_route, **safe_preview)
            if not safe_ok:
                message = "workspace-tree safe preview failed"
                (failures if args.enforce_route else warnings).append(message)

            for probe in TRAVERSAL_PROBES:
                probe_path = preview_path_for_probe(args.route, probe)
                response = http_get(args.local_base, probe_path, args.timeout)
                blocked = response.get("reachable") and response.get("status") in BLOCKED_STATUSES
                leaked_passwd = "root:" in str(response.get("body_preview", ""))
                ok = blocked and not leaked_passwd
                path_result = "pass" if ok else ("fail" if args.enforce_route else "needs_action")
                add_check(checks, "workspace_tree_path_safety", path_result, probe=probe, enforce_route=args.enforce_route, blocked=blocked, leaked_passwd=leaked_passwd, **response)
                if not ok:
                    message = f"workspace-tree path safety failed for {probe}"
                    (failures if args.enforce_route else warnings).append(message)
        else:
            add_check(checks, "path_safety_checks", "skipped", reason="route is not workspace-tree-like", route=args.route)

    if args.public_base:
        public_path = build_route_path(args.route, args.safe_file)
        public_response = http_get(args.public_base, public_path, args.timeout)
        public_status = public_response.get("status")
        if not public_response.get("reachable"):
            status = "skipped_auth_required"
            reason = public_response.get("error")
        elif public_status in {401, 403}:
            status = "skipped_auth_required"
            reason = f"HTTP {public_status}"
        elif public_status in OK_STATUSES:
            status = "pass"
            reason = "public route reachable"
        else:
            status = "fail"
            reason = f"unexpected HTTP {public_status}"
            failures.append(f"public route failed: {reason}")
        add_check(checks, "public_route_response", status, reason=reason, **public_response)
    else:
        add_check(checks, "public_route_response", "skipped_auth_required", reason="no public base/authenticated session provided")

    ok = not failures
    output = {
        "marker": FINAL_MARKER if ok else "PRODUCTION_DURABILITY_VERIFIER_FAILED",
        "ok": ok,
        "route": args.route,
        "local_base": args.local_base,
        "verification_scope": "ad_hoc_targeted_standard_verifier",
        "notes": [
            "Credential-free/local mode does not require Cloudflare/auth credentials.",
            "Public/authenticated checks are skipped_auth_required unless --public-base and auth/session context are supplied.",
            "Screenshot/browser proof is reported separately and is required for real route fix closeout.",
            "Use --enforce-route --require-local for production route fix closeout; without --enforce-route, reachable route gaps are reported as needs_action.",
        ],
        "warnings": warnings,
        "failures": failures,
        "checks": checks,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    if ok:
        print(FINAL_MARKER)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
