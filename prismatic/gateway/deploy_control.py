"""Deploy control API for the Prismatic portal (Portal Plan P0 #1).

Mounts under ``/api/deploys/``::

    POST /api/deploys                  trigger a deploy for a repo
    POST /api/deploys/{id}/rollback    roll a deploy back to the previous release
    GET  /api/deploys                  deploy history, sourced from alerts.log
    GET  /api/deploys/releases         per-repo release state (read-only)

Design notes:

* **Trigger reuses the post-merge webhook path exactly.** The gateway
  resolves the repo and the ref, signs a payload with the repo's HMAC
  secret, and POSTs it to the deploy receiver's ``/deploy`` endpoint --
  the same intake the GitHub workflow hits. No deploy logic is duplicated
  here: routing, mirror refresh, the atomic deploy, health checks,
  rollback-on-failure, alerts, and records all run in the receiver
  (``pe.deploy.receiver``). This module only orchestrates.
* **Trigger is async (202 Accepted).** Deploys take minutes and a trigger
  for the gateway's own repo restarts the gateway mid-request; the
  dispatch runs in a thread and the outcome lands in alerts.log, the
  deploy manifest, and deploy.* events on /ws. The 202 carries a
  ``trigger_id`` and the ``pr_sha`` so the caller can follow up with
  ``GET /api/deploys?repo=...``.
* **Rollback reuses the automatic-rollback code path**
  (``GatewayRedeployer._rollback`` -- the pilot proved its
  ``GatewayRollbackStarted``/``GatewayRollbackCompleted`` alerts) with a
  ``GatewayDeployResult`` reconstructed from the deploy manifest. The
  gateway refuses to roll back its *own* live release from inside itself
  (409): a gateway self-restart would kill the verifying request thread,
  so gateway self-recovery stays with the receiver-side automatic path.
* **Role gating** comes from ``prismatic.gateway.control_auth``: both POST
  routes resolve to the ``operator`` role there; the GET routes are
  read-only and ungated, matching the Viewer-can-read model.
* **Receipts:** every trigger and rollback writes a control receipt
  (``deploy_receipts.py``) -- who, when, what, before/after -- extending
  the verification-receipts pattern. Receipting never raises.
* **Read-only promises:** history parses alerts.log; releases only
  readlinks and lists dirs under the state dir. Nothing here writes
  releases, moves symlinks (except rollback's reuse of ``_rollback``),
  or restarts services except through the reused rollback path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query, Request
    from pydantic import BaseModel

    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover - gateway always has FastAPI
    _HAS_FASTAPI = False

from pe.deploy.config import (
    UnknownRepoError,
    alert_log_path,
    load_repo_registry,
    state_dir,
)
from pe.deploy.gateway_redeploy import GatewayDeployResult, GatewayRedeployer
from pe.deploy.manifest import DeployManifestStore, DeployRecord

from prismatic.gateway.deploy_receipts import write_control_receipt

logger = logging.getLogger(__name__)

#: Deploy receiver intake URL (the post-merge webhook path).
RECEIVER_URL_ENV = "PRISMATIC_DEPLOY_RECEIVER_URL"
DEFAULT_RECEIVER_URL = "http://127.0.0.1:9460"

#: Receiver env file holding the per-repo HMAC secrets (0600, same box).
#: This is the HOME-based ``~/.prismatic/env.d/deploy-receiver.env`` file the
#: receiver's own systemd drop-in sources -- NOT ``state_dir()/env.d/...``:
#: in production ``PRISMATIC_STATE_DIR`` is ``~/.prismatic/db``, so resolving
#: against ``state_dir()`` points at a path that does not exist.
RECEIVER_ENV_PATH = Path.home() / ".prismatic" / "env.d" / "deploy-receiver.env"

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_DETAIL_KV_RE = re.compile(r"(\w+)=([^\s]+)")

#: alerts.log entry names that belong to the deploy lifecycle, mapped to
#: the history ``type`` surfaced by GET /api/deploys.
_DEPLOY_EVENT_TYPES = {
    "PostMergeDeploySucceeded": "deploy_succeeded",
    "PostMergeDeployFailed": "deploy_failed",
    "GatewayDeployFailed": "deploy_failed",
    "GatewayDeployHealthCheckFailed": "deploy_failed",
    "GatewayRollbackStarted": "rollback_started",
    "GatewayRollbackCompleted": "rollback_completed",
    "GatewayRollbackFailed": "rollback_failed",
}


class TriggerRequest(BaseModel):
    repo: str
    ref: str | None = None
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Small helpers (pure / read-only)
# ---------------------------------------------------------------------------


def _receiver_base_url() -> str:
    return os.environ.get(RECEIVER_URL_ENV, "").strip() or DEFAULT_RECEIVER_URL


def _read_env_file_values(path: Path) -> dict[str, str]:
    """Read VAR=value pairs from an env file (values never logged)."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            values[key] = value.strip().strip("\"'")
    return values


