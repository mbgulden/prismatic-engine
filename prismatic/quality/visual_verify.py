# SPDX-License-Identifier: AGPL-3.0-only
"""Visual verification gate for rendered UI artifacts.

This module is intentionally stdlib-only so the gate can run in CI, cron, and
agent sandboxes before optional BrowserMCP/Nano Banana services are available.
When those services are configured, the gate can capture screenshots through a
BrowserMCP-compatible HTTP endpoint and grade them through a multimodal model
endpoint. When they are not configured, callers can still verify existing
screenshots with deterministic local checks.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_VIEWPORTS = ("desktop:1440x900", "tablet:768x1024", "mobile:390x844")
BROKEN_LAYOUT_MARKERS = (
    "horizontal overflow",
    "text clipping",
    "clipped text",
    "overlap",
    "broken grid",
    "missing image",
    "404",
    "error boundary",
)


@dataclass(frozen=True)
class ViewportSpec:
    """Viewport requested from BrowserMCP."""

    name: str
    width: int
    height: int

    @classmethod
    def parse(cls, value: str) -> ViewportSpec:
        """Parse NAME:WIDTHxHEIGHT strings used by the CLI."""
        if ":" not in value or "x" not in value:
            raise ValueError(f"viewport must be NAME:WIDTHxHEIGHT, got {value!r}")
        name, dims = value.split(":", 1)
        width_s, height_s = dims.lower().split("x", 1)
        width = int(width_s)
        height = int(height_s)
        if not name or width <= 0 or height <= 0:
            raise ValueError(f"invalid viewport {value!r}")
        return cls(name=name, width=width, height=height)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "width": self.width, "height": self.height}


@dataclass
class ScreenshotArtifact:
    """One screenshot plus optional extracted page text."""

    viewport: str
    path: str
    width: int = 0
    height: int = 0
    text: str = ""
    source: str = "provided"

    def exists(self) -> bool:
        return bool(self.path) and Path(self.path).exists()

    def byte_size(self) -> int:
        return Path(self.path).stat().st_size if self.exists() else 0


@dataclass
class VisualFinding:
    """One visual verification finding."""

    check: str
    status: str
    detail: str
    viewport: str = ""
    severity: str = "error"


@dataclass
class VisualVerifyResult:
    """Structured visual gate result."""

    passed: bool
    url: str = ""
    score: float = 0.0
    min_score: float = 0.8
    attempts: int = 1
    screenshots: list[ScreenshotArtifact] = field(default_factory=list)
    findings: list[VisualFinding] = field(default_factory=list)
    reason: str = ""
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        icon = "✅" if self.passed else "❌"
        lines = [
            f"# {icon} Visual verification",
            "",
            f"- URL: {self.url or '(screenshot-only)'}",
            f"- Score: {self.score:.2f} / required {self.min_score:.2f}",
            f"- Attempts: {self.attempts}",
            f"- Verdict: {'PASS' if self.passed else 'FAIL'}",
            f"- Reason: {self.reason}",
            "",
            "## Screenshots",
            "",
        ]
        if self.screenshots:
            lines.append("| viewport | path | bytes | source |")
            lines.append("|---|---:|---:|---|")
            for shot in self.screenshots:
                lines.append(
                    f"| {shot.viewport} | `{shot.path}` | {shot.byte_size()} | {shot.source} |"
                )
        else:
            lines.append("No screenshots captured or supplied.")
        lines.extend(["", "## Findings", ""])
        if self.findings:
            lines.append("| status | severity | viewport | check | detail |")
            lines.append("|---|---|---|---|---|")
            for finding in self.findings:
                lines.append(
                    "| {status} | {severity} | {viewport} | {check} | {detail} |".format(
                        status=finding.status,
                        severity=finding.severity,
                        viewport=finding.viewport or "all",
                        check=finding.check,
                        detail=finding.detail.replace("|", "\\|"),
                    )
                )
        else:
            lines.append("No findings.")
        return "\n".join(lines) + "\n"


def _http_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach {url}: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"non-JSON response from {url}: {raw[:500]}") from exc
    if isinstance(parsed, dict) and parsed.get("error"):
        raise RuntimeError(f"error response from {url}: {parsed['error']}")
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"expected JSON object from {url}, got {type(parsed).__name__}"
        )
    return parsed


def _decode_screenshot(
    item: dict[str, Any], output_dir: Path, index: int
) -> ScreenshotArtifact:
    viewport = str(item.get("viewport") or item.get("name") or f"shot-{index}")
    path = item.get("path") or item.get("file")
    source = "browser-mcp"
    if not path and item.get("image_base64"):
        suffix = item.get("format") or "png"
        path = output_dir / f"visual-verify-{viewport}.{suffix}"
        Path(path).write_bytes(base64.b64decode(item["image_base64"]))
    if not path and item.get("data_url"):
        header, _, encoded = str(item["data_url"]).partition(",")
        guessed = "png" if "png" in header else "jpg"
        path = output_dir / f"visual-verify-{viewport}.{guessed}"
        Path(path).write_bytes(base64.b64decode(encoded))
    return ScreenshotArtifact(
        viewport=viewport,
        path=str(path or ""),
        width=int(item.get("width") or 0),
        height=int(item.get("height") or 0),
        text=str(item.get("text") or item.get("page_text") or ""),
        source=source,
    )


def capture_with_browser_mcp(
    *,
    endpoint: str,
    url: str,
    viewports: list[ViewportSpec],
    output_dir: Path,
    timeout: float = 30.0,
) -> list[ScreenshotArtifact]:
    """Capture screenshots using a BrowserMCP-compatible HTTP endpoint.

    The request shape is deliberately simple and explicit so an adapter MCP server
    can support it without depending on Prismatic internals.
    """
    payload = {
        "action": "capture_screenshots",
        "url": url,
        "viewports": [viewport.to_dict() for viewport in viewports],
        "return": ["path", "image_base64", "text", "dimensions"],
    }
    data = _http_json(endpoint, payload, timeout=timeout)
    items = data.get("screenshots") or data.get("artifacts") or []
    if not isinstance(items, list):
        raise RuntimeError("BrowserMCP response field 'screenshots' must be a list")
    return [_decode_screenshot(item, output_dir, i) for i, item in enumerate(items)]


def _read_local_screenshots(paths: list[str]) -> list[ScreenshotArtifact]:
    artifacts: list[ScreenshotArtifact] = []
    for idx, value in enumerate(paths):
        if ":" in value and not Path(value).exists():
            viewport, path = value.split(":", 1)
        else:
            viewport, path = (
                ("provided" if len(paths) == 1 else f"provided-{idx + 1}"),
                value,
            )
        artifacts.append(
            ScreenshotArtifact(viewport=viewport, path=path, source="provided")
        )
    return artifacts


def _image_data(path: str) -> str:
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def grade_with_nano(
    *,
    endpoint: str,
    api_key: str | None,
    screenshots: list[ScreenshotArtifact],
    prompt: str,
    reference: str | None,
    timeout: float = 60.0,
) -> tuple[float, list[VisualFinding]]:
    """Ask a multimodal visual model endpoint to grade screenshot artifacts."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload: dict[str, Any] = {
        "task": "visual_verification",
        "prompt": prompt,
        "screenshots": [
            {
                "viewport": shot.viewport,
                "path": shot.path,
                "image": _image_data(shot.path) if shot.exists() else None,
                "text": shot.text,
            }
            for shot in screenshots
        ],
    }
    if reference:
        ref_path = Path(reference)
        payload["reference"] = (
            _image_data(reference) if ref_path.exists() else reference
        )
    data = _http_json(endpoint, payload, headers=headers, timeout=timeout)
    score = float(data.get("score", 0.0))
    findings: list[VisualFinding] = []
    for item in data.get("findings", []) or []:
        if isinstance(item, dict):
            findings.append(
                VisualFinding(
                    check=str(item.get("check") or "model"),
                    status=str(item.get("status") or "failed"),
                    detail=str(item.get("detail") or item.get("message") or ""),
                    viewport=str(item.get("viewport") or ""),
                    severity=str(item.get("severity") or "error"),
                )
            )
    if not findings and score < 1.0:
        findings.append(
            VisualFinding("model_score", "failed", f"model returned score {score:.2f}")
        )
    return score, findings


