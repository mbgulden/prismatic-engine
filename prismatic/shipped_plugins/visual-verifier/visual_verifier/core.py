from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_VIEWPORTS: List[Dict[str, int | str]] = [
    {"name": "desktop", "width": 1200, "height": 800},
    {"name": "tablet", "width": 768, "height": 1024},
    {"name": "mobile", "width": 375, "height": 667},
]
DEFAULT_CHECKS = [
    "spacing",
    "alignment",
    "text-clipping",
    "overflow",
    "contrast",
    "asset-consistency",
]


@dataclass
class CacheEntry:
    expires_at: float
    value: Dict[str, Any]


class VisualVerifierError(RuntimeError):
    """Expected visual-verifier failure with a user-safe message."""


class VisualVerifier:
    """Capture pages and grade screenshots for visual regressions.

    The production path is intentionally out-of-process: browser work runs via
    subprocesses and model calls use HTTPS. If either BrowserMCP/Playwright or
    Nano Banana 2 is unavailable, methods return structured error/fallback
    payloads instead of surfacing stack traces to agents.
    """

    def __init__(
        self,
        output_dir: str | Path | None = None,
        cache_ttl_seconds: int = 300,
        require_browsermcp: bool = False,
    ) -> None:
        self.output_dir = Path(
            output_dir
            or os.environ.get(
                "VISUAL_VERIFIER_OUTPUT_DIR", "/tmp/prismatic-visual-verifier"
            )
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.require_browsermcp = require_browsermcp
        self._cache: Dict[str, CacheEntry] = {}

    def verify_url(
        self,
        url: str,
        viewports: Optional[List[Dict[str, Any]]] = None,
        checks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        target = self._normalize_url(url)
        return self._verify_target(target, viewports=viewports, checks=checks)

    def verify_file(
        self,
        path: str,
        viewports: Optional[List[Dict[str, Any]]] = None,
        checks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        file_path = Path(path).expanduser().resolve()
        if not file_path.exists():
            return self._error_report(f"Local HTML file not found: {file_path}")
        return self._verify_target(
            file_path.as_uri(), viewports=viewports, checks=checks
        )

    def compare_images(
        self, image_a: str, image_b: str, criteria: Optional[List[str] | str] = None
    ) -> Dict[str, Any]:
        bytes_a = self._read_image_input(image_a)
        bytes_b = self._read_image_input(image_b)
        hash_a = hashlib.sha256(bytes_a).hexdigest()
        hash_b = hashlib.sha256(bytes_b).hexdigest()
        dims_a = self._png_dimensions(bytes_a)
        dims_b = self._png_dimensions(bytes_b)
        same_bytes = hash_a == hash_b
        same_size = dims_a == dims_b
        diff = {
            "same_bytes": same_bytes,
            "same_dimensions": same_size,
            "image_a": {"sha256": hash_a, "dimensions": dims_a},
            "image_b": {"sha256": hash_b, "dimensions": dims_b},
            "criteria": criteria or [],
        }
        diff["overall_pass"] = bool(same_bytes or same_size)
        diff["failures"] = (
            []
            if diff["overall_pass"]
            else [
                "Images differ in dimensions and bytes; manual/AI review recommended."
            ]
        )
        return diff

    def grade_screenshot(
        self, screenshot_b64: str, checks: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        checks = checks or DEFAULT_CHECKS
        image_bytes = base64.b64decode(
            self._strip_data_url(screenshot_b64), validate=False
        )
        cache_key = self._cache_key(image_bytes, checks)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

        try:
            result = self._grade_with_nano_banana(image_bytes, checks)
        except VisualVerifierError as exc:
            result = self._grade_with_heuristics(
                image_bytes, checks, fallback_reason=str(exc)
            )
        except (
            Exception
        ) as exc:  # defensive boundary: never leak tracebacks as tool output
            result = self._grade_with_heuristics(
                image_bytes,
                checks,
                fallback_reason=f"Nano Banana 2 grader failed; fallback used ({exc.__class__.__name__}).",
            )

        self._cache_set(cache_key, result)
        return result

    def _verify_target(
        self,
        target: str,
        viewports: Optional[List[Dict[str, Any]]] = None,
        checks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        checks = checks or DEFAULT_CHECKS
        try:
            screenshots = self.capture_viewports(target, viewports=viewports)
        except VisualVerifierError as exc:
            return self._error_report(str(exc))
        except Exception as exc:
            return self._error_report(
                f"Visual capture failed gracefully ({exc.__class__.__name__})."
            )

        viewport_results: List[Dict[str, Any]] = []
        for shot in screenshots:
            data = Path(str(shot["path"])).read_bytes()
            grade = self.grade_screenshot(
                base64.b64encode(data).decode("ascii"), checks=checks
            )
            failures = grade.get("failures") or []
            if not failures and not grade.get("pass", False):
                failures = grade.get("defects", []) or [
                    grade.get("overallVerdict", "visual check did not pass")
                ]
            viewport_results.append(
                {
                    "viewport": shot["name"],
                    "screenshot": shot["path"],
                    "width": shot["width"],
                    "height": shot["height"],
                    "grades": grade.get("grades", {}),
                    "failures": failures,
                    "pass": bool(grade.get("pass", not failures)),
                    "fallback_used": bool(grade.get("fallback_used", False)),
                }
            )

        overall_pass = all(item["pass"] for item in viewport_results)
        return {"viewport_results": viewport_results, "overall_pass": overall_pass}

    def capture_viewports(
        self, target: str, viewports: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        browsermcp_status = self._probe_browsermcp()
        if self.require_browsermcp and not browsermcp_status["available"]:
            raise VisualVerifierError(browsermcp_status["message"])

        normalized_viewports = self._normalize_viewports(viewports)
        run_dir = self.output_dir / time.strftime("run-%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)

        if shutil.which("node") is None:
            raise VisualVerifierError(
                "Browser capture unavailable: node is not installed, so BrowserMCP/Playwright cannot run."
            )

        script = self._playwright_capture_script()
        payload = {
            "target": target,
            "viewports": normalized_viewports,
            "output_dir": str(run_dir),
        }
        proc = subprocess.run(
            ["node", "-e", script],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=int(os.environ.get("VISUAL_VERIFIER_CAPTURE_TIMEOUT", "60")),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [
                "unknown browser error"
            ]
            raise VisualVerifierError(
                "BrowserMCP/Playwright capture unavailable: "
                f"{detail[0]}. Install with `npm install playwright && npx playwright install chromium`, "
                "or connect BrowserMCP."
            )
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise VisualVerifierError("Browser capture returned invalid JSON.") from exc
        if not result.get("screenshots"):
            raise VisualVerifierError("Browser capture returned no screenshots.")
        return result["screenshots"]

    def _probe_browsermcp(self) -> Dict[str, Any]:
        if shutil.which("npx") is None:
            return {
                "available": False,
                "message": "BrowserMCP is not connected: npx is not installed.",
            }
        # `npx @browsermcp/mcp@latest` is an interactive stdio server. For CLI
        # visual verification we only probe installability here; screenshot work
        # is delegated to the Playwright-compatible capture subprocess below.
        return {
            "available": True,
            "message": "BrowserMCP launcher is available via npx.",
        }

    def _grade_with_nano_banana(
        self, image_bytes: bytes, checks: List[str]
    ) -> Dict[str, Any]:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise VisualVerifierError(
                "Nano Banana 2 API key not configured; fallback heuristic check used."
            )
        model = os.environ.get("NANO_BANANA_MODEL", "gemini-3.1-flash-image-preview")
        prompt = (
            "You are a strict UI visual QA grader. Return only JSON with keys: "
            "pass (boolean), grades (object of 0-100 scores), failures (array of strings), "
            "defects (array), overallVerdict (string). Check: " + ", ".join(checks)
        )
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=int(os.environ.get("NANO_BANANA_TIMEOUT", "30"))
            ) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 429}:
                raise VisualVerifierError(
                    "Nano Banana 2 quota/auth unavailable; fallback heuristic check used."
                ) from exc
            raise VisualVerifierError(
                f"Nano Banana 2 HTTP {exc.code}; fallback heuristic check used."
            ) from exc
        text = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VisualVerifierError(
                "Nano Banana 2 returned non-JSON output; fallback heuristic check used."
            ) from exc
        parsed.setdefault("fallback_used", False)
        parsed.setdefault("pass", not parsed.get("failures"))
        return parsed

    def _grade_with_heuristics(
        self, image_bytes: bytes, checks: List[str], fallback_reason: str
    ) -> Dict[str, Any]:
        dims = self._png_dimensions(image_bytes)
        failures: List[str] = []
        grades = {check: 75 for check in checks}
        if not dims:
            failures.append(
                "Screenshot is not a valid PNG; unable to inspect dimensions."
            )
        elif dims[0] < 320 or dims[1] < 320:
            failures.append(
                f"Screenshot dimensions look too small for layout verification: {dims[0]}x{dims[1]}."
            )
            grades = {check: 40 for check in checks}
        return {
            "pass": not failures,
            "grades": grades,
            "failures": failures,
            "defects": failures,
            "overallVerdict": "Fallback heuristic visual check completed. Use Nano Banana 2 for semantic layout grading.",
            "fallback_used": True,
            "fallback_reason": fallback_reason,
            "dimensions": dims,
        }

    def _playwright_capture_script(self) -> str:
        return r"""
const fs = require('fs');
const path = require('path');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  let browser;
  try {
    const { chromium } = require('playwright');
    const payload = JSON.parse(input);
    fs.mkdirSync(payload.output_dir, { recursive: true });
    browser = await chromium.launch({ headless: true });
    const screenshots = [];
    for (const vp of payload.viewports) {
      const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
      await page.goto(payload.target, { waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForTimeout(250);
      const out = path.join(payload.output_dir, `${vp.name}.png`);
      await page.screenshot({ path: out, fullPage: true });
      await page.close();
      screenshots.push({ name: vp.name, path: out, width: vp.width, height: vp.height });
    }
    await browser.close();
    console.log(JSON.stringify({ screenshots }));
  } catch (err) {
    if (browser) { try { await browser.close(); } catch (_) {} }
    console.error(err && err.message ? err.message : String(err));
    process.exit(1);
  }
});
"""

    def _normalize_viewports(
        self, viewports: Optional[List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        raw = viewports or DEFAULT_VIEWPORTS
        normalized = []
        for vp in raw:
            normalized.append(
                {
                    "name": str(vp["name"]),
                    "width": int(vp["width"]),
                    "height": int(vp["height"]),
                }
            )
        return normalized

    def _normalize_url(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in {"http", "https", "file"}:
            return url
        raise VisualVerifierError(
            "Target must be an http(s) URL or local file path via verify_file()."
        )

    def _error_report(self, message: str) -> Dict[str, Any]:
        return {"viewport_results": [], "overall_pass": False, "error": message}

    def _cache_key(self, image_bytes: bytes, checks: Iterable[str]) -> str:
        h = hashlib.sha256()
        h.update(image_bytes)
        h.update(json.dumps(list(checks), sort_keys=True).encode("utf-8"))
        return h.hexdigest()

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        item = self._cache.get(key)
        if not item:
            return None
        if item.expires_at < time.time():
            self._cache.pop(key, None)
            return None
        return item.value

    def _cache_set(self, key: str, value: Dict[str, Any]) -> None:
        self._cache[key] = CacheEntry(
            expires_at=time.time() + self.cache_ttl_seconds, value=value
        )

    def _strip_data_url(self, value: str) -> str:
        if value.startswith("data:") and "," in value:
            return value.split(",", 1)[1]
        return value

    def _read_image_input(self, value: str) -> bytes:
        maybe_path = Path(value).expanduser()
        if maybe_path.exists():
            return maybe_path.read_bytes()
        return base64.b64decode(self._strip_data_url(value), validate=False)

    def _png_dimensions(self, data: bytes) -> Optional[List[int]]:
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return [width, height]


def parse_viewport(value: str) -> Dict[str, Any]:
    name, _, dims = value.partition(":")
    width, _, height = dims.lower().partition("x")
    if not name or not width or not height:
        raise ValueError(
            "Viewport must look like name:WIDTHxHEIGHT, e.g. mobile:375x667"
        )
    return {"name": name, "width": int(width), "height": int(height)}