def _repo_hmac_secret(repo: Any) -> str:
    """Resolve the HMAC secret the receiver expects for one repo.

    Precedence mirrors the receiver: the repo's per-repo env var, then the
    shared ``DEPLOY_HMAC_SECRET`` -- read from the process environment
    first, then the receiver env file on this box (the gateway unit does
    not source that file, but it runs as the same user and may read it).
    """
    var = repo.hmac_secret_env
    file_values = _read_env_file_values(RECEIVER_ENV_PATH)
    for candidate in (var, "DEPLOY_HMAC_SECRET"):
        value = os.environ.get(candidate, "").strip()
        if value:
            return value
        value = file_values.get(candidate, "").strip()
        if value:
            return value
    raise HTTPException(
        status_code=500,
        detail=(
            f"no HMAC secret available for repository {repo.full_name!r}: "
            f"{var} / DEPLOY_HMAC_SECRET are set neither in the gateway "
            "environment nor in the receiver env file"
        ),
    )


def _secret_available(repo: Any) -> bool:
    """Non-raising check: is there an HMAC secret for this repo?

    Mirrors :func:`_repo_hmac_secret`'s precedence (per-repo env var, then
    the shared ``DEPLOY_HMAC_SECRET``; process env, then the receiver env
    file) without raising. Used for the read-only releases listing.
    """
    var = repo.hmac_secret_env
    file_values = _read_env_file_values(RECEIVER_ENV_PATH)
    for candidate in (var, "DEPLOY_HMAC_SECRET"):
        if os.environ.get(candidate, "").strip():
            return True
        if file_values.get(candidate, "").strip():
            return True
    return False


def _sign_payload(secret: str, body_bytes: bytes) -> str:
    """Build the X-Hub-Signature-256 value the receiver verifies."""
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _control_actor(request: Request) -> str:
    return getattr(request.state, "control_actor", None) or "unknown"


def _live_release_name(repo: Any) -> str:
    """Name of the release dir a repo's live ``current`` link points at."""
    redeployer = _redeployer_for_repo(repo)
    target = redeployer._readlink(redeployer.current_link)  # noqa: SLF001
    return target.name if target is not None else ""


def _redeployer_for_repo(repo: Any) -> GatewayRedeployer:
    """Gateway redeployer for one repo -- same construction as the
    receiver pipeline's per-repo redeployer (no pipeline needed)."""
    return GatewayRedeployer(
        service=repo.target_service,
        health_endpoints=repo.health_endpoints,
        release_prefix=repo.release_prefix,
        port=repo.port,
        extras=repo.extras,
        smoke_import=repo.smoke_import,
    )


def _resolve_ref_to_sha(repo: Any, ref: str | None) -> str:
    """Resolve a trigger ref to a commit SHA using the repo's mirror.

    A bare SHA passes through (the receiver verifies it is a real commit
    and an ancestor of origin/main). Otherwise the mirror is fetched and
    the ref -- default ``origin/main`` -- is rev-parsed. Fail-closed with
    a 4xx naming the repo and the mirror.
    """
    want = (ref or "").strip()
    if want and _SHA_RE.match(want):
        return want
    mirror = Path(repo.mirror_dir)
    if not (mirror / "HEAD").is_file():
        raise HTTPException(
            status_code=400,
            detail=(
                f"no git mirror for {repo.full_name!r} at {mirror}; "
                f"onboard it with `prismatic deploy add-repo {repo.full_name}`"
            ),
        )
    rev = want or "origin/main"
    try:
        fetch = subprocess.run(
            ["git", "-C", str(mirror), "fetch", "origin", "--prune"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"mirror fetch for {repo.full_name!r} failed: {exc}"
        ) from exc
    if fetch.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=(
                f"mirror fetch for {repo.full_name!r} failed: "
                f"{(fetch.stderr or '').strip()[:200]}"
            ),
        )
    try:
        cp = subprocess.run(
            ["git", "-C", str(mirror), "rev-parse", rev],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ref resolution for {repo.full_name!r} failed: {exc}",
        ) from exc
    if cp.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"cannot resolve ref {rev!r} in the {repo.full_name!r} mirror "
                f"({(cp.stderr or '').strip()[:200]}); "
                "bare mirrors need `git -C <mirror> fetch origin` first"
            ),
        )
    sha = cp.stdout.strip()
    if not _SHA_RE.match(sha):
        raise HTTPException(
            status_code=502,
            detail=f"ref {rev!r} resolved to a non-SHA value for {repo.full_name!r}",
        )
    return sha


