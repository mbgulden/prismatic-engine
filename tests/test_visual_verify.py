# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from prismatic.quality.visual_verify import (
    ViewportSpec,
    capture_with_browser_mcp,
    main,
    run_visual_verify,
)


def _png_fixture(path: Path, size: int = 2048) -> Path:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + (b"0" * size))
    return path


def test_viewport_spec_parse():
    viewport = ViewportSpec.parse("mobile:390x844")
    assert viewport.name == "mobile"
    assert viewport.width == 390
    assert viewport.height == 844
    assert viewport.to_dict() == {"name": "mobile", "width": 390, "height": 844}


def test_local_visual_verify_passes_for_meaningful_screenshot(tmp_path):
    screenshot = _png_fixture(tmp_path / "home.png")
    result = run_visual_verify(
        screenshot_paths=[f"desktop:{screenshot}"],
        required_text=[],
        min_score=0.8,
    )
    assert result.passed is True
    assert result.score == 1.0
    assert result.screenshots[0].viewport == "desktop"
    assert "visual verification passed" in result.reason


def test_local_visual_verify_fails_for_missing_screenshot(tmp_path):
    result = run_visual_verify(screenshot_paths=[str(tmp_path / "missing.png")])
    assert result.passed is False
    assert any(f.check == "screenshot_exists" for f in result.findings)


def test_required_text_missing_fails_even_when_screenshot_exists(tmp_path):
    screenshot = _png_fixture(tmp_path / "landing.png")
    result = run_visual_verify(
        screenshot_paths=[str(screenshot)], required_text=["Book Now"]
    )
    assert result.passed is False
    assert any(
        f.check == "required_text" and f.status == "failed" for f in result.findings
    )


def test_browser_mcp_capture_decodes_base64_screenshot(tmp_path):
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"1" * 2048).decode("ascii")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            assert payload["action"] == "capture_screenshots"
            assert payload["viewports"][0]["name"] == "desktop"
            body = json.dumps(
                {
                    "screenshots": [
                        {
                            "viewport": "desktop",
                            "image_base64": encoded,
                            "format": "png",
                            "width": 1440,
                            "height": 900,
                            "text": "Hero section loaded",
                        }
                    ]
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        shots = capture_with_browser_mcp(
            endpoint=f"http://127.0.0.1:{server.server_port}",
            url="https://example.test",
            viewports=[ViewportSpec.parse("desktop:1440x900")],
            output_dir=tmp_path,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert len(shots) == 1
    assert shots[0].exists()
    assert shots[0].byte_size() > 1024
    assert shots[0].text == "Hero section loaded"


def test_cli_writes_report_and_returns_success(tmp_path, capsys):
    screenshot = _png_fixture(tmp_path / "ok.png")
    report = tmp_path / "report.md"
    code = main(["--screenshot", str(screenshot), "--report", str(report)])
    assert code == 0
    assert report.exists()
    assert "Visual verification" in report.read_text()
    assert "PASS" in capsys.readouterr().out


def test_cli_json_failure_for_no_input(capsys):
    code = main(["--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["findings"][0]["check"] == "input"
