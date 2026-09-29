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

Crash window (deploy hardening): the two live symlinks are flipped
sequentially (``venv_current`` first, then ``current``); each flip is atomic
but the pair is not one transaction. A process death between the flips
(SIGKILL, power loss) leaves a mixed generation -- new venv + old code --
that the in-process rollback cannot see (it only runs on exceptions). The
proportionate fix is not a structural redesign (one symlink for both would
touch the venv layout, the service ``ExecStart``, and the WS1 per-repo link
scheme): every redeploy starts with ``_reconcile_links()``, which restores a
mismatched ``venv_current`` to the generation named by ``current`` (the code
link is authoritative) and alert-logs the repair. A re-run therefore heals
the pair even when the crash itself went unrecorded.

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

from pe.deploy.deploy_alerts import emit_deploy_alert
from pe.deploy.deploy_events import emit_deploy_event
from pe.deploy.process_manager import ProcessManager, ProcessManagerError
from pe.deploy.process_manager_systemd import SystemdProcessManager
from pe.deploy.config import (
    DEFAULT_HEALTH_ENDPOINTS,
    DEFAULT_RELEASE_PREFIX,
    DEFAULT_SMOKE_IMPORT,
    DeployRepoConfig,
)

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
        manager: ProcessManager | None = None,
        health_endpoints: tuple[tuple[str, str], ...] | None = None,
        release_prefix: str | None = None,
        smoke_import: str | None = None,
    ):
        home_p = Path(home).expanduser() if home else Path.home()
        self.prismatic = home_p / ".prismatic"
        # Per-repo live links (second-repo support). The default repo keeps
        # the exact pre-existing link names, so gateway behavior is unchanged;
        # any other repo gets suffixed links so its deploys can never flip
        # the production gateway's symlinks.
        self.release_prefix = release_prefix or DEFAULT_RELEASE_PREFIX
        if self.release_prefix == DEFAULT_RELEASE_PREFIX:
            self.current_link = self.prismatic / "current"
            self.venv_link = self.prismatic / "venv_current"
            self.state_name = STATE_NAME
        else:
            self.current_link = self.prismatic / f"current-{self.release_prefix}"
            self.venv_link = self.prismatic / f"venv_current-{self.release_prefix}"
            self.state_name = f"last-deploy-{self.release_prefix}.json"
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
        self.port = port if port is not None else int(
            os.environ.get("PRISMATIC_PORT", str(DEFAULT_GATEWAY_PORT))
        )
        # None = not provided (env/default); "" = explicitly no extras.
        self.extras = (
            extras
            if extras is not None
            else os.environ.get("PRISMATIC_GATEWAY_EXTRAS", DEFAULT_EXTRAS)
        )
        self.smoke_import = smoke_import or DEFAULT_SMOKE_IMPORT
        # WS1: per-repo HTTP health endpoints; default == pre-WS1 behavior.
        self.health_endpoints = health_endpoints or DEFAULT_HEALTH_ENDPOINTS
        self.systemctl_bin = systemctl_bin
        self._process_manager = (
            manager
            if manager is not None
            else SystemdProcessManager(systemctl_bin=systemctl_bin, run=self._run)
        )

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
        self,
        pr_sha: str,
        repo: Path | str,
        dry_run: bool = False,
        repo_config: DeployRepoConfig | None = None,
    ) -> GatewayDeployResult:
        """Run the full atomic redeploy. Never raises; result carries all.

        ``repo_config`` (WS1) supplies the release-naming prefix and the
        health endpoints for the routed repo; unset means the default repo's
        values, i.e. exactly today's behavior.
        """
        started = time.time()
        res = GatewayDeployResult(pr_sha=str(pr_sha or ""))
        if dry_run:
            res.success = True
            res.skipped = True
            res.reason = "dry-run: gateway redeploy skipped"
            res.duration_ms = int((time.time() - started) * 1000)
            return res
        release_prefix = repo_config.release_prefix if repo_config else DEFAULT_RELEASE_PREFIX
        try:
            with self._locked():
                res = self._redeploy_locked(
                    str(pr_sha or ""),
                    Path(repo),
                    started,
                    release_prefix=release_prefix,
                )
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
        self,
        pr_sha: str,
        repo: Path,
        started: float,
        release_prefix: str = DEFAULT_RELEASE_PREFIX,
    ) -> GatewayDeployResult:
        res = GatewayDeployResult(pr_sha=pr_sha)
        tmp = Path(tempfile.mkdtemp(prefix="gw-deploy-"))
        worktree = tmp / "worktree"
        flipped_venv = False
        flipped_current = False
        try:
            # 0. reconcile a mixed current/venv_current pair left behind by
            #    a crashed deploy (SIGKILL between the two flips). The code
            #    link is authoritative; the venv link is restored to the
            #    matching generation (or rebuilt below when it is gone).
            self._reconcile_links()

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
            venv_dir = self.venvs_dir / f"{release_prefix}-{full_sha}"
            self._create_venv(venv_dir, wheel)

            # 7. stage immutable release dir
            version_dir = self.releases_dir / f"{release_prefix}-{full_sha}"
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
                # Alert-log: the DETECT moment. Additive only -- the raise
                # below is unchanged.
                emit_deploy_alert(
                    "GatewayDeployHealthCheckFailed",
                    "critical",
                    f"gateway deploy health check failed for {full_sha[:12]}",
                    f"pr_sha={full_sha} failed_release={version_dir.name} "
                    f"reason={health['details']}",
                )
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
            # Alert-log: the FAILURE moment (fires for every failure path,
            # including the health-check failure above). Additive only --
            # the rollback call below is unchanged.
            emit_deploy_alert(
                "GatewayDeployFailed",
                "critical",
                f"gateway deploy failed for {res.pr_sha[:12]}: {str(exc)[:120]}",
                f"pr_sha={res.pr_sha} attempted_release={res.version_dir} "
                f"reason={str(exc)[:300]}",
            )
            # Portal Phase 1 (P0 #3): push the gateway-step failure to the
            # event bus (mirrors GatewayDeployFailed). The pipeline-level
            # deploy.failed follows at the terminal state; the rollback
            # phases are emitted from _rollback below.
            emit_deploy_event(
                "deploy.failed",
                {
                    "pr_sha": res.pr_sha,
                    "release_prefix": self.release_prefix,
                    "step": "gateway-redeploy",
                    "failure_reason": str(exc)[:300],
                    "attempted_release": (
                        Path(res.version_dir).name if res.version_dir else ""
                    ),
                },
            )
            res.rolled_back = self._rollback(
                res, flipped_venv=flipped_venv, flipped_current=flipped_current
            )
            if not res.rolled_back:
                res.reason += (
                    " | ROLLBACK FAILED -- service may be down. Manual recovery: "
                    f"sudo /usr/bin/systemctl restart {self.service} ; "
                    f"verify {self.current_link} and {self.venv_link} symlinks"
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

    def _reconcile_links(self) -> None:
        """Reconcile a mixed ``current``/``venv_current`` pair (deploy hardening).

        The two live symlinks flip sequentially (venv first, then current);
        a process death between the flips leaves a mixed generation (new
        venv + old code) that nothing detects -- the in-process rollback
        only runs on exceptions, not on SIGKILL or power loss. A re-run
        heals the pair (both links flip again), so the fix is
        detect-and-reconcile here, at the start of every redeploy, rather
        than a structural redesign.

        Semantics: the CODE link (``current``) is authoritative. When
        ``venv_current`` names a different generation and the matching venv
        dir exists on disk, ``venv_current`` is atomically restored to it
        and a ``GatewayLinkMismatchReconciled`` critical alert is logged.
        When the matching venv dir is gone, a critical alert is logged and
        the deploy proceeds -- it builds a fresh venv and flips both links,
        healing the pair. ``current`` missing means a first deploy: no-op.
        Never raises: reconcile is best-effort repair, never a deploy
        blocker.
        """
        try:
            current_target = self._readlink(self.current_link)
            if current_target is None:
                return  # first deploy: no live generation yet
            expected_venv = self.venvs_dir / current_target.name
            venv_target = self._readlink(self.venv_link)
            if venv_target is not None and venv_target.name == expected_venv.name:
                return  # consistent pair
            detail = (
                f"current={current_target.name} "
                f"venv_current={venv_target.name if venv_target else '<missing>'} "
                f"expected_venv={expected_venv.name}"
            )
            if expected_venv.is_dir():
                self._atomic_symlink_swap(expected_venv, self.venv_link)
                logger.warning(
                    "gateway redeploy: reconciled mixed link pair: %s", detail
                )
                emit_deploy_alert(
                    "GatewayLinkMismatchReconciled",
                    "critical",
                    "mixed gateway link pair reconciled: venv_current restored "
                    f"to {expected_venv.name}",
                    detail,
                )
            else:
                logger.warning(
                    "gateway redeploy: mixed link pair but the expected venv "
                    "dir is missing; proceeding (this deploy rebuilds it): %s",
                    detail,
                )
                emit_deploy_alert(
                    "GatewayLinkMismatchReconciled",
                    "critical",
                    "mixed gateway link pair: expected venv dir missing, "
                    "proceeding to rebuild it",
                    detail,
                )
        except Exception as exc:
            # Reconcile must never block a deploy; the deploy's own flips
            # heal the pair on the way through. Loud, but non-fatal.
            logger.warning("gateway redeploy: link reconcile failed: %s", exc)

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
        """Return the short SHA of the currently-live release, if any.

        Reads this deployer's own live link, so per-repo links give per-repo
        answers. The default repo keeps the exact pre-existing behavior.
        """
        target = self._readlink(self.current_link)
        if not target:
            return None
        name = target.name
        prefix = f"{self.release_prefix}-"
        if name.startswith(prefix):
            sha = name[len(prefix):]
            if SHA_RE.match(sha):
                return sha
        return None

    def _build_wheel(self, worktree: Path, dist_dir: Path) -> Path:
        self._run_checked(
            [sys.executable, "-m", "pip", "wheel", "--no-deps",
             "--wheel-dir", str(dist_dir), str(worktree)],
            timeout=900,
        )
        # WS1: accept whatever wheel the repo's source tree builds. The
        # default repo still produces prismatic_engine-*.whl, as before.
        wheels = sorted(dist_dir.glob("*.whl"))
        if not wheels:
            raise DeployFailed("wheel build produced no wheel")
        logger.info("built wheel %s", wheels[-1].name)
        return wheels[-1]

    def _create_venv(self, venv_dir: Path, wheel: Path) -> None:
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        self._run_checked([sys.executable, "-m", "venv", str(venv_dir)], timeout=600)
        pip = venv_dir / "bin" / "pip"
        # An explicitly empty extras means "install the bare wheel" (repos
        # without extras would fail on wheel[]).
        target = f"{wheel}[{self.extras}]" if self.extras else str(wheel)
        self._run_checked(
            [str(pip), "install", "--find-links", str(self.wheel_cache), target],
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
        (self.run_dir / self.state_name).write_text(json.dumps(state, indent=2))

    def _systemctl(self, action: str, timeout: int | None = 120) -> None:
        # The only action the deploy loop ever issues is "restart"; the
        # real work is delegated to the process manager (whose argv must
        # match the NOPASSWD sudoers entry exactly). This wrapper keeps
        # the override point and the DeployFailed surface that callers
        # and tests rely on.
        if action != "restart":
            raise DeployFailed(f"unsupported systemctl action: {action!r}")
        try:
            self._process_manager.restart(self.service, timeout=timeout)
        except ProcessManagerError as exc:
            raise DeployFailed(str(exc)) from exc

    def _service_active(self, timeout_s: int = 60) -> bool:
        try:
            return self._process_manager.is_active(self.service, timeout_s=timeout_s)
        except ProcessManagerError as exc:
            raise DeployFailed(str(exc)) from exc

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
                 f"import {self.smoke_import}"],
                timeout=120,
            )
            checks["smoke_import"] = True
        except DeployFailed as exc:
            checks["smoke_import"] = False
            details["smoke_import_error"] = str(exc)[:300]

        for path, name in self.health_endpoints:
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
        # Alert-log: the ROLLBACK-STARTED moment. Additive only.
        prev_release = (
            Path(res.previous_version_dir).name if res.previous_version_dir else ""
        )
        failed_release = Path(res.version_dir).name if res.version_dir else ""
        emit_deploy_alert(
            "GatewayRollbackStarted",
            "critical",
            f"gateway rollback started: restoring {prev_release[-12:] or 'previous release'}",
            f"pr_sha={res.pr_sha} failed_release={failed_release} "
            f"previous_release={prev_release}",
        )
        # Portal Phase 1 (P0 #3): push the rollback-started phase.
        emit_deploy_event(
            "deploy.rolled_back",
            {
                "pr_sha": res.pr_sha,
                "release_prefix": self.release_prefix,
                "step": "gateway-redeploy",
                "phase": "started",
                "failed_release": failed_release,
                "previous_release": prev_release,
            },
        )
        try:
            if flipped_venv and res.previous_venv_dir:
                self._atomic_symlink_swap(Path(res.previous_venv_dir), self.venv_link)
            if flipped_current and res.previous_version_dir:
                self._atomic_symlink_swap(
                    Path(res.previous_version_dir), self.current_link)
        except Exception as exc:
            logger.error("rollback: symlink restore failed: %s", exc)
            emit_deploy_alert(
                "GatewayRollbackFailed",
                "critical",
                f"gateway rollback failed for {res.pr_sha[:12]}: symlink restore",
                f"pr_sha={res.pr_sha} failure_point=symlink_restore reason={exc}",
            )
            # Portal Phase 1 (P0 #3): push the failed rollback phase.
            emit_deploy_event(
                "deploy.rolled_back",
                {
                    "pr_sha": res.pr_sha,
                    "release_prefix": self.release_prefix,
                    "step": "gateway-redeploy",
                    "phase": "failed",
                    "failure_point": "symlink_restore",
                    "failure_reason": str(exc)[:300],
                },
            )
            return False
        try:
            self._systemctl("restart", timeout=180)
        except DeployFailed as exc:
            logger.error("rollback: service restart failed: %s", exc)
            emit_deploy_alert(
                "GatewayRollbackFailed",
                "critical",
                f"gateway rollback failed for {res.pr_sha[:12]}: service restart",
                f"pr_sha={res.pr_sha} failure_point=service_restart reason={exc}",
            )
            # Portal Phase 1 (P0 #3): push the failed rollback phase.
            emit_deploy_event(
                "deploy.rolled_back",
                {
                    "pr_sha": res.pr_sha,
                    "release_prefix": self.release_prefix,
                    "step": "gateway-redeploy",
                    "phase": "failed",
                    "failure_point": "service_restart",
                    "failure_reason": str(exc)[:300],
                },
            )
            return False
        if not self._service_active(timeout_s=90):
            logger.error("rollback: service did not recover to active state")
            emit_deploy_alert(
                "GatewayRollbackFailed",
                "critical",
                f"gateway rollback failed for {res.pr_sha[:12]}: service not active",
                f"pr_sha={res.pr_sha} failure_point=service_active "
                "reason=recovery verification timed out",
            )
            # Portal Phase 1 (P0 #3): push the failed rollback phase.
            emit_deploy_event(
                "deploy.rolled_back",
                {
                    "pr_sha": res.pr_sha,
                    "release_prefix": self.release_prefix,
                    "step": "gateway-redeploy",
                    "phase": "failed",
                    "failure_point": "service_active",
                    "failure_reason": "recovery verification timed out",
                },
            )
            return False
        logger.warning(
            "rollback: gateway restored to previous release %s",
            (res.previous_version_dir or "")[-12:],
        )
        # Alert-log: the ROLLBACK-COMPLETED moment (recovery confirmed).
        # Additive only -- the return value is unchanged.
        emit_deploy_alert(
            "GatewayRollbackCompleted",
            "critical",
            "gateway rollback completed: restored to "
            f"{prev_release[-12:] or 'previous release'}",
            f"pr_sha={res.pr_sha} restored_release={prev_release} "
            "service_active=true",
        )
        # Portal Phase 1 (P0 #3): push the rollback-completed phase.
        emit_deploy_event(
            "deploy.rolled_back",
            {
                "pr_sha": res.pr_sha,
                "release_prefix": self.release_prefix,
                "step": "gateway-redeploy",
                "phase": "completed",
                "restored_release": prev_release,
            },
        )
        return True
