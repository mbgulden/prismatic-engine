"""Path traversal guard and tenant workspace isolation helpers."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

logger = logging.getLogger("prismatic.gateway.security")

_STATE_DIR_DEFAULT = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")


def _state_dir() -> str:
    """Return the active state directory at call time for test/runtime overrides."""
    return os.environ.get("PRISMATIC_STATE_DIR", _STATE_DIR_DEFAULT)


def _resolve_base(base_dir: str = "") -> str:
    return os.path.abspath(base_dir or _state_dir())


def sanitize_path(path: str, base_dir: str = "") -> str:
    """Resolve ``path`` and ensure it remains inside ``base_dir``.

    The check rejects null bytes, URL-encoded traversal/separators, shell-ish
    metacharacters, symlink escapes, and sibling-prefix bypasses such as
    ``/tmp/base`` vs ``/tmp/base_evil``.
    """
    if "\0" in path:
        raise ValueError("Path contains null byte — potential injection attempt")

    decoded = unquote(path)
    if decoded != path:
        lowered = decoded.lower()
        if ".." in lowered or "/" in decoded or "\\" in decoded:
            raise ValueError("Path contains URL-encoded traversal pattern")

    dangerous = {"|", "&", ";", "$", "`", "(", ")", "!", ">", "<"}
    for ch in path:
        if ch in dangerous:
            raise ValueError(f"Path contains dangerous character {ch!r}")

    base = _resolve_base(base_dir)
    candidate = os.path.abspath(os.path.join(base, path))
    base_real = os.path.realpath(base)
    candidate_real = os.path.realpath(candidate)

    if os.path.commonpath([base_real, candidate_real]) != base_real:
        raise ValueError(
            f"Path traversal detected: {path!r} resolves to {candidate_real!r} "
            f"which is outside base directory {base_real!r}"
        )
    return candidate_real


def require_tenant_isolation(tenant_id: str, requested_path: str) -> str:
    """Validate that ``requested_path`` stays in a tenant workspace."""
    tenant_base = os.path.join(_state_dir(), "workspaces", tenant_id)
    return sanitize_path(requested_path, tenant_base)


class PathTraversalGuard:
    """Reusable path traversal protection helper."""

    def __init__(self, state_dir: str = "") -> None:
        self.state_dir = state_dir or _state_dir()

    def sanitize(self, path: str, base_dir: str = "") -> str:
        return sanitize_path(path, base_dir or self.state_dir)

    def require_tenant(self, tenant_id: str, path: str) -> str:
        return require_tenant_isolation(tenant_id, path)

    def is_safe(self, path: str, base_dir: str = "") -> bool:
        try:
            self.sanitize(path, base_dir)
            return True
        except ValueError:
            return False

    def safe_path_or_none(self, path: str, base_dir: str = "") -> str | None:
        try:
            return self.sanitize(path, base_dir)
        except ValueError:
            return None


ASGIApp = Callable[..., Any]


def traversal_guard_middleware(app: ASGIApp) -> ASGIApp:
    """Minimal ASGI middleware that rejects obvious traversal in path/query."""

    async def middleware(scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            query = scope.get("query_string", b"").decode()
            if ".." in path or ".." in query or "%2e" in path.lower() or "%2e" in query.lower():
                logger.warning("Blocked path traversal attempt: path=%r query=%r", path, query)
                from starlette.responses import PlainTextResponse

                response = PlainTextResponse("Path traversal detected", status_code=400)
                await response(scope, receive, send)
                return
        await app(scope, receive, send)

    return middleware
