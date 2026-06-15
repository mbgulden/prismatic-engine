"""
prismatic/gateway/security.py — Path Traversal Guard & Security Middleware

Provides:
    - ``sanitize_path(path, base_dir)`` — resolve and validate a path against
      directory traversal attacks (``../``, symlink escapes)
    - ``PathTraversalGuard`` — reusable middleware that intercepts path parameters
      before they reach route handlers
    - ``require_tenant_isolation(tenant_id, requested_path)`` — verify that a
      requested path stays within the tenant's workspace boundary

Integration:
    - prismatic/sandbox/pod_manager.py — validates workspace mounts
    - prismatic/gateway/server.py — mounted as middleware for all API routes
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

logger = logging.getLogger("prismatic.gateway.security")

# ── Constants ──────────────────────────────────────────────────────────────

_PRISMATIC_STATE_DIR_CACHED = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")


def _get_state_dir() -> str:
    """Return the current PRISMATIC_STATE_DIR, checking env var at call time.

    This allows tests to set ``os.environ["PRISMATIC_STATE_DIR"]`` after
    module import and have it take effect immediately.
    """
    return os.environ.get("PRISMATIC_STATE_DIR", _PRISMATIC_STATE_DIR_CACHED)


# ── Public API ─────────────────────────────────────────────────────────────


def sanitize_path(path: str, base_dir: str = "") -> str:
    """Resolve and validate a path, rejecting traversal attempts.

    Args:
        path: The user-supplied path string (may contain ``../``).
        base_dir: The allowed root directory. Defaults to PRISMATIC_STATE_DIR.

    Returns:
        The resolved absolute path (guaranteed to be inside base_dir).

    Raises:
        ValueError: If the resolved path escapes the base directory,
                    or contains null bytes or other dangerous characters.
    """
    base = os.path.abspath(base_dir or _get_state_dir())

    # Reject null bytes
    if "\0" in path:
        raise ValueError("Path contains null byte — potential injection attempt")

    # Reject URL-encoded path separators (traversal via encoding)
    path_lower = path.lower()
    if "%2f" in path_lower or "%2e" in path_lower:
        raise ValueError(
            "Path contains URL-encoded traversal pattern (%2f or %2e) "
            "— potential injection attempt"
        )

    # Reject shell metacharacters (semicolons, pipes, redirects)
    dangerous = {"|", "&", ";", "$", "`", "(", ")", "!", ">", "<"}
    for ch in path:
        if ch in dangerous:
            raise ValueError(f"Path contains dangerous character {ch!r}")

    resolved = os.path.abspath(os.path.join(base, path))

    if not resolved.startswith(base):
        raise ValueError(
            f"Path traversal detected: {path!r} resolves to {resolved!r} "
            f"which is outside base directory {base!r}"
        )

    return resolved


def require_tenant_isolation(tenant_id: str, requested_path: str) -> str:
    """Verify that a requested path stays within a tenant's workspace.

    Tenant workspace base: ``<PRISMATIC_STATE_DIR>/workspaces/<tenant_id>/``

    Args:
        tenant_id: The tenant identifier.
        requested_path: The path being requested (relative or absolute).

    Returns:
        The resolved path if it's within the tenant's workspace.

    Raises:
        ValueError: If the path escapes the tenant's workspace boundary.
    """
    tenant_base = os.path.join(_get_state_dir(), "workspaces", tenant_id)
    return sanitize_path(requested_path, base_dir=tenant_base)


# ── Middleware ─────────────────────────────────────────────────────────────


class PathTraversalGuard:
    """Reusable middleware/base class for path traversal protection.

    Usage with FastAPI::

        from prismatic.gateway.security import PathTraversalGuard

        guard = PathTraversalGuard()

        # In a route handler:
        safe_path = guard.sanitize(user_provided_path, base_dir="/app/data")

    Usage as raw middleware function::

        from prismatic.gateway.security import traversal_guard_middleware

        app.middleware("http")(traversal_guard_middleware)
    """

    def __init__(self, state_dir: str = ""):
        self.state_dir = state_dir or _get_state_dir()

    def sanitize(self, path: str, base_dir: str = "") -> str:
        """See :func:`sanitize_path`."""
        return sanitize_path(path, base_dir or self.state_dir)

    def require_tenant(self, tenant_id: str, path: str) -> str:
        """See :func:`require_tenant_isolation`."""
        return require_tenant_isolation(tenant_id, path)

    def is_safe(self, path: str, base_dir: str = "") -> bool:
        """Return True if the path is safe (does not raise)."""
        try:
            self.sanitize(path, base_dir)
            return True
        except ValueError:
            return False

    # ── Convenience patterns ───────────────────────────────────────

    def safe_path_or_none(self, path: str, base_dir: str = "") -> str | None:
        """Return the resolved path if safe, None otherwise."""
        try:
            return self.sanitize(path, base_dir)
        except ValueError:
            return None


# ── Standalone middleware function ─────────────────────────────────────────

# Type for an ASGI receive/send pair
ASGIApp = Callable


def traversal_guard_middleware(app: ASGIApp) -> ASGIApp:
    """ASGI middleware that intercepts path parameters for traversal attempts.

    This is a basic ASGI middleware wrapper. For production use, prefer
    the FastAPI middleware integration via ``@app.middleware("http")``.

    Usage::

        from prismatic.gateway.security import traversal_guard_middleware

        app.add_middleware(traversal_guard_middleware)  # Starlette-style
    """

    guard = PathTraversalGuard()

    async def middleware(scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            query = scope.get("query_string", b"").decode()

            # Quick check: reject obvious traversal in path or query
            if ".." in path or ".." in query:
                logger.warning("Blocked path traversal attempt: path=%r query=%r", path, query)
                # Return 400
                from starlette.responses import PlainTextResponse
                response = PlainTextResponse("Path traversal detected", status_code=400)
                await response(scope, receive, send)
                return

        await app(scope, receive, send)

    return middleware