def _post_to_receiver(payload: dict[str, Any], repo: Any) -> dict[str, Any]:
    """Sign a deploy payload and POST it to the receiver's /deploy intake.

    This is the post-merge webhook path: the receiver verifies the HMAC,
    routes by ``repository``, and runs the full deploy pipeline. Separated
    for tests (monkeypatch this, not the network).
    """
    import urllib.request

    body = json.dumps(payload).encode("utf-8")
    secret = _repo_hmac_secret(repo)
    url = _receiver_base_url().rstrip("/") + "/deploy"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign_payload(secret, body),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        logger.error("deploy-control: receiver POST failed: %s", exc)
        raise
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"raw": raw[:500]}
    return {"http_status": resp.getcode(), "body": data}


def _dispatch_trigger(payload: dict[str, Any], repo: Any, trigger_id: str) -> None:
    """Thread target: hand the trigger to the receiver; log the outcome."""
    try:
        result = _post_to_receiver(payload, repo)
        logger.info(
            "deploy-control: trigger %s for %s dispatched, receiver replied %s",
            trigger_id,
            repo.full_name,
            result.get("http_status"),
        )
    except Exception as exc:  # the receipt + alerts.log stay the record
        logger.error(
            "deploy-control: trigger %s for %s failed to reach the receiver: %s",
            trigger_id,
            repo.full_name,
            exc,
        )


# ---------------------------------------------------------------------------
# History: alerts.log parsing (+ manifest union for dry runs)
# ---------------------------------------------------------------------------


def _parse_detail_kv(details: str) -> dict[str, str]:
    return dict(_DETAIL_KV_RE.findall(details or ""))


def _manifest_records_by_id(store: DeployManifestStore) -> dict[str, DeployRecord]:
    try:
        return {r.deploy_id: r for r in store.list_deploys(limit=1000)}
    except Exception as exc:
        logger.warning("deploy-control: manifest read failed: %s", exc)
        return {}


def _repo_for_release_name(
    release_name: str, registry: Any, records: dict[str, DeployRecord]
) -> str:
    """Attribute a release dir name to a repo via its release prefix."""
    for repo in registry:
        if release_name == repo.release_prefix or release_name.startswith(
            repo.release_prefix + "-"
        ):
            return repo.full_name
    return "unknown"


