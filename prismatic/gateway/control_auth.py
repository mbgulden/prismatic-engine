"""Fail-closed authorization for gateway control-plane mutations.

This module deliberately performs no configuration reads at import time.  The
credential file is opened and validated for every protected request so changes
(including replacement, permission changes, and removal) take effect without a
process restart.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from fastapi import Request, Response
from fastapi.responses import JSONResponse

_READ_ONLY_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
_ROLES: Final = frozenset({"operator", "approver", "executor"})
_CREDENTIAL_FILE_ENV: Final = "PRISMATIC_CONTROL_AUTH_FILE"
_MAX_CREDENTIAL_FILE_BYTES: Final = 1024 * 1024


@dataclass(frozen=True)
class Credential:
    """One configured actor and its explicitly granted roles."""

    actor: str
    token_sha256: str
    roles: frozenset[str]


class CredentialConfigurationError(Exception):
    """An intentionally detail-free invalid-configuration signal."""


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CredentialConfigurationError
        result[key] = value
    return result


def _load_credentials() -> tuple[Credential, ...]:
    """Read and strictly validate the external credential file.

    No error from this function should be exposed to a client: path and file
    details can themselves be sensitive operational information.
    """

    raw_path = os.environ.get(_CREDENTIAL_FILE_ENV)
    if not raw_path or not Path(raw_path).is_absolute():
        raise CredentialConfigurationError

    path = Path(raw_path)
    try:
        # Reject a link in any path component, not only a linked leaf.  lstat
        # also rejects FIFOs/devices before open, avoiding a blocking open.
        if any(component.is_symlink() for component in (path, *path.parents)):
            raise CredentialConfigurationError
        path_metadata = os.lstat(path)
        if not stat.S_ISREG(path_metadata.st_mode):
            raise CredentialConfigurationError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, CredentialConfigurationError) as exc:
        raise CredentialConfigurationError from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or (os.name != "nt" and metadata.st_mode & 0o077):
            raise CredentialConfigurationError
        if metadata.st_size > _MAX_CREDENTIAL_FILE_BYTES:
            raise CredentialConfigurationError
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            document = json.load(
                stream, object_pairs_hook=_object_without_duplicate_keys
            )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        CredentialConfigurationError,
    ) as exc:
        raise CredentialConfigurationError from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if not isinstance(document, dict) or set(document) != {"version", "credentials"}:
        raise CredentialConfigurationError
    if (
        type(document["version"]) is not int
        or document["version"] != 1
        or not isinstance(document["credentials"], list)
    ):
        raise CredentialConfigurationError
    if not document["credentials"]:
        raise CredentialConfigurationError

    credentials: list[Credential] = []
    digests: set[str] = set()
    for item in document["credentials"]:
        if not isinstance(item, dict) or set(item) != {
            "actor",
            "token_sha256",
            "roles",
        }:
            raise CredentialConfigurationError
        actor = item["actor"]
        digest = item["token_sha256"]
        roles = item["roles"]
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 200:
            raise CredentialConfigurationError
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or digest in digests
        ):
            raise CredentialConfigurationError
        if (
            not isinstance(roles, list)
            or not roles
            or any(not isinstance(role, str) for role in roles)
            or len(set(roles)) != len(roles)
            or not set(roles) <= _ROLES
        ):
            raise CredentialConfigurationError
        digests.add(digest)
        credentials.append(
            Credential(actor=actor.strip(), token_sha256=digest, roles=frozenset(roles))
        )
    return tuple(credentials)


def _bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization")
    if not value:
        return None
    parts = value.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None
    return parts[1]


def _authenticate(
    request: Request, credentials: tuple[Credential, ...]
) -> Credential | None:
    token = _bearer_token(request)
    if token is None:
        return None
    presented_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

    # Check every record rather than returning on the first match.  This both
    # uses constant-time comparison and avoids revealing record order by time.
    match: Credential | None = None
    for credential in credentials:
        if hmac.compare_digest(presented_digest, credential.token_sha256):
            match = credential
    return match


def _is_webhook_boundary(path: str) -> bool:
    return path.startswith("/webhooks/")


def _approval_route(path: str) -> bool:
    segments = set(PurePosixPath(path).parts)
    return bool(
        segments
        & {
            "approve",
            "reject",
            "approval",
            "approvals",
            "request-approval",
            "operator-action",
            "pr-approval",
            "pr-create-approved",
            "final-authorization",
            "final-action-authorizations",
            "promotion-decision",
        }
    )


def _executor_route(path: str) -> bool:
    segments = PurePosixPath(path).parts
    return any(
        segment
        in {
            "real-executor",
            "real-executor-arming",
            "pr-executor",
            "canary-dry-run",
            "invoke",
        }
        or segment.startswith("real-executor-")
        for segment in segments
    )


async def required_role(request: Request) -> str | None:
    """Classify a request, parsing native-cron JSON without consuming it."""

    path = request.url.path
    # Task-admission rows expose producer/worktree control-plane coordinates.
    # Protect readback as operator data before the broad read-only exemption.
    if path == "/api/dashboard/task-admissions" or path.startswith(
        "/api/dashboard/task-admissions/"
    ):
        return "operator"

    if (
        request.method.upper() in _READ_ONLY_METHODS
        or _is_webhook_boundary(path)
        or path.startswith("/api/skills")
        or path.startswith("/api/gateway/skills")
        or path.startswith("/api/signals")
        or path.startswith("/api/gateway/signals")
        or path.startswith("/api/swarmlock")
        or path.startswith("/api/gateway/swarmlock")
        or path.startswith("/api/hypervisor")
        or path.startswith("/api/gateway/hypervisor")
        or path.startswith("/api/dag")
        or path.startswith("/api/gateway/dag")
        or path.startswith("/api/decisions")
        or path.startswith("/api/gateway/decisions")
        or path.startswith("/api/agents")
        or path.startswith("/api/gateway/agents")
        or path.startswith("/api/credentials")
        or path.startswith("/api/gateway/credentials")
        or path.startswith("/api/oauth")
        or path.startswith("/api/gateway/oauth")
        or path.startswith("/api/services")
        or path.startswith("/api/gateway/services")
        or path.startswith("/api/workspace-tree/")
        or path.startswith("/api/mesh")
        or path.startswith("/api/swarm")
        or path.startswith("/api/gateway/swarm")
        or path.startswith("/api/gateway/studio")
        or path.startswith("/api/deliverables")
        or path.startswith("/api/gateway/deliverables")
        or path.startswith("/api/jobs")
        or path.startswith("/api/gateway/jobs")
        or path.startswith("/api/workers")
        or path.startswith("/api/gateway/workers")
    ):
        return None


    if path.startswith("/native-crons/") and path.endswith("/action"):
        try:
            payload = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("action") == "run":
            return "executor"

    if _executor_route(path):
        return "executor"
    if _approval_route(path):
        return "approver"
    return "operator"


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"detail": "control authorization required"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def control_authorization_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Authorize every non-read, non-webhook HTTP request before routing."""

    role = await required_role(request)
    if role is None:
        return await call_next(request)

    try:
        credentials = _load_credentials()
    except Exception:
        # Configuration/read failures are deliberately indistinguishable from
        # absent credentials and must never turn the boundary into a 500.
        return _unauthorized()

    credential = _authenticate(request, credentials)
    if credential is None:
        return _unauthorized()
    if role not in credential.roles:
        return JSONResponse({"detail": "insufficient control role"}, status_code=403)

    request.state.control_actor = credential.actor
    request.state.control_roles = credential.roles
    request.state.control_authorization_class = role
    response = await call_next(request)
    response.headers["X-Prismatic-Control-Authorization"] = "authorized"
    response.headers["X-Prismatic-Control-Role"] = role
    return response
