"""Local HTTP Deploy Receiver listening on port 9460 (WB-2).

Corresponds to §7.1 - §7.4 and R2 of okf-docs-workspace-deploy-v1.md.
Validates HMAC-SHA256 signature (DEPLOY_HMAC_SECRET), triggers deploy pipeline, runs health checks, and transitions Linear issues.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse

    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from pe.deploy.gateway_redeploy import GatewayRedeployer
from pe.deploy.deploy_alerts import emit_deploy_alert
from pe.deploy.health import PostDeployHealthChecker
from pe.deploy.integrate import AtomicDeployRunner
from pe.deploy.linear_transition import LinearDeployTransitioner
from pe.deploy.manifest import DeployManifestStore, DeployRecord
from pe.deploy.config import (
    DeployRepoConfig,
    UnknownRepoError,
    allow_default_hmac,
    default_repo_config,
    deploy_source_repo,
    deploy_timeout_s,
    load_repo_registry,
    strict_secrets,
)

logger = logging.getLogger(__name__)

RECEIVER_PORT = 9460

#: Overall timeout for one deploy request (seconds). The blocking deploy runs
#: in a worker thread so /health stays responsive; a deploy that exceeds this
#: fails loudly instead of hanging the workflow forever. (2026-09-22: an
#: unbounded rsync hung the receiver for 37 minutes and starved /health.)
DEPLOY_TIMEOUT_S = deploy_timeout_s()

MIRROR_FETCH_TIMEOUT_S = 120


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse key-value pairs from a simple .env file."""
    res: dict[str, str] = {}
    if not path.is_file():
        return res
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            res[k.strip()] = v.strip().strip("'\"")
    except Exception:
        pass
    return res


def get_deploy_hmac_secret() -> str:
    """Retrieve DEPLOY_HMAC_SECRET from environment, .env files, or dev fallback."""
    secret = os.environ.get("DEPLOY_HMAC_SECRET")
    if secret:
        return secret

    # Check local .env files
    for env_path in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
        Path.home() / ".prismatic" / ".env",
    ):
        env_vars = _load_env_file(env_path)
        if "DEPLOY_HMAC_SECRET" in env_vars:
            return env_vars["DEPLOY_HMAC_SECRET"]

    # Graceful fallback for test/dev mode or default local setup
    if allow_default_hmac() or not strict_secrets():
        logger.warning("DEPLOY_HMAC_SECRET not set; using default dev HMAC secret.")
        return "prismatic-deploy-hmac-secret-v1"

    raise RuntimeError(
        "DEPLOY_HMAC_SECRET environment variable or .env entry is missing! "
        "Set DEPLOY_HMAC_SECRET in .env or environment to enforce production security."
    )


def get_repo_hmac_secret(repo: DeployRepoConfig) -> str:
    """Resolve the HMAC secret for one routed repo (WS1).

    Precedence: the repo's per-repo env var
    (``DEPLOY_HMAC_SECRET_<OWNER>_<REPO>``, see
    :attr:`DeployRepoConfig.hmac_secret_env`), then the same ``.env`` files
    the shared secret uses, then the shared ``DEPLOY_HMAC_SECRET`` fallback
    (which keeps the strict/dev semantics of :func:`get_deploy_hmac_secret`:
    a missing secret for a routed repo is a loud refusal in strict mode).
    """
    per_repo_var = repo.hmac_secret_env
    _env = os.environ
    secret = _env.get(per_repo_var)
    if secret:
        return secret

    # Check local .env files for the per-repo var (same precedence as shared)
    for env_path in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
        Path.home() / ".prismatic" / ".env",
    ):
        env_vars = _load_env_file(env_path)
        if per_repo_var in env_vars:
            return env_vars[per_repo_var]

    # Shared fallback -- a missing secret for a routed repo is a loud
    # refusal in strict mode, naming the repo and the expected variable.
    try:
        return get_deploy_hmac_secret()
    except RuntimeError as exc:
        raise RuntimeError(
            f"no HMAC secret for repository {repo.full_name!r}: "
            f"{per_repo_var} is not set and the shared DEPLOY_HMAC_SECRET "
            f"fallback is unavailable: {exc}"
        ) from exc


