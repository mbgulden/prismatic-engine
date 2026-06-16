"""OAuth2 Bearer token validation middleware for Prismatic API.

Loads API keys from ``PRISMATIC_API_KEYS`` (comma-separated) or
``PRISMATIC_API_KEY`` (single) environment variables.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security_scheme = HTTPBearer(auto_error=False)


def _load_api_keys() -> dict[str, list[str]]:
    """Load API keys from environment with optional scope suffixes.

    Format:
        PRISMATIC_API_KEY=sk-admin       → full access
        PRISMATIC_API_KEYS=sk-admin,sk-ro → multi-key (comma separated)

    Returns a dict mapping API key to scopes-list.
    """
    keys: dict[str, list[str]] = {}

    single = os.environ.get("PRISMATIC_API_KEY")
    if single:
        keys[single.strip()] = ["admin"]

    multi = os.environ.get("PRISMATIC_API_KEYS")
    if multi:
        for raw in multi.split(","):
            raw = raw.strip()
            if not raw:
                continue
            # Support key_prefix:scope format e.g. sk-admin:admin,sk-ro:readonly
            if ":" in raw:
                parts = raw.split(":", 1)
                keys[parts[0].strip()] = [s.strip() for s in parts[1].split(",")]
            else:
                # Existing key from PRISMATIC_API_KEY already registered
                if raw not in keys:
                    keys[raw] = ["admin"]

    # Fallback dev key if nothing configured
    if not keys:
        keys["prismatic-dev-token"] = ["admin"]

    return keys


VALID_KEYS: dict[str, list[str]] = _load_api_keys()


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict[str, Any]:
    """FastAPI dependency — returns user info dict or 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )
    token = credentials.credentials
    if token not in VALID_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"token_prefix": token[:8], "scopes": VALID_KEYS[token]}
