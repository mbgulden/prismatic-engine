"""Automated Playwright Visual Audit & Mobile Viewport Assertion Test.

Spawns the real Gateway server on port 9005, connects via Playwright,
verifies Desktop (1920x1080), Tablet (768x1024), and Mobile (375x812) viewports,
and asserts the 375px zero horizontal-overflow invariant (scrollWidth <= 375).
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_playwright_visual_audit_against_live_gateway(tmp_path):
    """Run Playwright visual audit against live Gateway server and verify 375px viewport."""
    node_bin = shutil.which("node") or shutil.which("node.exe")
    if not node_bin:
        pytest.skip("Node.js executable 'node' not found in system PATH")

    node_script = (
        Path(__file__).parent.parent / "scripts" / "visual_audit_playwright.js"
    )
    if not node_script.exists():
        pytest.skip("visual_audit_playwright.js not found")

    port = 9088
    repo_root = str(Path(__file__).parent.parent)
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_root
    env["PRISMATIC_PORT"] = str(port)
    env["PRISMATIC_WS_PORT"] = "8798"
    env["PRISMATIC_TEST_URL"] = f"http://127.0.0.1:{port}"

    # Start Gateway server on port 9005
    proc = subprocess.Popen(
        [sys.executable, "-m", "prismatic.gateway.server", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait up to 15s for server to start listening
        server_ready = False
        for _ in range(30):
            time.sleep(0.5)
            if proc.poll() is not None:
                _, stderr = proc.communicate()
                pytest.fail(f"Gateway server failed to start: {stderr.decode()}")
            try:
                import urllib.request

                resp = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=1
                )
                if resp.status == 200:
                    server_ready = True
                    break
            except Exception:
                pass

        assert server_ready, f"Gateway server did not become ready on port {port}"

        # Run Playwright audit script
        rel_script = "scripts/visual_audit_playwright.js"
        res = subprocess.run(
            [node_bin, rel_script, f"http://127.0.0.1:{port}"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert res.returncode == 0, (
            f"Playwright audit failed:\nStdout: {res.stdout}\nStderr: {res.stderr}"
        )
        assert (
            "Mobile 375px zero-overflow invariant passed" in res.stdout
            or "scrollWidth=375" in res.stdout
        ), f"Horizontal overflow detected on mobile viewport:\n{res.stdout}"

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
