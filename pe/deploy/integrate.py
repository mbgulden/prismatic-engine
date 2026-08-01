"""Integration and Atomic Symlink Deployer (WB-3).

Corresponds to §4 and §16.8 anti-pattern #1 & #2 of okf-docs-workspace-deploy-v1.md.
Wraps prismatic.integrate.IntegratePhase, creates immutable versioned release, and performs atomic symlink swap.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

from prismatic.integrate import IntegratePhase, IntegrationManifest

logger = logging.getLogger(__name__)


def default_versions_dir() -> Path:
    """Resolve base versioned releases directory (~/.prismatic/versions)."""
    env_dir = os.environ.get("PRISMATIC_VERSIONS_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    p = Path("~/.prismatic/versions").expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_releases_symlink() -> Path:
    """Resolve release symlink path (~/.prismatic/releases/prismatic-engine)."""
    env_path = os.environ.get("PRISMATIC_RELEASE_SYMLINK")
    if env_path:
        return Path(env_path).expanduser()
    p = Path("~/.prismatic/releases/prismatic-engine").expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class AtomicDeployRunner:
    """Executes atomic deployments using versioned release directories and symlink swap."""

    def __init__(
        self,
        versions_dir: Optional[Path] = None,
        release_symlink: Optional[Path] = None,
        dry_run: bool = False,
    ):
        self.versions_dir = versions_dir or default_versions_dir()
        self.release_symlink = release_symlink or default_releases_symlink()
        self.dry_run = dry_run

    def deploy(
        self,
        source_repo: Path,
        pr_sha: str,
        issue_id: str = "GRO-DEPLOY",
        branch: str = "main",
    ) -> tuple[bool, Path, str]:
        """Execute deploy: rsync to versioned dir, then atomic symlink swap.

        Returns (success, version_dir_path, error_message).
        """
        sha_short = pr_sha[:12] if pr_sha else str(uuid.uuid4())[:8]
        target_version_dir = self.versions_dir / f"prismatic-engine-{sha_short}"

        if self.dry_run:
            logger.info("DRY RUN: would deploy to %s", target_version_dir)
            return True, target_version_dir, ""

        try:
            # 1. Create immutable versioned directory (never mutate in-place!)
            if target_version_dir.exists():
                shutil.rmtree(target_version_dir)
            target_version_dir.mkdir(parents=True, exist_ok=True)

            # 2. Copy release files to versioned directory
            self._copy_release_files(source_repo, target_version_dir)

            # 3. Perform atomic symlink swap
            self._atomic_symlink_swap(target_version_dir, self.release_symlink)

            logger.info("Deploy successful: %s → %s", self.release_symlink, target_version_dir)
            return True, target_version_dir, ""
        except Exception as exc:
            logger.error("Deploy failed for SHA %s: %s", pr_sha, exc)
            return False, target_version_dir, str(exc)

    def _copy_release_files(self, src: Path, dest: Path) -> None:
        """Copy source repository files to versioned release directory."""
        # Use rsync if available, fallback to shutil.copytree
        try:
            subprocess.run(
                [
                    "rsync", "-av", "--exclude=.git", "--exclude=node_modules",
                    "--exclude=__pycache__", f"{src}/", f"{dest}/"
                ],
                check=True,
                capture_output=True,
            )
        except Exception:
            # Fallback copy
            for item in src.iterdir():
                if item.name in (".git", "node_modules", "__pycache__", ".pytest_cache"):
                    continue
                d_item = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, d_item, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, d_item)

    @classmethod
    def _atomic_symlink_swap(cls, target_dir: Path, symlink_path: Path) -> None:
        """Atomic symlink swap using temporary symlink and atomic rename/replace."""
        temp_symlink = symlink_path.parent / f".tmp_symlink_{uuid.uuid4().hex[:8]}"
        try:
            # Create temp symlink pointing to target_dir
            if temp_symlink.exists() or temp_symlink.is_symlink():
                temp_symlink.unlink()

            os.symlink(target_dir, temp_symlink)

            # Atomic replace
            os.replace(temp_symlink, symlink_path)
        finally:
            if temp_symlink.exists() or temp_symlink.is_symlink():
                try:
                    temp_symlink.unlink()
                except OSError:
                    pass
