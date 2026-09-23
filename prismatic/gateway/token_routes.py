"""Portal API token endpoints (Portal Phase 1, P0 #5).

``GET /api/tokens``      list token metadata (never secrets)
``POST /api/tokens``     mint a token; the raw secret is returned exactly once
``DELETE /api/tokens/{id}``  revoke a token (metadata retained)

All three require the ``admin`` portal role.  The control-auth middleware
classifies ``/api/tokens*`` as admin-only before routing; these handlers
re-check ``request.state.portal_roles`` so a misconfigured middleware can
never silently open them (fail closed).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from prismatic.gateway import instance_config as instance_config_module
from prismatic.gateway.token_store import TOKEN_ROLES, TokenStore

router = APIRouter()


class MintTokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


def _forbidden() -> JSONResponse:
    return JSONResponse({"detail": "insufficient portal role"}, status_code=403)


def _store() -> TokenStore:
    state = instance_config_module.state_dir()
    config = instance_config_module.ensure_instance_id(state)
    return TokenStore.for_state_dir(state, config.instance_id)


def _require_admin(request: Request) -> JSONResponse | None:
    portal_roles = getattr(request.state, "portal_roles", frozenset())
    if "admin" not in portal_roles:
        return _forbidden()
    return None


@router.get("/api/tokens")
async def list_tokens(request: Request) -> Any:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    return {"tokens": [record.public() for record in _store().list()]}


@router.post("/api/tokens", status_code=201)
async def mint_token(request: Request, body: MintTokenBody) -> Any:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    role = body.role.strip().lower()
    if role not in TOKEN_ROLES:
        return JSONResponse(
            {"detail": f"role must be one of {sorted(TOKEN_ROLES)}"},
            status_code=422,
        )
    try:
        record, secret = _store().mint(
            name=body.name, role=role, expires_in_days=body.expires_in_days
        )
    except Exception:
        # TokenStoreError messages name files/paths: keep them server-side.
        return JSONResponse({"detail": "token mint failed"}, status_code=500)
    payload = record.public()
    # The raw secret is returned exactly once, at creation.  It is never
    # stored, never logged, and never returned again.
    payload["token"] = secret
    return JSONResponse(payload, status_code=201)


@router.delete("/api/tokens/{token_id}")
async def revoke_token(request: Request, token_id: str) -> Any:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    record = _store().revoke(token_id)
    if record is None:
        return JSONResponse({"detail": "unknown token"}, status_code=404)
    return {"token": record.public(), "revoked": True}