def local_visual_checks(
    *,
    screenshots: list[ScreenshotArtifact],
    required_text: list[str],
    reference: str | None = None,
) -> tuple[float, list[VisualFinding]]:
    """Run deterministic local checks when no visual model is configured."""
    findings: list[VisualFinding] = []
    if not screenshots:
        findings.append(
            VisualFinding(
                "screenshot", "failed", "no screenshots were captured or supplied"
            )
        )
        return 0.0, findings

    for shot in screenshots:
        if not shot.exists():
            findings.append(
                VisualFinding(
                    "screenshot_exists",
                    "failed",
                    f"missing screenshot: {shot.path}",
                    shot.viewport,
                )
            )
            continue
        size = shot.byte_size()
        if size < 1024:
            findings.append(
                VisualFinding(
                    "screenshot_size",
                    "failed",
                    f"screenshot too small to be meaningful: {size} bytes",
                    shot.viewport,
                )
            )
        else:
            findings.append(
                VisualFinding(
                    "screenshot_size",
                    "passed",
                    f"screenshot present: {size} bytes",
                    shot.viewport,
                    "info",
                )
            )
        lower_text = shot.text.lower()
        for marker in BROKEN_LAYOUT_MARKERS:
            if marker in lower_text:
                findings.append(
                    VisualFinding(
                        "broken_layout_marker",
                        "failed",
                        f"page text contains marker {marker!r}",
                        shot.viewport,
                    )
                )

    combined_text = "\n".join(shot.text for shot in screenshots).lower()
    for text in required_text:
        if text.lower() not in combined_text:
            findings.append(
                VisualFinding(
                    "required_text", "failed", f"missing required text {text!r}"
                )
            )
        else:
            findings.append(
                VisualFinding(
                    "required_text",
                    "passed",
                    f"found required text {text!r}",
                    severity="info",
                )
            )

    if reference and Path(reference).exists():
        ref_hash = hashlib.sha256(Path(reference).read_bytes()).hexdigest()
        for shot in screenshots:
            if shot.exists():
                shot_hash = hashlib.sha256(Path(shot.path).read_bytes()).hexdigest()
                status = "passed" if shot_hash == ref_hash else "warning"
                severity = "info" if status == "passed" else "warning"
                detail = (
                    "exact byte match to reference"
                    if status == "passed"
                    else "differs from reference; model endpoint required for perceptual grading"
                )
                findings.append(
                    VisualFinding(
                        "reference_exact_match", status, detail, shot.viewport, severity
                    )
                )

    failures = [
        finding
        for finding in findings
        if finding.status == "failed" and finding.severity == "error"
    ]
    warnings = [finding for finding in findings if finding.status == "warning"]
    if failures:
        score = max(0.0, 1.0 - (len(failures) * 0.35) - (len(warnings) * 0.1))
    else:
        score = max(0.0, 1.0 - (len(warnings) * 0.1))
    return min(1.0, score), findings


