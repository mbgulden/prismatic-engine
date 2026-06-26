"""Tests for the GRO-555 RouterConfigStore + REST API + admin UI."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import prismatic.core.router_config as rc
from prismatic.core.router_config import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_MODELS,
    ModelEntry,
    RouteEntry,
    RouterConfig,
    RouterConfigStore,
    get_router_config_store,
    reset_router_config_store,
)


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def tmp_config_path(tmp_path: Path) -> Path:
    return tmp_path / "router_config.json"


@pytest.fixture
def store(tmp_config_path: Path) -> RouterConfigStore:
    """Fresh, isolated store backed by a temp file."""
    return RouterConfigStore(config_path=tmp_config_path)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Make sure each test starts with no module-level singleton."""
    reset_router_config_store()
    yield
    reset_router_config_store()


# ── Data-class tests ──────────────────────────────────────────


class TestModelEntry:
    def test_roundtrip(self):
        m = ModelEntry(
            canonical_name="claude-opus",
            agy_model_flag="Claude Opus 4.6 (Thinking)",
            tier="premium",
            description="d",
            enabled=True,
        )
        d = m.to_dict()
        m2 = ModelEntry.from_dict("claude-opus", d)
        assert m == m2

    def test_defaults_in_from_dict(self):
        m = ModelEntry.from_dict("x", {})
        assert m.canonical_name == "x"
        assert m.tier == "premium"
        assert m.enabled is True


class TestRouteEntry:
    def test_roundtrip(self):
        r = RouteEntry(
            agent_label="agy",
            priority_chain=["claude-opus", "gpt-oss-120b"],
            weight=2.5,
            enabled=True,
            cooldown_seconds=120,
        )
        r2 = RouteEntry.from_dict("agy", r.to_dict())
        assert r == r2

    def test_defaults_in_from_dict(self):
        r = RouteEntry.from_dict("x", {})
        assert r.agent_label == "x"
        assert r.priority_chain == []
        assert r.weight == 1.0
        assert r.enabled is True
        assert r.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS


class TestRouterConfig:
    def test_defaults_match_legacy_chain(self):
        cfg = RouterConfig.defaults()
        # The original MODEL_PRIORITY_CHAIN is preserved
        assert "agy" in cfg.routes
        assert cfg.routes["agy"].priority_chain == [
            "claude-opus",
            "claude-sonnet",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
            "gpt-oss-120b",
        ]
        for name in ("claude-opus", "claude-sonnet", "gemini-3.5-flash",
                     "gemini-3.1-flash-lite", "gpt-oss-120b"):
            assert name in cfg.models
            assert cfg.models[name].agy_model_flag == DEFAULT_MODELS[name]["agy_model_flag"]

    def test_to_from_roundtrip(self):
        cfg = RouterConfig.defaults()
        cfg.routes["new-agent"] = RouteEntry(
            agent_label="new-agent",
            priority_chain=["claude-opus", "gpt-oss-120b"],
            weight=0.5,
        )
        cfg.models["claude-opus"].description = "updated"
        data = cfg.to_dict()
        cfg2 = RouterConfig.from_dict(data)
        assert "new-agent" in cfg2.routes
        assert cfg2.routes["new-agent"].priority_chain == ["claude-opus", "gpt-oss-120b"]
        assert cfg2.models["claude-opus"].description == "updated"

    def test_priority_chain_filters_disabled(self):
        cfg = RouterConfig.defaults()
        cfg.models["claude-opus"].enabled = False
        chain = cfg.priority_chain("agy")
        assert "claude-opus" not in chain
        assert chain[0] == "claude-sonnet"

    def test_priority_chain_unknown_label_falls_back(self):
        cfg = RouterConfig.defaults()
        chain = cfg.priority_chain("unknown-agent")
        assert chain == cfg.priority_chain("agy")

    def test_model_flag_lookup(self):
        cfg = RouterConfig.defaults()
        assert cfg.model_flag("claude-opus") == "Claude Opus 4.6 (Thinking)"
        assert cfg.model_flag("nope") is None


# ── Store tests ───────────────────────────────────────────────


