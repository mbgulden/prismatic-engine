# SPDX-License-Identifier: AGPL-3.0-only
"""CLI entry point for visual verification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prismatic.core.grader import (
    VisualVerifyError,
    parse_viewports,
    run_visual_verification,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic visual-verify",
        description="Capture multi-viewport screenshots and optionally run visual grading.",
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="Target http(s) or file URL. May also be supplied with --url.",
    )
    parser.add_argument("--url", dest="url_option", help="Target http(s) or file URL")
    parser.add_argument(
        "--file", action="store_true", help="Treat the target as a local HTML file path"
    )
    parser.add_argument(
        "--viewport",
        action="append",
        default=[],
        help="Additional viewport as name:WIDTHxHEIGHT; repeatable",
    )
    parser.add_argument(
        "--viewports",
        default=None,
        help="Comma-separated viewport list, e.g. desktop,tablet,mobile or hero:1440x900",
    )
    parser.add_argument(
        "--grade",
        action="store_true",
        help="Send captured screenshots to the configured visual grader",
    )
    parser.add_argument(
        "--model",
        default="gemini-3.1-flash",
        help="Visual grading model name (default: gemini-3.1-flash)",
    )
    parser.add_argument(
        "--output-dir",
        default=".prismatic/visual-verify",
        help="Directory for screenshots and visual-report.json",
    )
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=500,
        help="Delay after network idle before screenshots (default: 500)",
    )
    parser.add_argument(
        "--browser-endpoint",
        default=None,
        help="BrowserMCP HTTP endpoint (defaults to PRISMATIC_BROWSER_MCP_ENDPOINT)",
    )
    parser.add_argument(
        "--grader-endpoint",
        default=None,
        help="Visual grader endpoint (defaults to PRISMATIC_VISUAL_GRADER_ENDPOINT)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit compact JSON for deploy hooks"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    target_url = args.url_option or args.url
    if not target_url:
        parser.error("visual-verify requires a URL argument or --url")
    if args.file:
        target_url = Path(target_url).expanduser().resolve().as_uri()

    viewport_spec = args.viewports
    if args.viewport:
        viewport_spec = ",".join(
            [part for part in [viewport_spec, *args.viewport] if part]
        )

    output_dir = Path(args.output_dir).expanduser().resolve()
    try:
        result = run_visual_verification(
            url=target_url,
            viewports=parse_viewports(viewport_spec),
            output_dir=output_dir,
            grade=args.grade,
            model=args.model,
            delay_ms=args.delay_ms,
            browser_endpoint=args.browser_endpoint,
            grader_endpoint=args.grader_endpoint,
        )
        report_path = output_dir / "visual-report.json"
        result_payload = result.to_dict()
        report_path.write_text(
            json.dumps(result_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        viewport_results = _viewport_results(result_payload)
        overall_pass = all(item.get("pass", False) for item in viewport_results)
        payload = {
            "ok": overall_pass,
            "status": "pass" if overall_pass else "fail",
            "report": str(report_path),
            **result_payload,
            "viewport_results": viewport_results,
            "overall_pass": overall_pass,
        }
        print(
            json.dumps(payload, sort_keys=True)
            if args.json
            else json.dumps(payload, indent=2, sort_keys=True)
        )
        return 0 if overall_pass else 1
    except VisualVerifyError as exc:
        payload = {
            "ok": False,
            "status": "fail",
            "score": 0,
            "critical_count": 1,
            "issue_count": 1,
            "error": str(exc),
        }
        print(
            json.dumps(payload, sort_keys=True)
            if "--json" in (argv or [])
            else json.dumps(payload, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 1


def _viewport_results(result: dict) -> list[dict]:
    grade = result.get("grade") or {}
    reports = grade.get("viewportReports") if isinstance(grade, dict) else {}
    viewport_results = []
    for shot in result.get("screenshots", []):
        name = shot.get("name")
        report = reports.get(str(name), {}) if isinstance(reports, dict) else {}
        defects = report.get("defects", []) if isinstance(report, dict) else []
        grades = (
            {"visual": grade.get("score", 75)}
            if isinstance(grade, dict) and grade
            else {}
        )
        viewport_results.append(
            {
                "viewport": name,
                "screenshot": shot.get("path"),
                "width": shot.get("width"),
                "height": shot.get("height"),
                "grades": grades,
                "failures": defects,
                "pass": bool(report.get("pass", True))
                if isinstance(report, dict)
                else True,
            }
        )
    return viewport_results


if __name__ == "__main__":
    raise SystemExit(main())
