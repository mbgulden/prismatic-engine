"""Real atomic gateway redeploy for the post-merge deploy pipeline.

A signed POST to the deploy receiver used to only rsync the receiver's own
checkout and flip the receiver's bookkeeping symlink -- the RUNNING gateway
(``~/.prismatic/current`` + ``~/.prismatic/venv_current`` via
``prismatic-gateway.service``) was never touched. This module closes that loop:
a merge to main now fully redeploys the live gateway.

Deploy sequence (all-or-nothing):
  1. Acquire an exclusive deploy lock (overlapping merges serialize; a stuck
     lock times out loudly instead of interleaving).
  2. ``git fetch origin main`` in the control checkout, then verify the
     payload's ``pr_sha`` is a real commit and an ancestor of ``origin/main``.
     Arbitrary SHAs from payloads are refused.
  3. Skip as superseded if the live release already contains ``pr_sha``
     (two rapid merges: the newer one wins, the older one stands down).
  4. Create a pristine detached worktree of ``pr_sha`` (never the working
     checkout, which may carry local tweaks).
  5. Build a wheel from the worktree.
  6. Create a fresh venv and ``pip install`` the wheel with the gateway extras.
  7. Stage the full source tree into a new immutable release dir.
  8. Atomically flip ``venv_current`` then ``current`` to the new release.
  9. ``sudo systemctl restart prismatic-gateway.service``.
 10. Strict health check: service active, smoke import with the NEW venv's
     python, HTTP 200s from the gateway's own endpoints (polled, not
     best-effort).

Rollback: if ANY step fails, or the health check fails, both symlinks are
flipped back to the previous release, the service is restarted, and recovery
is verified. The deploy is recorded as FAILED loudly. The gateway is never
left down or half-flipped.

Stdlib only -- no prismatic imports -- so this module stays importable in
minimal environments and unit-testable with fakes.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_SERVICE = "prismatic-gateway.service"
DEFAULT_GATEWAY_PORT = 9000
DEFAULT_EXTRAS = "gateway,primitives,verification"
LOCK_NAME = "gateway-deploy.lock"
STATE_NAME = "last-gateway-deploy.json"
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class DeployRefused(Exception):
    """The requested deploy was refused (bad SHA, not on origin/main)."""


class DeployFailed(Exception):
    """A deploy step failed (build, install, flip, restart, health)."""


class DeployBusy(Exception):
    """Another deploy holds the lock past the timeout."""


@dataclass
class GatewayDeployResult:
    """Outcome of one atomic gateway redeploy attempt."""

    success: bool = False
    skipped: bool = False
    pr_sha: str = ""
    version_dir: str = ""
    venv_dir: str = ""
    previous_version_dir: str = ""
    previous_venv_dir: str = ""
    health: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    rolled_back: bool = False
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_http_get(url: str, timeout: int = 5) -> tuple[bool, str]:
    """Single HTTP GET; returns (ok, detail). Never raises."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Prismatic-Gateway-Health/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            if 200 <= code < 400:
                return True, f"HTTP {code}"
            return False, f"HTTP status {code}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