class TestRouterConfigStore:
    def test_load_when_file_missing_returns_defaults(self, store, tmp_config_path):
        assert not tmp_config_path.exists()
        cfg = store.load()
        assert "agy" in cfg.routes
        assert cfg.version == 1
        assert cfg.updated_by == "defaults"

    def test_save_writes_atomic_json(self, store, tmp_config_path):
        cfg = store.load()
        cfg.routes["agy"].weight = 9.9
        store.save(cfg, updated_by="test")
        raw = json.loads(tmp_config_path.read_text())
        assert raw["routes"]["agy"]["weight"] == 9.9
        assert raw["updated_by"] == "test"

    def test_cache_is_invalidated_after_save(self, store):
        c1 = store.load()
        c1.routes["x"] = RouteEntry("x", ["claude-opus"])
        store.save(c1, updated_by="t")
        c2 = store.load(force_reload=True)
        assert "x" in c2.routes

    def test_corrupt_file_falls_back_to_defaults(self, store, tmp_config_path):
        tmp_config_path.write_text("not json {")
        cfg = store.load()
        # Should not raise — returns defaults
        assert "agy" in cfg.routes

    def test_upsert_route(self, store):
        store.upsert_route(
            "fred",
            RouteEntry("fred", ["claude-opus", "gpt-oss-120b"], weight=0.7),
            updated_by="u",
        )
        cfg = store.load(force_reload=True)
        assert "fred" in cfg.routes
        assert cfg.routes["fred"].weight == 0.7

    def test_delete_route(self, store):
        store.upsert_route("tmp", RouteEntry("tmp", ["claude-opus"]))
        out = store.delete_route("tmp", updated_by="u")
        assert out is not None
        assert "tmp" not in store.load(force_reload=True).routes

    def test_delete_route_missing_returns_none(self, store):
        assert store.delete_route("nope") is None

    def test_upsert_model(self, store):
        store.upsert_model(
            "my-model",
            ModelEntry("my-model", "My Model Flag", tier="free"),
            updated_by="u",
        )
        cfg = store.load(force_reload=True)
        assert cfg.models["my-model"].agy_model_flag == "My Model Flag"

    def test_delete_model_in_use_raises(self, store):
        # agy route references claude-opus
        with pytest.raises(ValueError, match="referenced by route"):
            store.delete_model("claude-opus")

    def test_delete_model_unused_succeeds(self, store):
        # Add a fresh model that nothing references, then delete it
        store.upsert_model(
            "throwaway",
            ModelEntry("throwaway", "X", tier="free"),
            updated_by="u",
        )
        out = store.delete_model("throwaway", updated_by="u")
        assert out is not None
        assert "throwaway" not in store.load(force_reload=True).models

    def test_delete_model_missing_returns_none(self, store):
        assert store.delete_model("nope") is None

    def test_reset_to_defaults(self, store):
        # Mess with the config
        store.upsert_route("zzz", RouteEntry("zzz", ["claude-opus"]))
        cfg = store.reset_to_defaults(updated_by="reset")
        assert "zzz" not in cfg.routes
        assert cfg.updated_by == "reset"


# ── Singleton tests ───────────────────────────────────────────


class TestSingleton:
    def test_get_returns_same_instance(self, tmp_config_path, monkeypatch):
        monkeypatch.setattr(rc, "DEFAULT_STATE_DIR", str(tmp_config_path))
        a = get_router_config_store()
        b = get_router_config_store()
        assert a is b

    def test_reset_drops_singleton(self, tmp_config_path, monkeypatch):
        monkeypatch.setattr(rc, "DEFAULT_STATE_DIR", str(tmp_config_path))
        a = get_router_config_store()
        reset_router_config_store()
        b = get_router_config_store()
        assert a is not b


# ── API tests ─────────────────────────────────────────────────


