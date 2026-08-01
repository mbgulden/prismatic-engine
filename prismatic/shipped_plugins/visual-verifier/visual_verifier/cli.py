from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import VisualVerifier, parse_viewport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic-engine visual-verify",
        description="Run visual verification for a URL or local HTML file.",
    )
    parser.add_argument(
        "target", help="Target URL, or local file path when --file is set"
    )
    parser.add_argument(
        "--file", action="store_true", help="Treat target as a local HTML file"
    )
    parser.add_argument(
        "--viewport",
        action="append",
        default=[],
        help="Viewport as name:WIDTHxHEIGHT; repeatable",
    )
    parser.add_argument(
        "--check", action="append", default=[], help="Visual check name; repeatable"
    )
    parser.add_argument(
        "--output-dir", default=None, help="Screenshot output directory"
    )
    parser.add_argument(
        "--require-browsermcp",
        action="store_true",
        help="Fail if BrowserMCP/npx is not available",
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        viewports = [parse_viewport(v) for v in args.viewport] or None
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    verifier = VisualVerifier(
        output_dir=args.output_dir, require_browsermcp=args.require_browsermcp
    )
    if args.file:
        result = verifier.verify_file(
            args.target, viewports=viewports, checks=args.check or None
        )
    else:
        # Convenience: existing local paths are treated as files even without
        # --file, while true URLs go through verify_url.
        if Path(args.target).expanduser().exists():
            result = verifier.verify_file(
                args.target, viewports=viewports, checks=args.check or None
            )
        else:
            result = verifier.verify_url(
                args.target, viewports=viewports, checks=args.check or None
            )

    output = json.dumps(result, indent=2 if args.pretty else None, sort_keys=True)
    stream = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
    stream.write(output + "\n")
    return 0 if result.get("overall_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
