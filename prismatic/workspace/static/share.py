"""Signed Share Link Generator for the Curated Workspace Plugin (WA-8).

Corresponds to §6.3 and R6 of okf-docs-workspace-deploy-v1.md.
Generates HMAC-SHA256 signed share-link tokens with 24-hour default TTL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any, Optional

DEFAULT_SECRET = os.environ.get(
    "PRISMATIC_WORKSPACE_SHARE_SECRET", "prismatic-workspace-share-secret-v1"
)
DEFAULT_TTL_SECONDS = 86400  # 24 hours

_REVOKED_TOKENS: set[str] = set()


class WorkspaceShareManager:
    """Generates and validates signed shareable links for workspace docs."""

    def __init__(self, secret: str = DEFAULT_SECRET):
        self.secret = secret.encode("utf-8")

    def generate_token(
        self,
        doc_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Generate a signed share token for a doc_id."""
        expires_at = int(time.time()) + ttl_seconds
        payload = f"{doc_id}:{expires_at}"
        sig = hmac.new(self.secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        raw_token = f"{payload}:{sig}"
        token = base64.urlsafe_b64encode(raw_token.encode("utf-8")).decode("utf-8")

        return {
            "token": token,
            "doc_id": doc_id,
            "expires_at": expires_at,
            "ttl_seconds": ttl_seconds,
            "share_url": f"/api/workspace/share/{token}",
        }

    def validate_token(self, token: str) -> tuple[bool, str, Optional[str]]:
        """Validate a share token.

        Returns (is_valid, doc_id, error_message).
        """
        if token in _REVOKED_TOKENS:
            return False, "", "Token revoked"

        try:
            raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
            parts = raw.split(":")
            if len(parts) != 3:
                return False, "", "Malformed token"

            doc_id, expires_at_str, sig = parts[0], parts[1], parts[2]
            expires_at = int(expires_at_str)

            # Check expiration
            if time.time() > expires_at:
                return False, doc_id, "Token expired"

            # Check HMAC signature
            payload = f"{doc_id}:{expires_at}"
            expected_sig = hmac.new(
                self.secret, payload.encode("utf-8"), hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(sig, expected_sig):
                return False, doc_id, "Invalid signature"

            return True, doc_id, None
        except Exception as exc:
            return False, "", f"Invalid token format: {exc}"

    @classmethod
    def revoke_token(cls, token: str) -> None:
        """Revoke a token."""
        _REVOKED_TOKENS.add(token)