class GatewayRedeployer:
    """Performs real atomic redeploys of the live Prismatic gateway."""

    def __init__(
        self,
        home: Path | str | None = None,
        run: Any | None = None,
        http_get: Any | None = None,
        lock_timeout_s: int = 1800,
        service: str | None = None,
        port: int | None = None,
        extras: str | None = None,
        systemctl_bin: str = "/usr/bin/systemctl",
    ):
        home_p = Path(home).expanduser() if home else Path.home()
        self.prismatic = home_p / ".prismatic"
        self.current_link = self.prismatic / "current"
        self.venv_link = self.prismatic / "venv_current"
        self.releases_dir = self.prismatic / "releases"
        self.venvs_dir = self.prismatic / "venvs"
        self.wheel_cache = self.prismatic / "wheel_cache"
        self.run_dir = self.prismatic / "run"

        self._run = run or self._subprocess_run
        self._http_get = http_get or _default_http_get
        self.lock_timeout_s = lock_timeout_s
        self.service = service or os.environ.get(
            "PRISMATIC_GATEWAY_SERVICE", DEFAULT_GATEWAY_SERVICE
        )
        self.port = port or int(os.environ.get("PRISMATIC_PORT", str(DEFAULT_GATEWAY_PORT)))
        self.extras = extras or os.environ.get(
            "PRISMATIC_GATEWAY_EXTRAS", DEFAULT_EXTRAS
        )
        self.systemctl_bin = systemctl_bin

    # ------------------------------------------------------------------
    # low-level helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _subprocess_run(argv: list[str], **kwargs: Any) -> Any:
        return subprocess.run(
            argv,
            capture_output=kwargs.get("capture_output", True),
            text=True,
            timeout=kwargs.get("timeout"),
            cwd=kwargs.get("cwd"),
        )

    def _run_checked(self, argv: list[str], timeout: int | None = None) -> Any:
        """Run argv; raise DeployFailed on non-zero exit or launch error."""
        try:
            cp = self._run(argv, timeout=timeout)
        except Exception as exc:
            raise DeployFailed(f"command failed to launch {' '.join(argv[:3])}: {exc}")
        if getattr(cp, "returncode", 1) != 0:
            err = (getattr(cp, "stderr", "") or "").strip()[:500]
            raise DeployFailed(
                f"command exited {cp.returncode}: {' '.join(argv[:4])} :: {err}"
            )
        return cp

    def _run_out(self, argv: list[str], timeout: int | None = None) -> str:
        cp = self._run_checked(argv, timeout=timeout)
        return (getattr(cp, "stdout", "") or "").strip()

    @staticmethod
    def _readlink(link: Path) -> Path | None:
        try:
            return Path(os.readlink(link))
        except OSError:
            return None

    @staticmethod
    def _atomic_symlink_swap(target_dir: Path, symlink_path: Path) -> None:
        """Atomic symlink swap via temp symlink + os.replace."""
        tmp = symlink_path.parent / f".tmp_gwswap_{os.getpid()}_{int(time.time() * 1000)}"
        try:
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()
            os.symlink(target_dir, tmp)
            os.replace(tmp, symlink_path)
        finally:
            if tmp.exists() or tmp.is_symlink():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    @contextmanager
    def _locked(self):  # type: ignore[no-untyped-def]
        """Exclusive inter-process deploy lock with a timeout."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.run_dir / LOCK_NAME
        fh = open(lock_path, "a+")
        try:
            deadline = time.monotonic() + self.lock_timeout_s
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise DeployBusy(
                            f"timed out after {self.lock_timeout_s}s waiting for "
                            f"deploy lock {lock_path} -- another deploy may be "
                            f"stuck; investigate before retrying"
                        )
                    time.sleep(2)
            fh.write(f"{os.getpid()} {time.time()}\n")
            fh.flush()
            yield
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()

    # ------------------------------------------------------------------
    # public entry point
    # ------------------------------------------------------------------

    def redeploy(
        self, pr_sha: str, repo: Path | str, dry_run: bool = False
    ) -> GatewayDeployResult:
        """Run the full atomic redeploy. Never raises; result carries all."""
        started = time.time()
        res = GatewayDeployResult(pr_sha=str(pr_sha or ""))
        if dry_run:
            res.success = True
            res.skipped = True
            res.reason = "dry-run: gateway redeploy skipped"
            res.duration_ms = int((time.time() - started) * 1000)
            return res
        try:
            with self._locked():
                res = self._redeploy_locked(str(pr_sha or ""), Path(repo), started)
        except DeployBusy as exc:
            res.success = False
            res.skipped = True
            res.reason = str(exc)
            res.duration_ms = int((time.time() - started) * 1000)
        return res

    # ------------------------------------------------------------------
    # deploy steps
    # ------------------------------------------------------------------

    def _redeploy_locked(
        self, pr_sha: str, repo: Path, started: float
    ) -> GatewayDeployResult:
        res = GatewayDeployResult(pr_sha=pr_sha)
        tmp = Path(tempfile.mkdtemp(prefix="gw-deploy-"))
        worktree = tmp / "worktree"
        flipped_venv = False
        flipped_current = False
        try:
            # 1-2. verify the SHA is a real commit on origin/main
            full_sha, main_sha = self._verify_sha(repo, pr_sha)
            res.pr_sha = full_sha
            logger.info(
                "gateway redeploy: sha=%s origin/main=%s", full_sha[:12], main_sha[:12]
            )

            # 3. supersede check: don't redeploy what is already live (or newer)
            live_sha = self._live_sha()
            if live_sha and self._is_ancestor(repo, full_sha, live_sha):
                res.success = True
                res.skipped = True
                res.reason = (
                    f"superseded: live release {live_sha[:12]} already contains "
                    f"{full_sha[:12]}"
                )
                res.duration_ms = int((time.time() - started) * 1000)
                return res

            # 4. pristine detached worktree of exactly pr_sha
            self._run_checked(["git", "-C", str(repo), "worktree", "prune"])
            self._run_checked(
                ["git", "-C", str(repo), "worktree", "add", "--detach",
                 str(worktree), full_sha],
                timeout=300,
            )

            # 5. build wheel
            dist = tmp / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            wheel = self._build_wheel(worktree, dist)

            # 6. fresh venv + install
            venv_dir = self.venvs_dir / f"prismatic-engine-{full_sha}"
            self._create_venv(venv_dir, wheel)

            # 7. stage immutable release dir
            version_dir = self.releases_dir / f"prismatic-engine-{full_sha}"
            self._stage_release(worktree, version_dir)

            # record previous targets for rollback + audit
            prev_current = self._readlink(self.current_link)
            prev_venv = self._readlink(self.venv_link)
            res.previous_version_dir = str(prev_current or "")
            res.previous_venv_dir = str(prev_venv or "")
            self._write_state(full_sha, prev_current, prev_venv, version_dir, venv_dir)

            # 8. atomic flips (venv first, then current)
            self._atomic_symlink_swap(venv_dir, self.venv_link)
            flipped_venv = True
            self._atomic_symlink_swap(version_dir, self.current_link)
            flipped_current = True
            res.version_dir = str(version_dir)
            res.venv_dir = str(venv_dir)

            # 9. restart the live gateway service
            self._systemctl("restart", timeout=180)

            # 10. strict health check against the NEW release
            health = self._health_check(venv_dir)
            res.health = health
            if not health["passed"]:
                raise DeployFailed(
                    f"post-restart health check failed: {health['details']}"
                )

            res.success = True
            res.reason = f"gateway redeployed to {full_sha[:12]}"
            logger.info("gateway redeploy SUCCESS: %s", res.reason)
            return res

        except DeployRefused as exc:
            res.success = False
            res.reason = f"refused: {exc}"
            logger.warning("gateway redeploy REFUSED: %s", exc)
            return res
        except Exception as exc:
            res.success = False
            res.reason = str(exc)
            logger.error("gateway redeploy FAILED: %s", exc)
            res.rolled_back = self._rollback(
                res, flipped_venv=flipped_venv, flipped_current=flipped_current
            )
            if not res.rolled_back:
                res.reason += (
                    " | ROLLBACK FAILED -- gateway may be down. Manual recovery: "
                    "sudo /usr/bin/systemctl restart prismatic-gateway.service ; "
                    "verify ~/.prismatic/current and ~/.prismatic/venv_current symlinks"
                )
            return res
        finally:
            try:
                self._run(["git", "-C", str(repo), "worktree", "remove",
                           "--force", str(worktree)], timeout=120)
            except Exception:
                pass
            shutil.rmtree(tmp, ignore_errors=True)
            res.duration_ms = int((time.time() - started) * 1000)

    # ------------------------------------------------------------------
    # individual steps (overridable for tests)
    # ------------------------------------------------------------------

    def _verify_sha(self, repo: Path, pr_sha: str) -> tuple[str, str]:
        """Fetch origin/main and prove pr_sha is a commit on it."""
        if not pr_sha or not SHA_RE.match(pr_sha):
            raise DeployRefused(f"pr_sha {pr_sha!r} is not a valid commit SHA")
        try:
            self._run_checked(["git", "-C", str(repo), "fetch", "origin", "main"],
                              timeout=300)
        except DeployFailed as exc:
            raise DeployRefused(f"could not fetch origin/main: {exc}")
        main_sha = self._run_out(
            ["git", "-C", str(repo), "rev-parse", "--verify", "origin/main"])
        try:
            full_sha = self._run_out(
                ["git", "-C", str(repo), "rev-parse", "--verify", f"{pr_sha}^{{commit}}"])
        except DeployFailed:
            raise DeployRefused(f"{pr_sha!r} is not a commit in this repo")
        if not self._is_ancestor(repo, full_sha, main_sha):
            raise DeployRefused(
                f"{full_sha[:12]} is not an ancestor of origin/main ({main_sha[:12]})"
            )
        return full_sha, main_sha

    def _is_ancestor(self, repo: Path, sha: str, descendant: str) -> bool:
        cp = self._run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, descendant])
        return getattr(cp, "returncode", 1) == 0

    def _live_sha(self) -> str | None:
        target = self._readlink(self.current_link)
        if not target:
            return None
        name = target.name
        if name.startswith("prismatic-engine-"):
            sha = name[len("prismatic-engine-"):]
            if SHA_RE.match(sha):
                return sha
        return None

    def _build_wheel(self, worktree: Path, dist_dir: Path) -> Path:
        self._run_checked(
            [sys.executable, "-m", "pip", "wheel", "--no-deps",
             "--wheel-dir", str(dist_dir), str(worktree)],
            timeout=900,
        )
        wheels = sorted(dist_dir.glob("prismatic_engine-*.whl"))
        if not wheels:
            raise DeployFailed("wheel build produced no prismatic_engine wheel")
        logger.info("built wheel %s", wheels[-1].name)
        return wheels[-1]

    def _create_venv(self, venv_dir: Path, wheel: Path) -> None:
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        self._run_checked([sys.executable, "-m", "venv", str(venv_dir)], timeout=600)
        pip = venv_dir / "bin" / "pip"
        self._run_checked(
            [str(pip), "install", "--find-links", str(self.wheel_cache),
             f"{wheel}[{self.extras}]"],
            timeout=1800,
        )
        logger.info("venv ready at %s", venv_dir)

    def _stage_release(self, worktree: Path, version_dir: Path) -> None:
        if version_dir.exists():
            shutil.rmtree(version_dir)
        version_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._run_checked(
                ["rsync", "-a", "--exclude=.git", "--exclude=node_modules",
                 "--exclude=__pycache__", "--exclude=.pytest_cache",
                 f"{worktree}/", f"{version_dir}/"],
                timeout=900,
            )
        except DeployFailed:
            logger.warning("rsync unavailable, falling back to shutil copy")
            for item in worktree.iterdir():
                if item.name in (".git", "node_modules", "__pycache__", ".pytest_cache"):
                    continue
                dest = version_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)

    def _write_state(
        self,
        full_sha: str,
        prev_current: Path | None,
        prev_venv: Path | None,
        version_dir: Path,
        venv_dir: Path,
    ) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "pr_sha": full_sha,
            "at": time.time(),
            "previous_current": str(prev_current or ""),
            "previous_venv": str(prev_venv or ""),
            "new_version_dir": str(version_dir),
            "new_venv_dir": str(venv_dir),
        }
        (self.run_dir / STATE_NAME).write_text(json.dumps(state, indent=2))

    def _systemctl(self, action: str, timeout: int | None = 120) -> None:
        # argv must match the NOPASSWD sudoers entry exactly
        self._run_checked(
            ["sudo", "-n", self.systemctl_bin, action, self.service], timeout=timeout
        )

    def _service_active(self, timeout_s: int = 60) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                out = self._run_out(
                    ["sudo", "-n", self.systemctl_bin, "is-active", self.service],
                    timeout=15,
                )
                if out == "active":
                    return True
            except DeployFailed:
                pass
            time.sleep(3)
        return False

    def _poll_http(self, path: str, timeout_s: int = 90) -> tuple[bool, str]:
        url = f"http://localhost:{self.port}{path}"
        deadline = time.monotonic() + timeout_s
        last = "no attempts"
        while time.monotonic() < deadline:
            ok, detail = self._http_get(url, timeout=5)
            if ok:
                return True, detail
            last = detail
            time.sleep(3)
        return False, f"timed out after {timeout_s}s; last: {last}"

    def _health_check(self, venv_dir: Path) -> dict[str, Any]:
        """Strict health check of the NEW release. Every check must pass."""
        checks: dict[str, bool] = {}
        details: dict[str, str] = {}

        active = self._service_active(timeout_s=60)
        checks["service_active"] = active
        if not active:
            details["service_active_error"] = (
                f"{self.service} did not become active after restart"
            )

        try:
            self._run_checked(
                [str(venv_dir / "bin" / "python"), "-c",
                 "import prismatic.gateway.server"],
                timeout=120,
            )
            checks["smoke_import"] = True
        except DeployFailed as exc:
            checks["smoke_import"] = False
            details["smoke_import_error"] = str(exc)[:300]

        for path, name in (("/health", "gateway_health"),
                           ("/api/review-factory/jobs", "review_jobs")):
            ok, detail = self._poll_http(path, timeout_s=90)
            checks[f"http_{name}"] = ok
            details[f"http_{name}_detail"] = detail

        passed = all(checks.values())
        return {"passed": passed, "checks": checks, "details": details}

    def _rollback(
        self, res: GatewayDeployResult, flipped_venv: bool, flipped_current: bool
    ) -> bool:
        """Restore previous release. Returns True only if fully recovered."""
        if not flipped_venv and not flipped_current:
            logger.info("rollback: nothing was flipped, no restart needed")
            return True
        try:
            if flipped_venv and res.previous_venv_dir:
                self._atomic_symlink_swap(Path(res.previous_venv_dir), self.venv_link)
            if flipped_current and res.previous_version_dir:
                self._atomic_symlink_swap(
                    Path(res.previous_version_dir), self.current_link)
        except Exception as exc:
            logger.error("rollback: symlink restore failed: %s", exc)
            return False
        try:
            self._systemctl("restart", timeout=180)
        except DeployFailed as exc:
            logger.error("rollback: service restart failed: %s", exc)
            return False
        if not self._service_active(timeout_s=90):
            logger.error("rollback: service did not recover to active state")
            return False
        logger.warning(
            "rollback: gateway restored to previous release %s",
            (res.previous_version_dir or "")[-12:],
        )
        return True
