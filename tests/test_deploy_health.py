"""Unit tests for pe.deploy.health PostDeployHealthChecker (WB-5).
"""

from pe.deploy.health import PostDeployHealthChecker


def test_health_checker_filesystem(tmp_path):
    version_dir = tmp_path / "version"
    version_dir.mkdir()
    (version_dir / "prismatic").mkdir()

    symlink_path = tmp_path / "release_symlink"
    import os
    os.symlink(version_dir, symlink_path)

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