def verify_hmac_signature(
    body_bytes: bytes,
    signature_header: str | None,
    secret: str | None = None,
) -> bool:
    """Verify HMAC-SHA256 signature against request body."""
    if not signature_header:
        return False

    try:
        sec_str = secret or get_deploy_hmac_secret()
    except RuntimeError:
        logger.error("HMAC verification failed: DEPLOY_HMAC_SECRET not set")
        return False

    sec = sec_str.encode("utf-8")

    # Strip 'sha256=' prefix if present
    sig = signature_header.replace("sha256=", "").strip()
    expected_sig = hmac.new(sec, body_bytes, hashlib.sha256).hexdigest()

    return hmac.compare_digest(sig, expected_sig)


class DeployReceiverPipeline:
    """Orchestrates full deploy lifecycle upon receiving signed POST."""

    def __init__(
        self,
        source_repo: Path | None = None,
        deploy_runner: AtomicDeployRunner | None = None,
        health_checker: PostDeployHealthChecker | None = None,
        transitioner: LinearDeployTransitioner | None = None,
        store: DeployManifestStore | None = None,
        gateway_redeployer: GatewayRedeployer | None = None,
        mirror_repo: Path | None = None,
        repo_config: DeployRepoConfig | None = None,
    ):
        self.source_repo = source_repo or self._source_repo_from_env()
        # WS1: the repo this pipeline instance is configured for (registry
        # default when unset). The default deploy runner / gateway redeployer
        # are built from the default repo's config so release naming and the
        # target service agree between the two writers of the convention.
        # Explicitly injected doubles (tests) are still honored as-is.
        self.repo_config = repo_config or default_repo_config()
        self.deploy_runner = deploy_runner or AtomicDeployRunner(
            release_prefix=self.repo_config.release_prefix
        )
        self.health_checker = health_checker or PostDeployHealthChecker()
        self.transitioner = transitioner or LinearDeployTransitioner()
        self.store = store or DeployManifestStore()
        self.gateway_redeployer = gateway_redeployer or GatewayRedeployer(
            service=self.repo_config.target_service,
            health_endpoints=self.repo_config.health_endpoints,
        )
        self.mirror_repo = mirror_repo
        # Per-repo runners/redeployers for non-default repos, built lazily.
        self._runners: dict[str, AtomicDeployRunner] = {}
        self._redeployers: dict[str, GatewayRedeployer] = {}

    @staticmethod
    def _source_repo_from_env() -> Path:
        """Resolve the deploy source from PRISMATIC_DEPLOY_SOURCE_REPO.

        Fail-fast: the receiver never guesses a deploy source from its
        working directory. (2026-09-22: a CWD-derived source silently turned
        the entire ~/.prismatic home tree into a deploy source, rsynced 82G
        into itself, and filled the disk.)
        """
        return deploy_source_repo()

    def _resolve_repo(
        self, payload: dict[str, Any], repo_config: DeployRepoConfig | None
    ) -> DeployRepoConfig:
        """Route a deploy payload to its repo config (WS1).

        Explicit ``repo_config`` wins (internal callers); otherwise the
        payload's ``repository`` field (``owner/repo``) is looked up in the
        registry -- an unknown repo raises :class:`UnknownRepoError`. A
        payload with no ``repository`` field falls back to this pipeline's
        configured repo (direct/internal callers only; the HTTP intake
        fail-closes on a missing field before reaching here).
        """
        if repo_config is not None:
            return repo_config
        repository = payload.get("repository")
        if repository:
            return load_repo_registry().get(str(repository))
        return self.repo_config

    def _runner_for(self, repo: DeployRepoConfig) -> AtomicDeployRunner:
        """Atomic deploy runner for one repo (built lazily, cached)."""
        if repo.full_name == self.repo_config.full_name:
            return self.deploy_runner
        if repo.full_name not in self._runners:
            from pe.deploy.config import release_symlink_path, versions_dir

            self._runners[repo.full_name] = AtomicDeployRunner(
                versions_dir=versions_dir(),
                release_symlink=release_symlink_path(repo.release_prefix),
                release_prefix=repo.release_prefix,
                dry_run=self.deploy_runner.dry_run,
            )
        return self._runners[repo.full_name]

    def _source_repo_for(self, repo: DeployRepoConfig) -> Path:
        """Source checkout a deploy builds from (WS1).

        The default repo builds from the explicit ``PRISMATIC_DEPLOY_SOURCE_REPO``
        checkout (fail-fast when unset, pre-WS1 behavior). An additional repo
        has no configured source checkout, so it builds from its own persistent
        mirror -- the mirror timer keeps it tracking origin/main.
        """
        if repo.full_name == self.repo_config.full_name:
            return self.source_repo
        return repo.mirror_dir

    def _redeployer_for(self, repo: DeployRepoConfig) -> GatewayRedeployer:
        """Gateway redeployer for one repo (built lazily, cached)."""
        if repo.full_name == self.repo_config.full_name:
            return self.gateway_redeployer
        if repo.full_name not in self._redeployers:
            self._redeployers[repo.full_name] = GatewayRedeployer(
                service=repo.target_service,
                health_endpoints=repo.health_endpoints,
            )
        return self._redeployers[repo.full_name]

    def refresh_repo_mirror(
        self, repo: DeployRepoConfig | None = None
    ) -> dict[str, Any]:
        """Best-effort ``git fetch origin --prune`` of the persistent repo mirror.

        Event-based freshness: a successful deploy means origin/main moved, so
        pull the mirror in now instead of waiting for the 15-minute systemd
        timer (which remains the authoritative drift backstop).

        The mirror is the *routed* repo's (WS1): an explicitly injected
        ``mirror_repo`` wins (test seam), then the routed repo's
        ``mirror_dir``, which defaults to ``~/.prismatic/repos/<owner>/<repo>``.

        Fail-closed: this never raises. Every failure is logged and returned
        in the result dict; the deploy itself is unaffected.
        """
        routed = repo or self.repo_config
        mirror = self.mirror_repo
        if mirror is None:
            mirror = routed.mirror_dir
        if not mirror.is_dir():
            logger.warning(
                "Repo mirror %s not present; skipping refresh (timer owns creation)",
                mirror,
            )
            return {"refreshed": False, "reason": "mirror-not-present"}
        try:
            proc = subprocess.run(
                ["git", "-C", str(mirror), "fetch", "origin", "--prune"],
                capture_output=True,
                text=True,
                timeout=MIRROR_FETCH_TIMEOUT_S,
            )
        except Exception as exc:  # fail-closed: never break the deploy
            logger.warning("Repo mirror refresh failed (fail-closed): %s", exc)
            return {"refreshed": False, "reason": f"fetch-error: {exc}"}
        if proc.returncode != 0:
            stderr_lines = proc.stderr.strip().splitlines()
            last_line = stderr_lines[-1] if stderr_lines else ""
            logger.warning(
                "Repo mirror refresh failed (fail-closed): rc=%s: %s",
                proc.returncode,
                last_line,
            )
            return {"refreshed": False, "reason": f"git-rc-{proc.returncode}"}
        logger.info("Repo mirror refreshed after successful deploy: %s", mirror)
        return {"refreshed": True, "reason": "fetch-ok"}

    def _fail_closed_unknown_repo(
        self,
        payload: dict[str, Any],
        repository: str,
        reason: str,
        now_iso: str,
        start_time: float,
    ) -> DeployRecord:
        """Fail-closed record for a trigger naming an unregistered repo (WS1).

        The deploy is refused before any side effect; the refusal is recorded
        and alert-logged as ``PostMergeDeployFailed`` naming the repo, so a
        misconfigured trigger can never silently no-op.
        """
        pr_sha = str(payload.get("pr_sha", ""))
        duration_ms = int((time.time() - start_time) * 1000)
        failure_reason = f"refused: unknown repository {repository!r}: {reason}"
        record = DeployRecord(
            deploy_id=f"deploy-{pr_sha[:8] if pr_sha else 'manual'}-refused",
            pr_sha=pr_sha,
            pr_number=int(payload.get("pr_number", 0)),
            pr_title=str(payload.get("pr_title", "")),
            merged_at=payload.get("merged_at", now_iso),
            deployed_at=now_iso,
            deployer=str(payload.get("deployer", "github-action")),
            repository=repository,
            duration_ms=duration_ms,
            success=False,
            failure_reason=failure_reason,
        )
        self.store.record_deploy(record)
        logger.error("deploy REFUSED: %s", failure_reason)
        try:
            emit_deploy_alert(
                "PostMergeDeployFailed",
                "critical",
                f"deploy refused: unknown repository {repository}",
                f"deploy_id={record.deploy_id} repository={repository} "
                f"failure_reason={failure_reason[:300]}",
            )
        except Exception as exc:  # pragma: no cover - emit never raises
            logger.warning("deploy-alerts: refusal emit failed: %s", exc)
        return record

    def process_deploy(
        self,
        payload: dict[str, Any],
        repo_config: DeployRepoConfig | None = None,
    ) -> DeployRecord:
        """Process incoming deploy request payload."""
        start_time = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()

        # WS1: route the trigger to its repo config. An unknown repo is
        # fail-closed (refused record + PostMergeDeployFailed naming it).
        try:
            repo = self._resolve_repo(payload, repo_config)
        except UnknownRepoError as exc:
            return self._fail_closed_unknown_repo(
                payload,
                str(payload.get("repository", "")),
                str(exc),
                now_iso,
                start_time,
            )

        pr_sha = str(payload.get("pr_sha", ""))
        pr_number = int(payload.get("pr_number", 0))
        pr_title = str(payload.get("pr_title", ""))
        deployer = str(payload.get("deployer", "github-action"))
        commits = payload.get("commits", [])

        # The deploy runner for the routed repo (default repo: the injected
        # runner, preserving every existing test double).
        runner = self._runner_for(repo)
        redeployer = self._redeployer_for(repo)
        source_repo_for = self._source_repo_for(repo)

        # Fail-closed dry-run gate: evaluate BEFORE any side effect. A dry run
        # must produce zero deployment side effects (no release dir, no symlink
        # move, no gateway redeploy, no Linear transitions, no mirror refresh).
        # Any truthy dry_run value means "do not deploy" -- ambiguity resolves
        # to no-deploy. (Repair 2026-09-22: the flag was previously read only
        # AFTER deploy() had already run, so a signed dry-run payload deployed.)
        is_dry_run = runner.dry_run or bool(payload.get("dry_run", False))
        if is_dry_run:
            duration_ms = int((time.time() - start_time) * 1000)
            record = DeployRecord(
                deploy_id=f"deploy-{pr_sha[:8] if pr_sha else 'manual'}-dryrun",
                pr_sha=pr_sha,
                pr_number=pr_number,
                pr_title=pr_title,
                merged_at=payload.get("merged_at", now_iso),
                deployed_at=now_iso,
                deployer=deployer,
            repository=repo.full_name,
                version_dir="",
                release_symlink=str(runner.release_symlink),
                health_check={
                    "passed": True,
                    "checks": {},
                    "details": {
                        "dry_run": "skipped: dry-run, zero deployment side effects"
                    },
                },
                linear_transitions=[],
                gateway_deploy={"skipped": True, "reason": "dry-run"},
                mirror_refresh={"refreshed": False, "reason": "skipped: dry-run"},
                duration_ms=duration_ms,
                success=True,
                failure_reason=None,
                dry_run=True,
            )
            # The record is the audit trail for the dry-run decision (every
            # decision emits a signal); dry_run=True marks it so it can never
            # be mistaken for a real deployment.
            self.store.record_deploy(record)
            logger.info("DRY RUN: no deployment performed for %s", record.deploy_id)
            return record

        # Step 1: Execute atomic deploy
        success, version_dir, err_msg = runner.deploy(
            source_repo=source_repo_for,
            pr_sha=pr_sha,
            branch=payload.get("ref", "main"),
        )

        # Step 1b: Real atomic gateway redeploy -- close the merge->prod loop.
        # A merge to main must redeploy the RUNNING gateway, not just record it.
        gateway_info: dict[str, Any] = {}
        if success and not is_dry_run:
            gw_res = redeployer.redeploy(
                pr_sha=pr_sha, repo=source_repo_for, repo_config=repo
            )
            gateway_info = gw_res.to_dict()
            if gw_res.skipped:
                logger.info("Gateway redeploy skipped: %s", gw_res.reason)
            elif not gw_res.success:
                success = False
                err_msg = (
                    f"GATEWAY REDEPLOY FAILED: {gw_res.reason}"
                    + (
                        " [rolled back to previous release]"
                        if gw_res.rolled_back
                        else " [ROLLBACK FAILED -- manual recovery required]"
                    )
                )
        elif is_dry_run:
            gateway_info = {"skipped": True, "reason": "dry-run"}

        # Step 2: Post-deploy health check. A failing health check must NOT
        # overwrite the underlying deploy-step error (2026-09-22: the real
        # rsync ENOSPC error was masked by "version dir missing or invalid").
        # Both are recorded in failure_reason.
        health_res = self.health_checker.check(
            version_dir=version_dir,
            release_symlink=runner.release_symlink,
            dry_run=is_dry_run,
        )

        if not health_res["passed"]:
            health_err = f"Post-deploy health check failed: {health_res['details']}"
            err_msg = f"{err_msg} | {health_err}" if err_msg else health_err
            success = False

        # Step 3: Transition Linear issues if deploy and health check passed
        transitions: list[dict[str, Any]] = []
        record_id = f"deploy-{pr_sha[:8] if pr_sha else 'manual'}"

        if success:
            receipts = self.transitioner.transition_issues_for_deploy(
                deploy_id=record_id,
                pr_sha=pr_sha,
                pr_title=pr_title,
                commit_messages=commits,
            )
            transitions = [r.to_dict() for r in receipts]

        duration_ms = int((time.time() - start_time) * 1000)

        record = DeployRecord(
            deploy_id=record_id,
            pr_sha=pr_sha,
            pr_number=pr_number,
            pr_title=pr_title,
            merged_at=payload.get("merged_at", now_iso),
            deployed_at=now_iso,
            deployer=deployer,
            repository=repo.full_name,
            version_dir=str(version_dir),
            release_symlink=str(runner.release_symlink),
            health_check=health_res,
            linear_transitions=transitions,
            gateway_deploy=gateway_info,
            duration_ms=duration_ms,
            success=success,
            failure_reason=err_msg if not success else None,
        )

        # Step 4: Piggyback the persistent mirror refresh on the deploy event.
        # A successful deploy means origin/main moved: pull the mirror in now
        # instead of waiting for the 15-minute timer. Fail-closed: a refresh
        # failure is logged in the record but never fails the deploy itself;
        # the timer stays as the backstop.
        if success and not is_dry_run:
            record.mirror_refresh = self.refresh_repo_mirror(repo)
        elif is_dry_run:
            record.mirror_refresh = {"refreshed": False, "reason": "skipped: dry-run"}
        else:
            record.mirror_refresh = {
                "refreshed": False,
                "reason": "skipped: deploy failed",
            }

        # Step 5: Persist deploy record
        self.store.record_deploy(record)

        # Step 5b: Alert-log the pipeline's terminal state (additive only).
        # This sits AFTER the dry-run early return above, so dry runs keep
        # zero side effects. Event names are pipeline-level to distinguish
        # them from the gateway-redeploy step events in gateway_redeploy.py.
        # emit_deploy_alert never raises by contract; the guard below is
        # belt-and-braces so logging can never break the deploy path.
        try:
            if record.success:
                version_name = Path(str(version_dir)).name if version_dir else ""
                emit_deploy_alert(
                    "PostMergeDeploySucceeded",
                    "info",
                    f"deploy {record.deploy_id} succeeded: gateway at {pr_sha[:12]}",
                    f"deploy_id={record.deploy_id} pr_sha={pr_sha} "
                    f"pr_number={pr_number} version_dir={version_name} "
                    f"duration_ms={duration_ms}",
                )
            else:
                gw = record.gateway_deploy or {}
                emit_deploy_alert(
                    "PostMergeDeployFailed",
                    "critical",
                    f"deploy {record.deploy_id} failed: {str(err_msg)[:120]}",
                    f"deploy_id={record.deploy_id} pr_sha={pr_sha} "
                    f"failure_reason={str(err_msg)[:300]} "
                    f"rolled_back={gw.get('rolled_back', False)}",
                )
        except Exception as exc:  # pragma: no cover - emit never raises
            logger.warning("deploy-alerts: terminal-state emit failed: %s", exc)

        # Step 6: Feed the review factory's learn loop. Deploy success and
        # gateway rollback are the mechanical ground truth for merge
        # outcomes. record_outcome() refuses while the learn loop is
        # disabled, so this wiring is inert until the rollout ladder
        # advances it — safe to land now. A feed failure is logged, never
        # raised: the deploy already happened.
        self._feed_learn_loop(record)

        # Watchdog metrics feed (Phase 0 observe-only): record a
        # production rollback of an auto-merge. Append-only, never raises.
        self._feed_watchdog_rollback(record)

        return record

    def _feed_learn_loop(self, record: DeployRecord) -> None:
        """Report one deploy's mechanical outcome to the learn loop.

        Outcome mapping — the only ground truth the loop may consume:
          - gateway redeploy rolled back -> "rolled_back"
          - deploy succeeded              -> "clean"
          - failed without gateway rollback -> nothing recorded: prod is in
            an unknown state and an invented outcome would poison the loop.

        The loop joins outcomes to its decision log by job_id; the join
        here maps the deploy's pr_sha to the merge-authority decision row's
        merge_sha (falling back to head_sha). record_outcome() itself
        refuses unknown jobs, duplicates, and every call while the loop is
        disabled.
        """
        if getattr(record, "dry_run", False):
            return
        try:
            from prismatic.review_factory.learn_loop import (
                OUTCOME_CLEAN,
                OUTCOME_ROLLED_BACK,
                LearnLoop,
            )
        except Exception as exc:  # pragma: no cover - import-time failure
            logger.warning("learn-loop feed skipped (import failed): %s", exc)
            return

        gateway = record.gateway_deploy or {}
        if gateway.get("rolled_back"):
            outcome = OUTCOME_ROLLED_BACK
        elif record.success:
            outcome = OUTCOME_CLEAN
        else:
            logger.info(
                "learn-loop feed: deploy %s failed without a gateway rollback; "
                "no outcome recorded (unknown production state)",
                record.deploy_id,
            )
            return

        job_id = self._learn_loop_job_id(record.pr_sha)
        if job_id is None:
            logger.info(
                "learn-loop feed: no merge-authority decision row for pr_sha %s; "
                "outcome %r not recorded",
                record.pr_sha,
                outcome,
            )
            return
        try:
            result = LearnLoop().record_outcome(job_id, outcome)
        except Exception as exc:
            logger.warning("learn-loop feed failed for job %s: %s", job_id, exc)
            return
        logger.info("learn-loop feed outcome: %s", result)

    def _feed_watchdog_rollback(self, record: DeployRecord) -> None:
        """Record a gateway rollback in the watchdog metrics feed.

        Phase 0 observe-only: appends one rollback event row when the
        gateway redeploy rolled back to the previous release. The merge
        authority job id is joined from the deploys pr_sha, exactly like
        the learn-loop feed; when the join finds nothing the event is
        skipped rather than invented. Never raises and never alters the
        deploy outcome.
        """
        if getattr(record, "dry_run", False):
            return
        gateway = record.gateway_deploy or {}
        if not gateway.get("rolled_back"):
            return
        job_id = self._learn_loop_job_id(record.pr_sha)
        if job_id is None:
            logger.info(
                "watchdog feed: no merge-authority decision row for pr_sha %s; "
                "rollback not recorded",
                record.pr_sha,
            )
            return
        failure_note = record.failure_reason or "health check failed"
        try:
            from prismatic.review_factory.metrics_feed import record_rollback

            record_rollback(
                job_id=job_id,
                merge_sha=record.pr_sha,
                reason=(
                    f"gateway redeploy rolled back deploy {record.deploy_id} "
                    f"to previous release: {failure_note}"
                ),
            )
        except Exception:
            logger.warning(
                "watchdog feed record_rollback failed for deploy %s",
                record.deploy_id,
                exc_info=True,
            )

    @staticmethod
    def _learn_loop_job_id(pr_sha: str) -> str | None:
        """Newest merge-authority decision row for the deployed pr_sha.

        Joins on ``merge_sha`` == pr_sha, falling back to ``head_sha``;
        only "allowed" decisions count (a refused decision never merged).
        Returns the row's job_id — the learn loop's join key — or None.
        """
        if not pr_sha:
            return None
        try:
            from prismatic.review_factory.learn_loop import DEFAULT_DECISION_LOG
            from prismatic.review_factory.merge_authority import DECISION_ALLOWED

            path = Path(DEFAULT_DECISION_LOG)
            if not path.exists():
                return None
            rows: list[dict[str, Any]] = []
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # corrupt line: skip, never crash the feed
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            return None
        for row in reversed(rows):
            if row.get("decision") != DECISION_ALLOWED:
                continue
            if row.get("merge_sha") == pr_sha or row.get("head_sha") == pr_sha:
                job_id = row.get("job_id")
                if isinstance(job_id, str) and job_id:
                    return job_id
        return None


