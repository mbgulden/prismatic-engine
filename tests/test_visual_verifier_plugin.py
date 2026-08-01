from __future__ import annotations

import base64
import json
import sys
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "visual-verifier"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from prismatic.interface.plugin import PluginContext  # noqa: E402
from visual_verifier.cli import main as visual_verify_main  # noqa: E402
from visual_verifier.core import VisualVerifier, parse_viewport  # noqa: E402
from visual_verifier.plugin import VisualVerifierPlugin  # noqa: E402


def png_bytes(width: int = 640, height: int = 480) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00" * 12
    )


def test_grade_screenshot_uses_fallback_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    verifier = VisualVerifier()
    encoded = base64.b64encode(png_bytes()).decode("ascii")

    result = verifier.grade_screenshot(encoded, checks=["spacing", "alignment"])

    assert result["fallback_used"] is True
    assert result["pass"] is True
    assert result["dimensions"] == [640, 480]
    assert set(result["grades"]) == {"spacing", "alignment"}


def test_grade_screenshot_cache_reuses_result(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    verifier = VisualVerifier()
    encoded = base64.b64encode(png_bytes()).decode("ascii")

    first = verifier.grade_screenshot(encoded)
    second = verifier.grade_screenshot(encoded)

    assert first["fallback_used"] is True
    assert second["cached"] is True


def test_compare_images_reports_dimension_mismatch():
    verifier = VisualVerifier()
    image_a = base64.b64encode(png_bytes(640, 480)).decode("ascii")
    image_b = base64.b64encode(png_bytes(320, 240)).decode("ascii")

    result = verifier.compare_images(image_a, image_b, criteria=["brand-consistency"])

    assert result["overall_pass"] is False
    assert result["image_a"]["dimensions"] == [640, 480]
    assert result["image_b"]["dimensions"] == [320, 240]
    assert result["failures"]


def test_plugin_registers_required_visual_tools():
    plugin = VisualVerifierPlugin()
    plugin.on_init(PluginContext(config={}, db_connection=None, state_dir="/tmp"))

    tool_names = {tool["name"] for tool in plugin.register_tools()}

    assert {"verify_url", "verify_file", "grade_screenshot", "compare_images"}.issubset(
        tool_names
    )


def test_parse_viewport_contract():
    assert parse_viewport("mobile:375x667") == {
        "name": "mobile",
        "width": 375,
        "height": 667,
    }


def test_cli_returns_json_for_local_file_with_stubbed_capture(monkeypatch, tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<html><body>Hello</body></html>", encoding="utf-8")
    shot = tmp_path / "shot.png"
    shot.write_bytes(png_bytes())

    def fake_capture(self, target, viewports=None):
        return [{"name": "desktop", "path": str(shot), "width": 1200, "height": 800}]

    monkeypatch.setattr(VisualVerifier, "capture_viewports", fake_capture)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    stdout = StringIO()
    monkeypatch.setattr(sys, "__stdout__", stdout)

    code = visual_verify_main([str(html), "--file"])
    payload = json.loads(stdout.getvalue())

    assert code == 0
    assert payload["overall_pass"] is True
    assert payload["viewport_results"][0]["viewport"] == "desktop"
    assert payload["viewport_results"][0]["fallback_used"] is True
