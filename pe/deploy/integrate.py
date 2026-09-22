"""Integration and Atomic Symlink Deployer (WB-3).

Corresponds to §4 and §16.8 anti-pattern #1 & #2 of okf-docs-workspace-deploy-v1.md.
Wraps prismatic.integrate.IntegratePhase, creates immutable versioned release, and performs atomic symlink swap.

Deploy-source decoupling (2026-09-22): the deploy source repo is NEVER
derived from the process working directory. ``deploy()`` requires an explicit
``source_repo`` and fail-fasts when it is unset, not a git checkout, missing
the ``prismatic/`` package, or when ``pr_sha`` does not resolve in it. The
version dir is built from a pristine detached ``git worktree`` of the exact
``pr_sha`` (the ``gateway_redeploy.py`` pattern) — never by copying a live
directory, which once turned the entire ``~/.prismatic`` home tree into a
deploy source and filled the disk.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from prismatic.integrate import IntegratePhase

logger = logging.getLogger(__name__)

#: Env var naming the git checkout deploys are built from. The receiver unit
#: sets this; it is never inferred from CWD.
SOURCE_REPO_ENV_VAR = "PRISMATIC_DEPLOY_SOURCE_REPO"

#: Env var (GB, float) for the deploy disk-space preflight.
MIN_FREE_GB_ENV_VAR = "PRISMATIC_DEPLOY_MIN_FREE_GB"
DEFAULT_MIN_FREE_GB = 5.0

#: Hard timeout for creating the pristine worktree.
WORKTREE_TIMEOUT_S = 300

#: Hard timeout for the explicit rsync fallback copy.
RSYNC_TIMEOUT_S = 900

#: Exclusions for the explicit rsync fallback. ``versions/`` and
#: ``releases/`` are load-bearing: they stop a deploy from copying the
#: release tree into itself if the source ever overlaps the home dir.
RSYNC_EXCLUDES = (
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "versions/",
    "releases/",
    "venv*/",
    ".venv/",
    "*.egg-info/",
)


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
        versions_dir: Path | None = None,
        release_symlink: Path | None = None,
        dry_run: bool = False,
    ):
        self.versions_dir = versions_dir or default_versions_dir()
        self.release_symlink = release_symlink or default_releases_symlink()
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # source validation (fail fast, before any copy)
    # ------------------------------------------------------------------

    @staticmethod
    def _run_git(*args: str, cwd: Path) -> tuple[bool, str]:
        """Run git; return (ok, stdout). Raises ValueError if git is missing."""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            raise ValueError(
                "git executable not found on PATH; cannot validate or build "
                "a deploy worktree"
            )
        return proc.returncode == 0, proc.stdout.strip()

    def _validate_source_repo(
        self, source_repo: Path | None, pr_sha: str
    ) -> tuple[Path, str]:
        """Validate the deploy source; return (resolved source, full sha).

        Raises ValueError with a clear message for every invalid input.
        Nothing is copied or created before this passes.
        """
        if source_repo is None:
            raise ValueError(
                f"deploy source_repo is required: set {SOURCE_REPO_ENV_VAR} to "
                "a git checkout of prismatic-engine (refusing to guess from "
                "the working directory)"
            )
        src = Path(source_repo).expanduser()
        if not src.is_dir():
            raise ValueError(f"deploy source_repo {src} is not a directory")
        ok, _ = self._run_git("rev-parse", "--git-dir", cwd=src)
        if not ok:
            raise ValueError(
                f"deploy source_repo {src} is not a git repository; refusing "
                "to deploy from a non-repo directory"
            )
        if not (src / "prismatic").is_dir():
            raise ValueError(
                f"deploy source_repo {src} has no prismatic/ package directory"
            )
        if not pr_sha:
            raise ValueError("pr_sha is required to build a deploy worktree")
        ok, full_sha = self._run_git(
            "rev-parse", "--verify", f"{pr_sha}^{{commit}}", cwd=src
        )
        if not ok or not full_sha:
            raise ValueError(
                f"pr_sha {pr_sha!r} is not a commit in {src}; refusing to deploy"
            )
        return src, full_sha

    @staticmethod
    def _guard_not_self_copy(src: Path, dest: Path) -> None:
        """Refuse when the version dir sits inside the deploy source."""
        s, d = src.resolve(), dest.resolve()
        if d == s or d.is_relative_to(s):
            raise ValueError(
                f"refusing deploy: version dir {d} is inside source {s} "
                "(recursive self-copy)"
            )

    def _preflight_disk(self) -> None:
        """Fail fast when the versions volume is too full to deploy onto."""
        raw = os.environ.get(MIN_FREE_GB_ENV_VAR, str(DEFAULT_MIN_FREE_GB))
        try:
            min_gb = float(raw)
        except ValueError:
            min_gb = DEFAULT_MIN_FREE_GB
        free = shutil.disk_usage(self.versions_dir).free
        need = int(min_gb * 1024**3)
        if free < need:
            raise ValueError(
                f"deploy preflight: only {free / 1024**3:.1f}G free on "
                f"{self.versions_dir}, need {min_gb}G; refusing to start"
            )

    # ------------------------------------------------------------------
    # deploy
    # ------------------------------------------------------------------

    def deploy(
        self,
        source_repo: Path | None,
        pr_sha: str,
        issue_id: str = "GRO-DEPLOY",
        branch: str = "main",
        copy_method: str = "worktree",
    ) -> tuple[bool, Path, str]:
        """Execute deploy: validate source, build pristine worktree, atomic symlink swap.

        Returns (success, version_dir_path, error_message).
        """
        sha_short = pr_sha[:12] if pr_sha else str(uuid.uuid4())[:8]
        target_version_dir = self.versions_dir / f"prismatic-engine-{sha_short}"

        if self.dry_run:
            logger.info("DRY RUN: would deploy to %s", target_version_dir)
            return True, target_version_dir, ""

        try:
            # 0. Fail fast on a bad source, before touching the disk.
            src, full_sha = self._validate_source_repo(source_repo, pr_sha)
            self._preflight_disk()

            # 0b. Wrap canonical IntegratePhase runner
            try:
                phase = IntegratePhase(
                    issue_id=issue_id,
                    branch=branch,
                    target_branch="main",
                    repo_path=src,
                    skip_tests=True,
                )
                self.last_integration_manifest = phase.manifest
            except Exception as _exc:
                logger.warning("IntegratePhase wrap warning: %s", _exc)
                self.last_integration_manifest = None

            # 1-2. Pristine detached worktree of exactly pr_sha (never the
            # live checkout, never a live directory tree).
            self._copy_release_files(
                src, target_version_dir, full_sha, copy_method=copy_method
            )

            # 3. Perform atomic symlink swap
            self._atomic_symlink_swap(target_version_dir, self.release_symlink)

            logger.info("Deploy successful: %s → %s", self.release_symlink, target_version_dir)
            return True, target_version_dir, ""
        except Exception as exc:
            logger.error("Deploy failed for SHA %s: %s", pr_sha, exc)
            return False, target_version_dir, str(exc)

    def _copy_release_files(
        self,
        src: Path,
        dest: Path,
        pr_sha: str,
        copy_method: str = "worktree",
    ) -> None:
        """Build the immutable version dir from the source repo."""
        self._guard_not_self_copy(src, dest)
        if copy_method == "rsync":
            self._rsync_copy(src, dest)
            return
        if copy_method != "worktree":
            raise ValueError(f"unknown copy_method {copy_method!r}")
        self._worktree_copy(src, dest, pr_sha)

    def _worktree_copy(self, src: Path, dest: Path, pr_sha: str) -> None:
        """`git worktree add --detach <dest> <pr_sha>`; dest becomes a plain snapshot."""
        if dest.exists():
            shutil.rmtree(dest)
        try:
            subprocess.run(
                ["git", "-C", str(src), "worktree", "add", "--detach",
                 str(dest), pr_sha],
                check=True,
                capture_output=True,
                text=True,
                timeout=WORKTREE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"git worktree add timed out after {WORKTREE_TIMEOUT_S}s: {exc}"
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git worktree add failed: {(exc.stderr or '').strip()[-500:]}"
            )
        except Exception:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            raise
        try:
            # The version dir must be a plain immutable snapshot, not a live
            # worktree: drop the .git pointer file, then prune the orphaned
            # registration from the source repo (best-effort).
            gitfile = dest / ".git"
            if gitfile.is_file() or gitfile.is_symlink():
                gitfile.unlink()
        finally:
            try:
                subprocess.run(
                    ["git", "-C", str(src), "worktree", "prune"],
                    capture_output=True,
                    timeout=60,
                )
            except Exception:
                pass

    def _rsync_copy(self, src: Path, dest: Path) -> None:
        """Explicit-opt-in rsync copy with timeout and self-copy exclusions."""
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        argv = (
            ["rsync", "-a"]
            + [f"--exclude={e}" for e in RSYNC_EXCLUDES]
            + [f"{src}/", f"{dest}/"]
        )
        try:
            subprocess.run(
                argv,
                check=True,
                capture_output=True,
                text=True,
                timeout=RSYNC_TIMEOUT_S,
            )
            return
        except FileNotFoundError:
            logger.warning("rsync unavailable, falling back to shutil copy")
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"rsync copy timed out after {RSYNC_TIMEOUT_S}s: {exc}"
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"rsync copy failed: {(exc.stderr or '').strip()[-500:]}"
            )
        # Fallback copy (rsync binary missing only)
        skip = {e.rstrip("/") for e in RSYNC_EXCLUDES if not e.startswith("*")}
        for item in src.iterdir():
            if item.name in skip:
                continue
            d_item = dest / item.name
            if item.is_dir():
                shutil.copytree(item, d_item, dirs_exist_ok=True)
            else:
                shutil.copy2(item, d_item)

    @classmethod
    def _atomic_symlink_swap(cls, target_dir: Path, symlink_path: Path) -> None:
        """Atomic symlink swap using temporary symlink or cross-platform fallback."""
        temp_symlink = symlink_path.parent / f".tmp_symlink_{uuid.uuid4().hex[:8]}"
        try:
            if temp_symlink.exists() or temp_symlink.is_symlink():
                temp_symlink.unlink()

            try:
                os.symlink(target_dir, temp_symlink)
                os.replace(temp_symlink, symlink_path)
            except OSError:
                # Windows non-admin fallback (WinError 1314)
                if symlink_path.exists() or symlink_path.is_symlink():
                    if symlink_path.is_dir() and not symlink_path.is_symlink():
                        shutil.rmtree(symlink_path)
                    else:
                        symlink_path.unlink()
                shutil.copytree(target_dir, symlink_path)
        finally:
            if temp_symlink.exists() or temp_symlink.is_symlink():
                try:
                    temp_symlink.unlink()
                except OSError:
                    pass