def create_deploy_receiver_app() -> Any:
    """Create FastAPI receiver application for port 9460."""
    if not _HAS_FASTAPI:
        return None

    app = FastAPI(title="Prismatic Deploy Receiver", version="1.0.0")
    pipeline = DeployReceiverPipeline()

    @app.post("/deploy")
    async def handle_deploy(
        request: Request,
        x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    ) -> Dict[str, Any]:
        body_bytes = await request.body()

        # Parse first: the payload's ``repository`` field selects the repo
        # config (WS1), and the per-repo HMAC secret cannot be resolved
        # without it.
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        # Fail-closed routing: a trigger without a repository field is
        # refused -- the receiver never guesses which repo a deploy is for.
        repository = payload.get("repository")
        if not repository or not isinstance(repository, str):
            raise HTTPException(
                status_code=400,
                detail=(
                    "payload is missing the required 'repository' field "
                    "(owner/repo); refusing to route an unidentified deploy"
                ),
            )

        # Fail-closed: unknown repos are refused loudly and alert-logged as
        # PostMergeDeployFailed naming the repo (never silently skipped).
        registry = load_repo_registry()
        try:
            repo = registry.get(repository)
        except UnknownRepoError as exc:
            logger.error("deploy trigger refused: %s", exc)
            try:
                emit_deploy_alert(
                    "PostMergeDeployFailed",
                    "critical",
                    f"deploy trigger refused: unknown repository {repository}",
                    f"repository={repository} reason=unknown-repo-not-in-registry",
                )
            except Exception:  # pragma: no cover - emit never raises
                logger.warning("deploy-alerts: refusal emit failed")
            raise HTTPException(
                status_code=400,
                detail=f"unknown repository {repository!r}: not in the deploy registry",
            )

        # Per-repo HMAC secret (shared DEPLOY_HMAC_SECRET fallback). A routed
        # repo with no usable secret is a loud refusal, never a skip.
        try:
            hmac_secret = get_repo_hmac_secret(repo)
        except RuntimeError as exc:
            logger.error(
                "deploy trigger for %s refused: %s", repository, exc
            )
            raise HTTPException(status_code=500, detail=str(exc))

        # Verify HMAC signature (§16.8 anti-pattern #3)
        if not verify_hmac_signature(body_bytes, x_hub_signature_256, secret=hmac_secret):
            raise HTTPException(
                status_code=401, detail="Invalid or missing HMAC signature"
            )

        # The deploy is blocking; run it in a worker thread so /health stays
        # responsive during deploys, and fail loudly on an overall timeout
        # instead of hanging the workflow forever.
        try:
            record = await asyncio.wait_for(
                asyncio.to_thread(pipeline.process_deploy, payload, repo),
                timeout=DEPLOY_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error(
                "deploy for pr_sha %s timed out after %ss",
                payload.get("pr_sha"),
                DEPLOY_TIMEOUT_S,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "status": "failed",
                    "error": f"deploy timed out after {DEPLOY_TIMEOUT_S}s",
                },
            )

        body = {
            "status": "success" if record.success else "failed",
            "deploy_record": record.to_dict(),
        }
        # A failed deploy must fail loudly: the workflow's curl -f turns this
        # into a red run instead of a green lie.
        if not record.success:
            return JSONResponse(status_code=500, content=body)
        return body

    @app.get("/health")
    async def receiver_health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "port": RECEIVER_PORT,
            "time": datetime.now(timezone.utc).isoformat(),
        }

    return app


_app: Any = None


def get_app() -> Any:
    """Return the deploy receiver FastAPI app, creating it on first call.

    Lazy by design: importing this module must have no side effects, because
    other processes import it without receiver configuration -- notably the
    gateway, which imports DeployReceiverPipeline via prismatic.deploy.routes
    (2026-09-22: module-level app creation crashed the gateway on every
    deploy of #528, because the gateway has no PRISMATIC_DEPLOY_SOURCE_REPO).
    The fail-fast on a missing PRISMATIC_DEPLOY_SOURCE_REPO still fires here,
    at actual receiver startup, never at import.
    """
    global _app
    if _app is None:
        _app = create_deploy_receiver_app()
    return _app


def __getattr__(name: str) -> Any:
    """PEP 562: resolve ``pe.deploy.receiver:app`` lazily.

    Keeps the ``"pe.deploy.receiver:app"`` import string used by uvicorn (and
    the ``__main__`` block below) working without building the app at import
    time. Any other name falls through to the normal AttributeError.
    """
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "pe.deploy.receiver:app", host="0.0.0.0", port=RECEIVER_PORT, reload=False
    )