def run_visual_verify(
    *,
    url: str = "",
    screenshot_paths: list[str] | None = None,
    browser_mcp_endpoint: str | None = None,
    nano_endpoint: str | None = None,
    nano_api_key: str | None = None,
    reference: str | None = None,
    required_text: list[str] | None = None,
    prompt: str = "Grade whether the rendered UI matches the requested layout and has no clipping, overlap, broken grids, or missing primary content.",
    viewports: list[ViewportSpec] | None = None,
    min_score: float = 0.8,
    output_dir: str | None = None,
    timeout: float = 30.0,
) -> VisualVerifyResult:
    """Run the visual verification gate and return a structured result."""
    out_dir = Path(output_dir or tempfile.mkdtemp(prefix="prismatic-visual-verify-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[ScreenshotArtifact] = []
    findings: list[VisualFinding] = []

    if screenshot_paths:
        shots.extend(_read_local_screenshots(screenshot_paths))
    elif browser_mcp_endpoint and url:
        try:
            shots.extend(
                capture_with_browser_mcp(
                    endpoint=browser_mcp_endpoint,
                    url=url,
                    viewports=viewports
                    or [ViewportSpec.parse(v) for v in DEFAULT_VIEWPORTS],
                    output_dir=out_dir,
                    timeout=timeout,
                )
            )
        except RuntimeError as exc:
            findings.append(VisualFinding("browser_mcp_capture", "failed", str(exc)))
    else:
        findings.append(
            VisualFinding(
                "input",
                "failed",
                "provide --screenshot or both --url and --browser-mcp-endpoint",
            )
        )

    if nano_endpoint and shots and not any(f.status == "failed" for f in findings):
        try:
            score, model_findings = grade_with_nano(
                endpoint=nano_endpoint,
                api_key=nano_api_key,
                screenshots=shots,
                prompt=prompt,
                reference=reference,
                timeout=max(timeout, 60.0),
            )
            findings.extend(model_findings)
        except RuntimeError as exc:
            findings.append(VisualFinding("nano_grade", "failed", str(exc)))
            score = 0.0
    else:
        score, local_findings = local_visual_checks(
            screenshots=shots,
            required_text=required_text or [],
            reference=reference,
        )
        findings.extend(local_findings)

    failed = [f for f in findings if f.status == "failed" and f.severity == "error"]
    passed = not failed and score >= min_score
    if failed:
        reason = f"{len(failed)} blocking visual finding(s)"
    elif score < min_score:
        reason = f"score {score:.2f} is below minimum {min_score:.2f}"
    else:
        reason = "visual verification passed"
    return VisualVerifyResult(
        passed=passed,
        url=url,
        score=score,
        min_score=min_score,
        screenshots=shots,
        findings=findings,
        reason=reason,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Prismatic rendered-output visual verification."
    )
    parser.add_argument("--url", default="", help="URL to capture through BrowserMCP")
    parser.add_argument(
        "--browser-mcp-endpoint",
        default=os.environ.get("BROWSER_MCP_ENDPOINT"),
        help="BrowserMCP HTTP endpoint",
    )
    parser.add_argument(
        "--nano-endpoint",
        default=os.environ.get("NANO_BANANA_ENDPOINT"),
        help="Nano Banana / multimodal grader HTTP endpoint",
    )
    parser.add_argument(
        "--nano-api-key",
        default=os.environ.get("NANO_BANANA_API_KEY"),
        help="API key for the visual grader",
    )
    parser.add_argument(
        "--screenshot",
        action="append",
        default=[],
        help="Existing screenshot path, optionally viewport:path",
    )
    parser.add_argument("--reference", default=None, help="Reference image path or URL")
    parser.add_argument(
        "--require-text",
        action="append",
        default=[],
        help="Text that must appear in captured page text",
    )
    parser.add_argument(
        "--viewport",
        action="append",
        default=[],
        help="Viewport NAME:WIDTHxHEIGHT; defaults to desktop/tablet/mobile",
    )
    parser.add_argument(
        "--prompt",
        default="Grade whether the rendered UI matches the requested layout and has no clipping, overlap, broken grids, or missing primary content.",
    )
    parser.add_argument("--min-score", type=float, default=0.8)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--json", action="store_true", help="Print JSON instead of markdown"
    )
    parser.add_argument(
        "--report", default=None, help="Write markdown report to this path"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        viewports = [
            ViewportSpec.parse(value) for value in (args.viewport or DEFAULT_VIEWPORTS)
        ]
    except ValueError as exc:
        parser.error(str(exc))

    result = run_visual_verify(
        url=args.url,
        screenshot_paths=args.screenshot,
        browser_mcp_endpoint=args.browser_mcp_endpoint,
        nano_endpoint=args.nano_endpoint,
        nano_api_key=args.nano_api_key,
        reference=args.reference,
        required_text=args.require_text,
        prompt=args.prompt,
        viewports=viewports,
        min_score=args.min_score,
        output_dir=args.output_dir,
        timeout=args.timeout,
    )
    if args.report:
        Path(args.report).write_text(result.to_markdown(), encoding="utf-8")
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.to_markdown())
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