class TestRouterConfigAPI:
    @pytest.fixture
    def env_setup(self, monkeypatch, tmp_path):
        # Point the store at a temp dir
        monkeypatch.setattr(rc, "DEFAULT_STATE_DIR", str(tmp_path))
        # Provision two keys: one admin, one read-only
        monkeypatch.setenv("PRISMATIC_API_KEYS", "sk-admin:admin,sk-ro:readonly")
        # Reload auth
        from prismatic.api import auth as auth_mod
        auth_mod.VALID_KEYS = auth_mod._load_api_keys()
        # Reset singleton so the store uses the new STATE_DIR
        reset_router_config_store()

    @pytest.fixture
    def client(self, env_setup):
        from prismatic.api.server import app
        return TestClient(app)

    def _auth(self, token: str = "sk-admin"):
        return {"Authorization": f"Bearer {token}"}

    def test_health_unauthenticated(self, client):
        r = client.get("/api/v1/router/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert body["route_count"] >= 1
        assert body["model_count"] >= 5

    def test_config_requires_auth(self, client):
        r = client.get("/api/v1/router/config")
        assert r.status_code == 401

    def test_readonly_can_read(self, client):
        r = client.get("/api/v1/router/config", headers=self._auth("sk-ro"))
        assert r.status_code == 200
        assert "routes" in r.json()

    def test_readonly_cannot_write(self, client):
        r = client.put(
            "/api/v1/router/routes/fred",
            headers=self._auth("sk-ro"),
            json={"priority_chain": ["claude-opus"]},
        )
        assert r.status_code == 403

    def test_admin_can_upsert_route(self, client):
        r = client.put(
            "/api/v1/router/routes/fred",
            headers=self._auth("sk-admin"),
            json={
                "priority_chain": ["claude-opus", "gemini-3.5-flash"],
                "weight": 2.0,
                "cooldown_seconds": 60,
                "enabled": True,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["priority_chain"] == ["claude-opus", "gemini-3.5-flash"]
        assert body["weight"] == 2.0
        assert body["cooldown_seconds"] == 60

    def test_upsert_route_validates_chain(self, client):
        r = client.put(
            "/api/v1/router/routes/fred",
            headers=self._auth("sk-admin"),
            json={"priority_chain": ["not-a-real-model"]},
        )
        assert r.status_code == 422
        assert "unknown models" in r.json()["detail"]

    def test_upsert_route_validates_empty_chain(self, client):
        r = client.put(
            "/api/v1/router/routes/fred",
            headers=self._auth("sk-admin"),
            json={"priority_chain": []},
        )
        assert r.status_code == 422

    def test_delete_route_admin(self, client):
        # Create then delete
        client.put(
            "/api/v1/router/routes/temp",
            headers=self._auth("sk-admin"),
            json={"priority_chain": ["claude-opus"]},
        )
        r = client.delete("/api/v1/router/routes/temp", headers=self._auth("sk-admin"))
        assert r.status_code == 204
        # Now 404
        r2 = client.delete("/api/v1/router/routes/temp", headers=self._auth("sk-admin"))
        assert r2.status_code == 404

    def test_admin_upsert_model(self, client):
        r = client.put(
            "/api/v1/router/models/new-model",
            headers=self._auth("sk-admin"),
            json={
                "canonical_name": "new-model",
                "agy_model_flag": "New Model",
                "tier": "free",
                "description": "test",
                "enabled": True,
            },
        )
        assert r.status_code == 200
        assert r.json()["agy_model_flag"] == "New Model"

    def test_upsert_model_validates_tier(self, client):
        r = client.put(
            "/api/v1/router/models/bad",
            headers=self._auth("sk-admin"),
            json={"canonical_name": "bad", "agy_model_flag": "X", "tier": "bogus"},
        )
        assert r.status_code == 422

    def test_delete_model_referenced_returns_409(self, client):
        r = client.delete(
            "/api/v1/router/models/claude-opus",
            headers=self._auth("sk-admin"),
        )
        assert r.status_code == 409
        assert "referenced by route" in r.json()["detail"]

    def test_delete_unused_model_succeeds(self, client):
        # Create an unused model first
        client.put(
            "/api/v1/router/models/throwaway",
            headers=self._auth("sk-admin"),
            json={
                "canonical_name": "throwaway",
                "agy_model_flag": "X",
                "tier": "free",
            },
        )
        r = client.delete(
            "/api/v1/router/models/throwaway",
            headers=self._auth("sk-admin"),
        )
        assert r.status_code == 204

    def test_reset_requires_admin(self, client):
        r = client.put("/api/v1/router/config/reset", headers=self._auth("sk-ro"))
        assert r.status_code == 403

    def test_reset_succeeds_for_admin(self, client):
        # Mess with config first
        client.put(
            "/api/v1/router/routes/junk",
            headers=self._auth("sk-admin"),
            json={"priority_chain": ["claude-opus"]},
        )
        r = client.put("/api/v1/router/config/reset", headers=self._auth("sk-admin"))
        assert r.status_code == 200
        assert "junk" not in r.json()["routes"]

    def test_update_system_defaults(self, client):
        r = client.post(
            "/api/v1/router/system",
            headers=self._auth("sk-admin"),
            json={"default_cooldown_seconds": 900, "quota_keywords": ["my-key"]},
        )
        assert r.status_code == 200
        assert r.json()["default_cooldown_seconds"] == 900
        assert "my-key" in r.json()["quota_keywords"]

    def test_update_system_rejects_bad_keywords(self, client):
        r = client.post(
            "/api/v1/router/system",
            headers=self._auth("sk-admin"),
            json={"quota_keywords": [1, 2, 3]},
        )
        assert r.status_code == 422

    def test_list_routes_and_models(self, client):
        r1 = client.get("/api/v1/router/routes", headers=self._auth("sk-admin"))
        assert r1.status_code == 200
        assert "agy" in r1.json()["routes"]

        r2 = client.get("/api/v1/router/models", headers=self._auth("sk-admin"))
        assert r2.status_code == 200
        assert "claude-opus" in r2.json()["models"]

    def test_get_individual_route_404(self, client):
        r = client.get("/api/v1/router/routes/nope", headers=self._auth("sk-admin"))
        assert r.status_code == 404


# ── Admin UI test ─────────────────────────────────────────────


class TestAdminUI:
    @pytest.fixture
    def client(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rc, "DEFAULT_STATE_DIR", str(tmp_path))
        from prismatic.api.server import app
        return TestClient(app)

    def test_ui_served(self, client):
        r = client.get("/api/v1/router/ui")
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        assert "Prismatic Router Admin" in r.text
        # JS hooks present
        assert "loadAll" in r.text
        assert "upsertRoute" in r.text
        assert "upsertModel" in r.text
