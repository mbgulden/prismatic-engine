"""Local HTTP Deploy Receiver listening on port 9460 (WB-2).

Corresponds to §7.1 - §7.4 and R2 of okf-docs-workspace-deploy-v1.md.
Validates HMAC-SHA256 signature (DEPLOY_HMAC_SECRET), triggers deploy pipeline, runs health checks, and transitions Linear issues.
"""

from __future__ import annotations

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

logger = logging.getLogger(__name__)

RECEIVER_PORT = 9460

#: Default location of the persistent private-repo mirror that the gateway's
#: repo-dir activation checks read. Refreshed eagerly on each successful
#: deploy (piggyback) so the mirror tracks origin/main without waiting for
#: the 15-minute systemd timer, which stays as the drift backstop.
MIRROR_REPO_RELATIVE = Path(".prismatic/repos/mbgulden/prismatic-engine")
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
    if os.environ.get("PRISMATIC_ALLOW_DEFAULT_HMAC") == "1" or not os.environ.get("PRISMATIC_STRICT_SECRETS"):
        logger.warning("DEPLOY_HMAC_SECRET not set; using default dev HMAC secret.")
        return "prismatic-deploy-hmac-secret-v1"

    raise RuntimeError(
        "DEPLOY_HMAC_SECRET environment variable or .env entry is missing! "
        "Set DEPLOY_HMAC_SECRET in .env or environment to enforce production security."
    )


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
    ):
        self.source_repo = source_repo or Path(".").resolve()
        self.deploy_runner = deploy_runner or AtomicDeployRunner()
        self.health_checker = health_checker or PostDeployHealthChecker()
        self.transitioner = transitioner or LinearDeployTransitioner()
        self.store = store or DeployManifestStore()
        self.gateway_redeployer = gateway_redeployer or GatewayRedeployer()
        self.mirror_repo = mirror_repo

    def refresh_repo_mirror(self) -> dict[str, Any]:
        """Best-effort ``git fetch origin --prune`` of the persistent repo mirror.

        Event-based freshness: a successful deploy means origin/main moved, so
        pull the mirror in now instead of waiting for the 15-minute systemd
        timer (which remains the authoritative drift backstop).

        Fail-closed: this never raises. Every failure is logged and returned
        in the result dict; the deploy itself is unaffected.
        """
        mirror = self.mirror_repo
        if mirror is None:
            mirror = Path.home() / MIRROR_REPO_RELATIVE
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

    def process_deploy(
        self,
        payload: dict[str, Any],
    ) -> DeployRecord:
        """Process incoming deploy request payload."""
        start_time = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()

        pr_sha = str(payload.get("pr_sha", ""))
        pr_number = int(payload.get("pr_number", 0))
        pr_title = str(payload.get("pr_title", ""))
        deployer = str(payload.get("deployer", "github-action"))
        commits = payload.get("commits", [])

        # Fail-closed dry-run gate: evaluate BEFORE any side effect. A dry run
        # must produce zero deployment side effects (no release dir, no symlink
        # move, no gateway redeploy, no Linear transitions, no mirror refresh).
        # Any truthy dry_run value means "do not deploy" -- ambiguity resolves
        # to no-deploy. (Repair 2026-09-22: the flag was previously read only
        # AFTER deploy() had already run, so a signed dry-run payload deployed.)
        is_dry_run = self.deploy_runner.dry_run or bool(payload.get("dry_run", False))
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
                version_dir="",
                release_symlink=str(self.deploy_runner.release_symlink),
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
        success, version_dir, err_msg = self.deploy_runner.deploy(
            source_repo=self.source_repo,
            pr_sha=pr_sha,
            branch=payload.get("ref", "main"),
        )

        # Step 1b: Real atomic gateway redeploy -- close the merge->prod loop.
        # A merge to main must redeploy the RUNNING gateway, not just record it.
        gateway_info: dict[str, Any] = {}
        if success and not is_dry_run:
            gw_res = self.gateway_redeployer.redeploy(
                pr_sha=pr_sha, repo=self.source_repo
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

        # Step 2: Post-deploy health check
        health_res = self.health_checker.check(
            version_dir=version_dir,
            release_symlink=self.deploy_runner.release_symlink,
            dry_run=is_dry_run,
        )

        if not health_res["passed"]:
            success = False
            err_msg = f"Post-deploy health check failed: {health_res['details']}"

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
            version_dir=str(version_dir),
            release_symlink=str(self.deploy_runner.release_symlink),
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
            record.mirror_refresh = self.refresh_repo_mirror()
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

        # Verify HMAC signature (§16.8 anti-pattern #3)
        if not verify_hmac_signature(body_bytes, x_hub_signature_256):
            raise HTTPException(
                status_code=401, detail="Invalid or missing HMAC signature"
            )

        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        record = pipeline.process_deploy(payload)

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


app = create_deploy_receiver_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "pe.deploy.receiver:app", host="0.0.0.0", port=RECEIVER_PORT, reload=False
    )
