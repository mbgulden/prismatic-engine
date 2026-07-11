"""Bearer token validation middleware for the Prismatic public API.

Loads API keys from ``PRISMATIC_API_KEYS`` (comma-separated) or
``PRISMATIC_API_KEY`` (single) environment variables at request time so keys
loaded by the server CLI before startup are honored.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security_scheme = HTTPBearer(auto_error=False)


def _split_scopes(raw_scopes: str) -> list[str]:
    """Split optional scope suffixes without colliding with key commas.

    ``PRISMATIC_API_KEYS`` uses commas between key entries. If an entry has a
    scope suffix, split that suffix on ``+`` or ``|``. Examples:
    ``sk-admin:admin`` or ``sk-review:read+jobs``.
    """
    scopes = [part.strip() for part in raw_scopes.replace("|", "+").split("+")]
    return [scope for scope in scopes if scope] or ["admin"]


def _load_api_keys() -> dict[str, list[str]]:
    """Load API keys from environment with optional scope suffixes.

    Format:
        PRISMATIC_API_KEY=sk-admin            → full access
        PRISMATIC_API_KEYS=sk-admin,sk-ro:read → multi-key
        PRISMATIC_API_ALLOW_DEV_TOKEN=1       → opt-in local dev fallback

    Returns a dict mapping API key to scopes-list. Empty means fail closed.
    """
    keys: dict[str, list[str]] = {}

    single = os.environ.get("PRISMATIC_API_KEY")
    if single and single.strip():
        keys[single.strip()] = ["admin"]

    multi = os.environ.get("PRISMATIC_API_KEYS")
    if multi:
        for raw in multi.split(","):
            raw = raw.strip()
            if not raw:
                continue
            if ":" in raw:
                token, scope_part = raw.split(":", 1)
                token = token.strip()
                if token:
                    keys[token] = _split_scopes(scope_part)
            elif raw not in keys:
                keys[raw] = ["admin"]

    if not keys and os.environ.get("PRISMATIC_API_ALLOW_DEV_TOKEN") == "1":
        keys["prismatic-dev-token"] = ["admin"]

    return keys


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict[str, Any]:
    """FastAPI dependency — returns user info dict or 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    valid_keys = _load_api_keys()
    if not valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No API keys configured",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    if token not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"token_prefix": token[:8], "scopes": valid_keys[token]}
