from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .core import VisualVerifier

mcp = FastMCP("hermes-plugin-visual-verifier")
verifier = VisualVerifier(
    output_dir=os.environ.get("VISUAL_VERIFIER_OUTPUT_DIR"),
    require_browsermcp=os.environ.get("VISUAL_VERIFIER_REQUIRE_BROWSERMCP") == "1",
)


@mcp.tool()
def verify_url(
    url: str,
    viewports: list[dict[str, Any]] | None = None,
    checks: list[str] | None = None,
) -> dict[str, Any]:
    """Render a URL, capture viewport screenshots, and grade them."""
    return verifier.verify_url(url, viewports=viewports, checks=checks)


@mcp.tool()
def verify_file(
    path: str, viewports: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Render a local HTML file, capture viewport screenshots, and grade them."""
    return verifier.verify_file(path, viewports=viewports)


@mcp.tool()
def grade_screenshot(
    screenshot_b64: str, checks: list[str] | None = None
) -> dict[str, Any]:
    """Grade a single base64-encoded PNG screenshot."""
    return verifier.grade_screenshot(screenshot_b64, checks=checks)


@mcp.tool()
def compare_images(
    image_a: str, image_b: str, criteria: list[str] | str | None = None
) -> dict[str, Any]:
    """Compare two image paths or base64 payloads."""
    return verifier.compare_images(image_a, image_b, criteria=criteria)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
