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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse

    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from pe.deploy.health import PostDeployHealthChecker
from pe.deploy.integrate import AtomicDeployRunner
from pe.deploy.linear_transition import LinearDeployTransitioner
from pe.deploy.manifest import DeployManifestStore, DeployRecord

logger = logging.getLogger(__name__)

RECEIVER_PORT = 9460


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
    ):
        self.source_repo = source_repo or Path(".").resolve()
        self.deploy_runner = deploy_runner or AtomicDeployRunner()
        self.health_checker = health_checker or PostDeployHealthChecker()
        self.transitioner = transitioner or LinearDeployTransitioner()
        self.store = store or DeployManifestStore()

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

        # Step 1: Execute atomic deploy
        success, version_dir, err_msg = self.deploy_runner.deploy(
            source_repo=self.source_repo,
            pr_sha=pr_sha,
            branch=payload.get("ref", "main"),
        )

        is_dry_run = self.deploy_runner.dry_run or bool(payload.get("dry_run", False))

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
            duration_ms=duration_ms,
            success=success,
            failure_reason=err_msg if not success else None,
        )

        # Step 4: Persist deploy record
        self.store.record_deploy(record)

        return record


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

        return {
            "status": "success" if record.success else "failed",
            "deploy_record": record.to_dict(),
        }

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
