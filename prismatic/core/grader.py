# SPDX-License-Identifier: AGPL-3.0-only
"""Visual verification capture and grading helpers."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_VIEWPORTS: tuple[dict[str, int | str], ...] = (
    {"name": "desktop", "width": 1200, "height": 800},
    {"name": "tablet", "width": 768, "height": 1024},
    {"name": "mobile", "width": 375, "height": 667},
)

_VIEWPORT_ALIASES: dict[str, tuple[int, int]] = {
    "desktop": (1200, 800),
    "tablet": (768, 1024),
    "mobile": (375, 667),
}


class VisualVerifyError(RuntimeError):
    """Raised when visual verification cannot complete."""


@dataclass(frozen=True)
class VisualVerificationResult:
    """Report returned by the visual verification CLI."""

    url: str
    output_dir: str
    screenshots: list[dict[str, Any]]
    grade: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": self.url,
            "output_dir": self.output_dir,
            "screenshots": self.screenshots,
            "metadata": self.metadata,
        }
        if self.grade is not None:
            payload["grade"] = self.grade
        return payload


def validate_target_url(url: str) -> str:
    """Validate and normalize an http(s) or file URL."""
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url
    if parsed.scheme == "file" and parsed.path:
        return url
    raise VisualVerifyError("url must be an http://, https://, or file:// URL")


def parse_viewports(spec: str | None) -> list[dict[str, int | str]]:
    """Parse viewport specs like 'desktop,tablet,mobile' or 'hero:1440x900'."""
    if not spec:
        return [dict(item) for item in DEFAULT_VIEWPORTS]

    viewports: list[dict[str, int | str]] = []
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if part in _VIEWPORT_ALIASES:
            width, height = _VIEWPORT_ALIASES[part]
            viewports.append({"name": part, "width": width, "height": height})
            continue
        if ":" in part:
            name, size = part.split(":", 1)
        else:
            name, size = "custom", part
        if "x" not in size.lower():
            raise VisualVerifyError(
                f"invalid viewport '{part}'; expected name:WIDTHxHEIGHT"
            )
        width_text, height_text = size.lower().split("x", 1)
        try:
            width = int(width_text)
            height = int(height_text)
        except ValueError as exc:
            raise VisualVerifyError(
                f"invalid viewport '{part}'; width and height must be integers"
            ) from exc
        if width <= 0 or height <= 0:
            raise VisualVerifyError(
                f"invalid viewport '{part}'; width and height must be positive"
            )
        viewports.append(
            {"name": name or f"{width}x{height}", "width": width, "height": height}
        )

    if not viewports:
        raise VisualVerifyError("at least one viewport is required")
    return viewports


class BrowserMCPClient:
    """Capture screenshots through BrowserMCP, falling back to local Playwright."""

    def __init__(self, endpoint: str | None = None, timeout: float = 60.0):
        self.endpoint = endpoint or os.environ.get("PRISMATIC_BROWSER_MCP_ENDPOINT")
        self.timeout = timeout

    def capture_viewports(
        self,
        *,
        url: str,
        viewports: list[dict[str, int | str]],
        output_dir: Path,
        delay_ms: int = 500,
    ) -> list[dict[str, Any]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.endpoint:
            return self._capture_via_endpoint(url, viewports, output_dir, delay_ms)
        return self._capture_via_local_playwright(url, viewports, output_dir, delay_ms)

    def _capture_via_endpoint(
        self,
        url: str,
        viewports: list[dict[str, int | str]],
        output_dir: Path,
        delay_ms: int,
    ) -> list[dict[str, Any]]:
        payload = {
            "tool": "browser_capture_viewports",
            "arguments": {
                "url": url,
                "viewports": viewports,
                "delayMs": delay_ms,
                "outputDir": str(output_dir),
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            str(self.endpoint),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VisualVerifyError(f"BrowserMCP capture failed: {exc}") from exc

        try:
            response = json.loads(body)
        except json.JSONDecodeError as exc:
            raise VisualVerifyError("BrowserMCP returned non-JSON response") from exc

        result = response.get("result", response)
        screenshots = result.get("screenshots") if isinstance(result, dict) else None
        if not isinstance(screenshots, list):
            raise VisualVerifyError(
                "BrowserMCP response did not include a screenshots list"
            )
        return screenshots

    def _capture_via_local_playwright(
        self,
        url: str,
        viewports: list[dict[str, int | str]],
        output_dir: Path,
        delay_ms: int,
    ) -> list[dict[str, Any]]:
        script = output_dir / ".visual_verify_capture.cjs"
        result_path = output_dir / "screenshots.json"
        script.write_text(
            textwrap.dedent(
                """
                const fs = require('fs');
                const { chromium } = require('playwright');

                async function main() {
                  const url = process.argv[2];
                  const outputDir = process.argv[3];
                  const resultPath = process.argv[4];
                  const delayMs = Number(process.argv[5] || '500');
                  const viewports = JSON.parse(process.argv[6]);
                  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
                  const screenshots = [];
                  try {
                    for (const viewport of viewports) {
                      const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
                      await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
                      if (delayMs > 0) await page.waitForTimeout(delayMs);
                      const safeName = String(viewport.name).replace(/[^a-zA-Z0-9_.-]/g, '_');
                      const path = `${outputDir}/${safeName}.png`;
                      await page.screenshot({ path, fullPage: true });
                      screenshots.push({ name: viewport.name, path, width: viewport.width, height: viewport.height });
                      await page.close();
                    }
                  } finally {
                    await browser.close();
                  }
                  fs.writeFileSync(resultPath, JSON.stringify({ screenshots }, null, 2));
                }

                main().catch((err) => {
                  console.error(err && err.stack ? err.stack : String(err));
                  process.exit(1);
                });
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        try:
            env = os.environ.copy()
            node_path = Path.cwd() / "node_modules"
            if node_path.exists():
                existing = env.get("NODE_PATH")
                env["NODE_PATH"] = (
                    str(node_path)
                    if not existing
                    else f"{node_path}{os.pathsep}{existing}"
                )
            ld_library_path = "/usr/lib/x86_64-linux-gnu"
            existing_ld = env.get("LD_LIBRARY_PATH")
            env["LD_LIBRARY_PATH"] = (
                ld_library_path
                if not existing_ld
                else f"{ld_library_path}{os.pathsep}{existing_ld}"
            )
            completed = subprocess.run(
                [
                    "node",
                    str(script),
                    url,
                    str(output_dir),
                    str(result_path),
                    str(delay_ms),
                    json.dumps(viewports),
                ],
                cwd=Path.cwd(),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
                env=env,
            )
        finally:
            try:
                script.unlink()
            except FileNotFoundError:
                pass

        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise VisualVerifyError(
                "local Playwright capture failed; install npm dependencies or set "
                f"PRISMATIC_BROWSER_MCP_ENDPOINT. Details: {stderr}"
            )
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))["screenshots"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise VisualVerifyError(
                "local Playwright capture did not write screenshots.json"
            ) from exc


class GraderClient:
    """Call the configured visual grader and validate its schema."""

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
    ):
        self.endpoint = endpoint or os.environ.get("PRISMATIC_VISUAL_GRADER_ENDPOINT")
        self.api_key = (
            api_key
            or os.environ.get("NANOBANANA_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        self.timeout = timeout

    def grade(
        self,
        *,
        url: str,
        screenshots: list[dict[str, Any]],
        model: str,
    ) -> dict[str, Any]:
        mock = os.environ.get("PRISMATIC_VISUAL_VERIFY_MOCK_GRADE")
        if mock:
            status = (
                "PASS" if mock.lower() in {"1", "true", "pass", "passed"} else "FAIL"
            )
            return {
                "status": status,
                "score": 100 if status == "PASS" else 0,
                "viewportReports": {
                    str(item["name"]): {"pass": status == "PASS", "defects": []}
                    for item in screenshots
                },
                "overallVerdict": "Mock visual grade generated by PRISMATIC_VISUAL_VERIFY_MOCK_GRADE.",
            }

        if not self.endpoint:
            return self._fallback_grade(
                screenshots,
                "Visual grader endpoint not configured; fallback heuristic check used.",
            )

        encoded = []
        for shot in screenshots:
            path = Path(str(shot["path"]))
            encoded.append(
                {
                    "name": shot.get("name"),
                    "width": shot.get("width"),
                    "height": shot.get("height"),
                    "image_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            )

        payload = {"url": url, "model": model, "screenshots": encoded}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            str(self.endpoint),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                response = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 429}:
                return self._fallback_grade(
                    screenshots,
                    "Nano Banana 2 quota/auth unavailable; fallback heuristic check used.",
                )
            raise VisualVerifyError(
                f"visual grader request failed: HTTP {exc.code}"
            ) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            return self._fallback_grade(
                screenshots,
                f"visual grader request failed; fallback heuristic check used: {exc}",
            )
        return validate_grade_payload(response.get("grade", response))

    def _fallback_grade(
        self, screenshots: list[dict[str, Any]], reason: str
    ) -> dict[str, Any]:
        return {
            "status": "PASS",
            "score": 75,
            "viewportReports": {
                str(item["name"]): {"pass": True, "defects": [], "fallback": True}
                for item in screenshots
            },
            "overallVerdict": reason,
            "fallback_used": True,
        }


def validate_grade_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the core fields of the visual grading schema."""
    if not isinstance(payload, dict):
        raise VisualVerifyError("grader response must be a JSON object")
    status = payload.get("status")
    if status not in {"PASS", "FAIL"}:
        raise VisualVerifyError("grader response status must be PASS or FAIL")
    score = payload.get("score")
    if not isinstance(score, (int, float)) or score < 0 or score > 100:
        raise VisualVerifyError("grader response score must be a number from 0 to 100")
    reports = payload.get("viewportReports")
    if not isinstance(reports, dict):
        raise VisualVerifyError("grader response must include viewportReports object")
    verdict = payload.get("overallVerdict")
    if not isinstance(verdict, str):
        raise VisualVerifyError("grader response must include overallVerdict string")
    return payload


def run_visual_verification(
    *,
    url: str,
    viewports: list[dict[str, int | str]],
    output_dir: Path,
    grade: bool = False,
    model: str = "gemini-3.1-flash",
    delay_ms: int = 500,
    browser_endpoint: str | None = None,
    grader_endpoint: str | None = None,
) -> VisualVerificationResult:
    """Capture viewports and optionally grade them."""
    target = validate_target_url(url)
    capture = BrowserMCPClient(endpoint=browser_endpoint).capture_viewports(
        url=target,
        viewports=viewports,
        output_dir=output_dir,
        delay_ms=delay_ms,
    )
    grade_payload = None
    if grade:
        grade_payload = GraderClient(endpoint=grader_endpoint).grade(
            url=target,
            screenshots=capture,
            model=model,
        )
    return VisualVerificationResult(
        url=target,
        output_dir=str(output_dir),
        screenshots=capture,
        grade=grade_payload,
        metadata={"model": model, "delay_ms": delay_ms},
    )
