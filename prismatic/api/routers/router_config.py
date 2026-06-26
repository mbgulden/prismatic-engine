"""prismatic/api/routers/router_config.py — Admin API for router configuration.

GRO-555: CRUD endpoints that mutate the live RouterConfigStore without
requiring code changes or redeploys.

Endpoints
---------
- GET    /api/router/config           — full config snapshot
- PUT    /api/router/config/reset     — reset to defaults (admin scope)
- GET    /api/router/routes           — list routes
- GET    /api/router/routes/{label}   — get a single route
- PUT    /api/router/routes/{label}   — create or replace a route
- DELETE /api/router/routes/{label}   — delete a route (admin scope)
- GET    /api/router/models           — list models
- GET    /api/router/models/{name}    — get a single model
- PUT    /api/router/models/{name}    — create or replace a model
- DELETE /api/router/models/{name}    — delete a model (admin scope)
- POST   /api/router/system           — update system defaults (admin scope)
- GET    /api/router/health           — health probe (no auth required)

Auth
----
All write endpoints require ``admin`` scope. Read endpoints accept
either ``admin`` or ``readonly`` scope. Set ``PRISMATIC_API_KEYS`` to
provision keys, e.g. ``sk-admin:admin,sk-ro:readonly``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from prismatic.api.auth import verify_api_key
from prismatic.core.router_config import (
    ModelEntry,
    RouteEntry,
    RouterConfigStore,
    get_router_config_store,
)

logger = logging.getLogger("prismatic.api.routers.router_config")

router = APIRouter(prefix="/router", tags=["router-config"])


# ── Scope helpers ─────────────────────────────────────────────


def _require_admin(current_user: dict[str, Any] = Depends(verify_api_key)) -> dict[str, Any]:
    scopes = current_user.get("scopes", []) or []
    if "admin" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin scope required",
        )
    return current_user


def _store() -> RouterConfigStore:
    return get_router_config_store()


# ── Validation helpers ────────────────────────────────────────


_VALID_TIERS = {"premium", "fallback", "free"}


def _validate_priority_chain(payload: dict[str, Any], store: RouterConfigStore) -> list[str]:
    chain = payload.get("priority_chain")
    if chain is None:
        raise HTTPException(status_code=422, detail="priority_chain is required")
    if not isinstance(chain, list) or not all(isinstance(x, str) for x in chain):
        raise HTTPException(status_code=422, detail="priority_chain must be a list of strings")
    if len(chain) == 0:
        raise HTTPException(status_code=422, detail="priority_chain must not be empty")

    cfg = store.load()
    known = set(cfg.models.keys())
    unknown = [m for m in chain if m not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown models in chain: {unknown}. Known: {sorted(known)}",
        )
    return chain


def _validate_model_entry(payload: dict[str, Any]) -> ModelEntry:
    name = payload.get("canonical_name")
    flag = payload.get("agy_model_flag")
    tier = payload.get("tier", "premium")
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=422, detail="canonical_name is required")
    if not flag or not isinstance(flag, str):
        raise HTTPException(status_code=422, detail="agy_model_flag is required")
    if tier not in _VALID_TIERS:
        raise HTTPException(
            status_code=422,
            detail=f"tier must be one of {sorted(_VALID_TIERS)}, got {tier!r}",
        )
    return ModelEntry(
        canonical_name=name,
        agy_model_flag=flag,
        tier=tier,
        description=str(payload.get("description", "")),
        enabled=bool(payload.get("enabled", True)),
    )


# ── Health (unauthenticated) ──────────────────────────────────


@router.get("/health")
async def health() -> dict[str, Any]:
    """Health probe — returns version of the active config."""
    cfg = _store().load()
    return {
        "status": "ok",
        "version": cfg.version,
        "updated_at_iso": cfg.updated_at_iso,
        "route_count": len(cfg.routes),
        "model_count": len(cfg.models),
    }


# ── Config snapshot ───────────────────────────────────────────


@router.get("/config")
async def get_config(current_user: dict[str, Any] = Depends(verify_api_key)) -> dict[str, Any]:
    """Return the full router config snapshot."""
    return _store().load().to_dict()


@router.put("/config/reset")
async def reset_config(
    current_user: dict[str, Any] = Depends(_require_admin),
) -> dict[str, Any]:
    """Reset the entire router config to factory defaults."""
    cfg = _store().reset_to_defaults(updated_by=current_user.get("token_prefix", "admin"))
    return cfg.to_dict()


# ── Routes ─────────────────────────────────────────────────────


@router.get("/routes")
async def list_routes(
    current_user: dict[str, Any] = Depends(verify_api_key),
) -> dict[str, Any]:
    cfg = _store().load()
    return {
        "version": cfg.version,
        "routes": {label: r.to_dict() for label, r in cfg.routes.items()},
    }


@router.get("/routes/{label}")
async def get_route(
    label: str,
    current_user: dict[str, Any] = Depends(verify_api_key),
) -> dict[str, Any]:
    cfg = _store().load()
    route = cfg.routes.get(label)
    if route is None:
        raise HTTPException(status_code=404, detail=f"route {label!r} not found")
    return route.to_dict()


@router.put("/routes/{label}")
async def upsert_route(
    label: str,
    payload: dict[str, Any],
    current_user: dict[str, Any] = Depends(_require_admin),
) -> dict[str, Any]:
    store = _store()
    chain = _validate_priority_chain(payload, store)
    route = RouteEntry(
        agent_label=label,
        priority_chain=chain,
        weight=float(payload.get("weight", 1.0)),
        enabled=bool(payload.get("enabled", True)),
        cooldown_seconds=int(payload.get("cooldown_seconds", store.load().default_cooldown_seconds)),
    )
    cfg = store.upsert_route(label, route, updated_by=current_user.get("token_prefix", "admin"))
    return cfg.routes[label].to_dict()


@router.delete("/routes/{label}", status_code=204)
async def delete_route(
    label: str,
    current_user: dict[str, Any] = Depends(_require_admin),
) -> None:
    store = _store()
    cfg = store.delete_route(label, updated_by=current_user.get("token_prefix", "admin"))
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"route {label!r} not found")


# ── Models ─────────────────────────────────────────────────────


@router.get("/models")
async def list_models(
    current_user: dict[str, Any] = Depends(verify_api_key),
) -> dict[str, Any]:
    cfg = _store().load()
    return {
        "version": cfg.version,
        "models": {name: m.to_dict() for name, m in cfg.models.items()},
    }


@router.get("/models/{name}")
async def get_model(
    name: str,
    current_user: dict[str, Any] = Depends(verify_api_key),
) -> dict[str, Any]:
    cfg = _store().load()
    model = cfg.models.get(name)
    if model is None:
        raise HTTPException(status_code=404, detail=f"model {name!r} not found")
    return model.to_dict()


@router.put("/models/{name}")
async def upsert_model(
    name: str,
    payload: dict[str, Any],
    current_user: dict[str, Any] = Depends(_require_admin),
) -> dict[str, Any]:
    payload = dict(payload)
    payload.setdefault("canonical_name", name)
    model = _validate_model_entry(payload)
    cfg = _store().upsert_model(name, model, updated_by=current_user.get("token_prefix", "admin"))
    return cfg.models[name].to_dict()


@router.delete("/models/{name}", status_code=204)
async def delete_model(
    name: str,
    current_user: dict[str, Any] = Depends(_require_admin),
) -> None:
    try:
        cfg = _store().delete_model(name, updated_by=current_user.get("token_prefix", "admin"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"model {name!r} not found")


# ── System defaults ────────────────────────────────────────────


@router.post("/system")
async def update_system(
    payload: dict[str, Any],
    current_user: dict[str, Any] = Depends(_require_admin),
) -> dict[str, Any]:
    """Update system-level defaults (cooldown, quota keywords)."""
    store = _store()
    cfg = store.load()
    if "default_cooldown_seconds" in payload:
        try:
            cfg.default_cooldown_seconds = int(payload["default_cooldown_seconds"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "quota_keywords" in payload:
        kw = payload["quota_keywords"]
        if not isinstance(kw, list) or not all(isinstance(k, str) for k in kw):
            raise HTTPException(status_code=422, detail="quota_keywords must be a list of strings")
        cfg.quota_keywords = list(kw)
    cfg = store.save(cfg, updated_by=current_user.get("token_prefix", "admin"))
    return {
        "default_cooldown_seconds": cfg.default_cooldown_seconds,
        "quota_keywords": cfg.quota_keywords,
    }
