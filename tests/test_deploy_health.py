"""Unit tests for pe.deploy.health PostDeployHealthChecker (WB-5).
"""

from pathlib import Path
from pe.deploy.health import PostDeployHealthChecker


def test_health_checker_filesystem(tmp_path):
    version_dir = tmp_path / "version"
    version_dir.mkdir()
    (version_dir / "prismatic").mkdir()

    symlink_path = tmp_path / "release_symlink"
    import os, shutil
    try:
        os.symlink(version_dir, symlink_path)
    except OSError:
        shutil.copytree(version_dir, symlink_path)

    checker = PostDeployHealthChecker()
    res = checker.check(version_dir=version_dir, release_symlink=symlink_path)

    assert res["checks"]["symlink_exists"] is True
    assert res["checks"]["version_dir_valid"] is True
    assert res["passed"] is True


def test_health_checker_invalid_version_dir(tmp_path):
    version_dir = tmp_path / "invalid_dir"
    symlink_path = tmp_path / "symlink"

    checker = PostDeployHealthChecker()
    res = checker.check(version_dir=version_dir, release_symlink=symlink_path)

    assert res["checks"]["version_dir_valid"] is False
import http.server
import threading


def _valid_release(tmp_path):
    version_dir = tmp_path / "version"
    version_dir.mkdir()
    (version_dir / "prismatic").mkdir()
    import os
    symlink_path = tmp_path / "release_symlink"
    os.symlink(version_dir, symlink_path)
    return version_dir, symlink_path


def _dead_checker(tmp_path):
    # Nothing listens on port 9: both HTTP checks fail.
    return PostDeployHealthChecker(base_url="http://localhost:9", timeout_seconds=1)


def test_require_http_fails_when_gateway_down(tmp_path):
    """2026-09-28: the deploy path must not report success with the gateway down."""
    version_dir, symlink_path = _valid_release(tmp_path)
    res = _dead_checker(tmp_path).check(
        version_dir=version_dir, release_symlink=symlink_path, require_http=True
    )
    assert res["checks"]["http_gateway_health"] is False
    assert res["checks"]["http_gateway_skills"] is False
    assert res["passed"] is False
    assert "http_required_error" in res["details"]


def test_default_still_lenient_when_gateway_down(tmp_path):
    """Default (offline-test) behavior is unchanged: HTTP is best-effort."""
    version_dir, symlink_path = _valid_release(tmp_path)
    res = _dead_checker(tmp_path).check(
        version_dir=version_dir, release_symlink=symlink_path
    )
    assert res["passed"] is True


def test_strict_env_var_still_requires_http(tmp_path, monkeypatch):
    """STRICT_HTTP_HEALTH keeps its pre-existing meaning."""
    monkeypatch.setenv("STRICT_HTTP_HEALTH", "1")
    version_dir, symlink_path = _valid_release(tmp_path)
    res = _dead_checker(tmp_path).check(
        version_dir=version_dir, release_symlink=symlink_path
    )
    assert res["passed"] is False


def test_require_http_passes_when_gateway_up(tmp_path):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        version_dir, symlink_path = _valid_release(tmp_path)
        checker = PostDeployHealthChecker(base_url=f"http://127.0.0.1:{port}")
        res = checker.check(
            version_dir=version_dir, release_symlink=symlink_path, require_http=True
        )
        assert res["passed"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