def parse_deploy_history(
    *,
    log_path: Path | None = None,
    store: DeployManifestStore | None = None,
) -> list[dict[str, Any]]:
    """Parse alerts.log deploy-lifecycle entries into history items.

    Each item: timestamp, type, severity, summary, deploy_id, pr_sha, repo,
    source. Manifest records with no matching alert entry (e.g. dry runs,
    which the pipeline deliberately keeps out of alerts.log) are unioned
    in with ``source: "manifest"`` so a trigger is never invisible.
    Malformed lines are skipped, never fatal. Newest first.
    """
    path = log_path or alert_log_path()
    try:
        registry = load_repo_registry()
    except Exception as exc:  # read-only path: degrade, don't 500
        logger.warning("deploy-control: registry load failed: %s", exc)
        registry = ()
    manifest = store or DeployManifestStore()
    records = _manifest_records_by_id(manifest)

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        name = entry.get("name", "")
        if name not in _DEPLOY_EVENT_TYPES:
            continue
        kv = _parse_detail_kv(str(entry.get("details", "")))
        deploy_id = kv.get("deploy_id", "")
        record = records.get(deploy_id)
        release_name = (
            kv.get("version_dir")
            or kv.get("failed_release")
            or kv.get("attempted_release")
            or kv.get("restored_release")
            or ""
        )
        repo = (
            record.repository
            if record and record.repository
            else _repo_for_release_name(release_name, registry, records)
        )
        items.append(
            {
                "deploy_id": deploy_id,
                "timestamp": entry.get("timestamp", ""),
                "type": _DEPLOY_EVENT_TYPES[name],
                "alert": name,
                "severity": entry.get("severity", "info"),
                "summary": entry.get("summary", ""),
                "repo": repo,
                "pr_sha": kv.get("pr_sha", ""),
                "source": "alerts.log",
            }
        )
        if deploy_id:
            seen_ids.add(deploy_id)

    # Union: manifest records with no alert entry (dry runs stay out of the
    # alert log by pipeline design; they must still show in history).
    for record in records.values():
        if record.deploy_id in seen_ids:
            continue
        items.append(
            {
                "deploy_id": record.deploy_id,
                "timestamp": record.deployed_at,
                "type": "dry_run"
                if getattr(record, "dry_run", False)
                else "deploy_succeeded"
                if record.success
                else "deploy_failed",
                "alert": "",
                "severity": "info" if record.success else "critical",
                "summary": (
                    f"deploy {record.deploy_id} "
                    f"({'dry run' if getattr(record, 'dry_run', False) else 'succeeded' if record.success else 'failed'})"
                ),
                "repo": record.repository or "unknown",
                "pr_sha": record.pr_sha,
                "source": "manifest",
            }
        )

    items.sort(key=lambda item: item["timestamp"], reverse=True)
    return items


# ---------------------------------------------------------------------------
# Releases: per-repo release state (read-only)
# ---------------------------------------------------------------------------


def _readlink_name(link: Path) -> str | None:
    try:
        target = Path(os.readlink(link))
    except OSError:
        return None
    return target.name


def list_repo_releases(
    *, store: DeployManifestStore | None = None
) -> list[dict[str, Any]]:
    """Per-repo release state. Read-only: readlinks + dir listing only."""
    try:
        registry = load_repo_registry()
    except Exception as exc:  # read-only path: degrade, don't 500
        logger.warning("deploy-control: registry load failed: %s", exc)
        return []
    manifest = store or DeployManifestStore()
    prismatic = state_dir()
    releases_dir = prismatic / "releases"

    latest_by_repo: dict[str, DeployRecord] = {}
    for record in manifest.list_deploys(limit=1000):
        if record.repository and record.repository not in latest_by_repo:
            latest_by_repo[record.repository] = record

    repos: list[dict[str, Any]] = []
    for repo in registry:
        prefix = repo.release_prefix
        redeployer = _redeployer_for_repo(repo)
        current = _readlink_name(redeployer.current_link)
        current_venv = _readlink_name(redeployer.venv_link)

        releases: list[dict[str, Any]] = []
        try:
            entries = sorted(releases_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            entries = []
        for entry in entries:
            name = entry.name
            if name != prefix and not name.startswith(prefix + "-"):
                continue
            sha = name[len(prefix) + 1 :] if name.startswith(prefix + "-") else ""
            try:
                modified = datetime.fromtimestamp(
                    entry.stat().st_mtime, tz=timezone.utc
                ).isoformat()
            except OSError:
                modified = ""
            releases.append(
                {
                    "name": name,
                    "sha": sha,
                    "is_current": name == current,
                    "is_dir": entry.is_dir(),
                    "modified": modified,
                }
            )
        releases.sort(key=lambda r: r["modified"], reverse=True)

        latest = latest_by_repo.get(repo.full_name)
        repos.append(
            {
                "repo": repo.full_name,
                "release_prefix": prefix,
                "target_service": repo.target_service,
                "target_node": repo.target_node,
                "mirror_present": (Path(repo.mirror_dir) / "HEAD").is_file(),
                "secret_configured": _secret_available(repo),
                "current_release": current,
                "current_venv": current_venv,
                "releases": releases,
                "latest_deploy": (
                    {
                        "deploy_id": latest.deploy_id,
                        "pr_sha": latest.pr_sha,
                        "success": latest.success,
                        "deployed_at": latest.deployed_at,
                        "deployer": latest.deployer,
                    }
                    if latest
                    else None
                ),
            }
        )
    return repos


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def _rollback_deploy(deploy_id: str, actor: str) -> dict[str, Any]:
    """Roll a deploy back to the previous release via the reused automatic
    rollback path. Raises HTTPException on any refusal/failure."""
    store = DeployManifestStore()
    records = {r.deploy_id: r for r in store.list_deploys(limit=1000)}
    record = records.get(deploy_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"deploy {deploy_id!r} not found")

    try:
        repo = load_repo_registry().get(record.repository or "")
    except UnknownRepoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # The gateway cannot roll back its own live release from inside itself:
    # the rollback restarts the target service, which would kill the
    # verifying request thread. Gateway self-recovery stays with the
    # receiver-side automatic rollback path.
    registry = load_repo_registry()
    if repo.full_name == registry.default().full_name:
        raise HTTPException(
            status_code=409,
            detail=(
                f"refusing to roll back {repo.full_name!r} from the gateway API: "
                "the target service is the gateway itself and the restart "
                "would kill this request. Gateway self-recovery runs in the "
                "deploy receiver (automatic rollback on failed deploy)."
            ),
        )
    if repo.target_node != "local":
        raise HTTPException(
            status_code=409,
            detail=(
                f"refusing to roll back {repo.full_name!r}: node-targeted "
                f"({repo.target_node}) rollback is not supported in Phase 1"
            ),
        )

    repo_records = sorted(
        (
            r
            for r in records.values()
            if r.repository == repo.full_name
            and r.success
            and not getattr(r, "dry_run", False)
        ),
        key=lambda r: r.deployed_at,
        reverse=True,
    )
    if not repo_records or repo_records[0].deploy_id != deploy_id:
        latest = repo_records[0].deploy_id if repo_records else "none"
        raise HTTPException(
            status_code=409,
            detail=(
                f"refusing to roll back {deploy_id!r}: it is not the latest "
                f"successful deploy of {repo.full_name!r} (latest: {latest}); "
                "rolling back a superseded deploy is ambiguous"
            ),
        )
    if len(repo_records) < 2:
        raise HTTPException(
            status_code=409,
            detail=(
                f"refusing to roll back {deploy_id!r}: no previous successful "
                f"deploy of {repo.full_name!r} to restore"
            ),
        )
    previous = repo_records[1]

    redeployer = _redeployer_for_repo(repo)
    live = _readlink_name(redeployer.current_link)
    live_venv = _readlink_name(redeployer.venv_link)
    current_name = Path(record.version_dir).name if record.version_dir else ""
    if live != current_name:
        raise HTTPException(
            status_code=409,
            detail=(
                f"refusing to roll back {deploy_id!r}: the live release "
                f"({live or 'none'}) is not this deploy's release "
                f"({current_name or 'unknown'}); the world moved underneath"
            ),
        )

    prev_name = Path(previous.version_dir).name if previous.version_dir else ""
    prev_sha = (
        prev_name[len(repo.release_prefix) + 1 :]
        if prev_name.startswith(repo.release_prefix + "-")
        else ""
    )
    prev_venv_name = f"{repo.release_prefix}-{prev_sha}" if prev_sha else ""
    prev_dir = redeployer.releases_dir / prev_name
    prev_venv_dir = redeployer.venvs_dir / prev_venv_name
    if not prev_name or not prev_dir.is_dir():
        raise HTTPException(
            status_code=409,
            detail=(
                f"refusing to roll back {deploy_id!r}: previous release dir "
                f"{prev_name or 'unknown'} is missing under {redeployer.releases_dir}"
            ),
        )
    if not prev_venv_name or not prev_venv_dir.is_dir():
        raise HTTPException(
            status_code=409,
            detail=(
                f"refusing to roll back {deploy_id!r}: previous venv "
                f"{prev_venv_name or 'unknown'} is missing under {redeployer.venvs_dir}"
            ),
        )

    # Reuse the automatic rollback path: same symlink restore, same service
    # restart, same verification, same GatewayRollbackStarted/Completed
    # alerts. flipped_* = True because both links were flipped by the deploy.
    res = GatewayDeployResult(
        pr_sha=record.pr_sha,
        version_dir=str(redeployer.releases_dir / current_name) if current_name else "",
        venv_dir=str(redeployer.venvs_dir / live_venv) if live_venv else "",
        previous_version_dir=str(prev_dir),
        previous_venv_dir=str(prev_venv_dir),
    )
    before = {"live_release": live, "live_venv": live_venv}
    after = {
        "restored_release": prev_name,
        "restored_venv": prev_venv_name,
        "rolled_back_from": current_name,
    }
    ok = redeployer._rollback(res, flipped_venv=True, flipped_current=True)  # noqa: SLF001
    receipt = write_control_receipt(
        action="deploy.rollback",
        actor=actor,
        repository=repo.full_name,
        summary=f"rollback {deploy_id} -> {prev_name}",
        before=before,
        after=after,
        result="rolled_back" if ok else "rollback_failed",
    )
    if not ok:
        raise HTTPException(
            status_code=500,
            detail=(
                f"rollback of {deploy_id!r} FAILED -- service may be down. "
                f"Receipt {receipt['receipt_id']}. Manual recovery required."
            ),
        )
    return {
        "status": "rolled_back",
        "deploy_id": deploy_id,
        "repo": repo.full_name,
        "restored_release": prev_name,
        "restored_sha": prev_sha,
        "receipt_id": receipt["receipt_id"],
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_deploy_control_router() -> Any:
    """Create the FastAPI router for the deploy control API (P0 #1)."""
    if not _HAS_FASTAPI:
        return None

    router = APIRouter(prefix="/deploys", tags=["deploy-control"])

    @router.post("", status_code=202)
    async def trigger_deploy(request: Request, body: TriggerRequest) -> dict[str, Any]:
        """Trigger a deploy for a repo (operator role, async 202)."""
        repo_name = (body.repo or "").strip()
        try:
            repo = load_repo_registry().get(repo_name)
        except UnknownRepoError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        sha = _resolve_ref_to_sha(repo, body.ref)
        actor = _control_actor(request)
        trigger_id = f"trig-{uuid.uuid4().hex[:12]}"
        payload = {
            "pr_sha": sha,
            "repository": repo.full_name,
            "deployer": f"portal:{actor}",
            "pr_title": f"Portal manual trigger ({(body.ref or 'origin/main').strip()})",
            "dry_run": bool(body.dry_run),
        }
        receipt = write_control_receipt(
            action="deploy.trigger",
            actor=actor,
            repository=repo.full_name,
            summary=f"trigger {repo.full_name} @ {sha[:12]}"
            + (" (dry run)" if body.dry_run else ""),
            before={"live_release": _live_release_name(repo)},
            after={
                "requested_sha": sha,
                "dry_run": bool(body.dry_run),
                "trigger_id": trigger_id,
            },
            result="dispatched",
        )
        thread = threading.Thread(
            target=_dispatch_trigger,
            args=(payload, repo, trigger_id),
            name=f"deploy-trigger-{trigger_id}",
            daemon=True,
        )
        thread.start()
        return {
            "status": "dispatched",
            "trigger_id": trigger_id,
            "receipt_id": receipt["receipt_id"],
            "repo": repo.full_name,
            "pr_sha": sha,
            "dry_run": bool(body.dry_run),
            "detail": (
                "deploy dispatched to the receiver; follow up with "
                f"GET /api/deploys?repo={repo.full_name} or watch deploy.* on /ws"
            ),
        }

    @router.post("/{deploy_id}/rollback")
    async def rollback_deploy(request: Request, deploy_id: str) -> dict[str, Any]:
        """Roll a deploy back to the previous release (operator role)."""
        return _rollback_deploy(deploy_id.strip(), _control_actor(request))

    @router.get("")
    async def deploy_history(
        repo: str | None = Query(default=None),
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        """Deploy history, sourced from alerts.log (+ manifest union)."""
        items = parse_deploy_history()
        if repo:
            items = [i for i in items if i["repo"] == repo.strip()]
        if status:
            want = status.strip().lower()
            if want == "succeeded":
                items = [i for i in items if i["type"] == "deploy_succeeded"]
            elif want == "failed":
                items = [
                    i
                    for i in items
                    if i["type"] in ("deploy_failed", "rollback_failed")
                ]
            elif want == "rolled_back":
                items = [i for i in items if i["type"] == "rollback_completed"]
            elif want == "dry_run":
                items = [i for i in items if i["type"] == "dry_run"]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown status filter {status!r} "
                    "(expected succeeded|failed|rolled_back|dry_run)",
                )
        return {"count": len(items[:limit]), "deploys": items[:limit]}

    @router.get("/releases")
    async def deploy_releases() -> dict[str, Any]:
        """Per-repo release state (read-only)."""
        return {"repos": list_repo_releases()}

    return router


deploy_control_router = create_deploy_control_router()
