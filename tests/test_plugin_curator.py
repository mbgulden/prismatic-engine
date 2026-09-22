"""Tests for the prismatic-curator capability plugin.

prismatic-curator is ADVISORY ONLY: it returns ranked capability suggestions
with reasons and never selects, attaches, enables, or rewires anything. The
boundary test below is the most important test in this file — it asserts the
route table is GET-only, that no enable/attach/mutate semantics exist in the
plugin source, and that suggest() never mutates plugin or loader state.

The swarmcurator primitive is stubbed via sys.modules injection in every
test that needs it — the real primitive is never imported.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import shutil
import sys
import types
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "prismatic" / "shipped_plugins" / "curator"

sys.path.insert(0, str(REPO_ROOT / "prismatic" / "shipped_plugins"))

import curator.plugin as curator_plugin  # noqa: E402
from curator.plugin import (  # noqa: E402
    PLUGIN_NAME,
    CuratorPlugin,
    _validate_config,
)
from prismatic.core.registry import (  # noqa: E402
    PluginLoader,
    set_default_plugin_loader,
)
from prismatic.interface.plugin import (  # noqa: E402
    PluginContext,
    PluginValidationError,
)


def _fingerprint(provider: str, external_id: str, title: str) -> str:
    raw = f"{provider.lower()}:{external_id.strip()}:{title.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@pytest.fixture()
def stub_swarmcurator(monkeypatch):
    """Inject a stub swarmcurator via sys.modules; reset the lazy cache."""
    fake_models = types.ModuleType("swarmcurator.models")
    fake_models.compute_fingerprint = _fingerprint
    fake_pkg = types.ModuleType("swarmcurator")
    fake_pkg.models = fake_models
    monkeypatch.setitem(sys.modules, "swarmcurator", fake_pkg)
    monkeypatch.setitem(sys.modules, "swarmcurator.models", fake_models)
    monkeypatch.setattr(curator_plugin, "_swarmlib", None)
    return fake_models


@pytest.fixture()
def no_swarmcurator(monkeypatch):
    """Simulate a missing/broken primitive: imports must fail."""
    monkeypatch.setitem(sys.modules, "swarmcurator", None)
    monkeypatch.setitem(sys.modules, "swarmcurator.models", None)
    monkeypatch.setattr(curator_plugin, "_swarmlib", None)


@pytest.fixture(autouse=True)
def _reset_default_plugin_loader():
    yield
    set_default_plugin_loader(None)


def make_context(config=None, monkeypatch=None, tmp_path=None) -> PluginContext:
    if monkeypatch is not None and tmp_path is not None:
        monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    return PluginContext(
        config=config or {}, db_connection=None, state_dir=str(tmp_path or ".")
    )


def make_plugin(config=None, **kw) -> CuratorPlugin:
    plugin = CuratorPlugin()
    plugin.on_init(make_context(config, **kw))
    return plugin


def stub_catalog() -> dict:
    """Fabricated loader capability contracts (the catalog the plugin reads)."""
    return {
        "prismatic-mesh": {
            "description": "Guest addressing and presence for mesh peers",
            "tags": ["infrastructure", "networking"],
        },
        "prismatic-router": {
            "description": "Policy-based ingress classification of requests",
            "tags": ["infrastructure", "routing"],
        },
        "prismatic-cron": {
            "description": "Fires configured jobs on cron schedules",
            "tags": ["infrastructure", "scheduling"],
            "keywords": ["timer", "periodic"],
        },
    }


# ── manifest ─────────────────────────────────────────────────────────────


def test_manifest_parses_with_required_fields():
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["name"] == "prismatic-curator"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "curator.plugin:CuratorPlugin"
    assert manifest["core_version_constraint"] == ">=0.2.0, <2.0.0"
    assert "swarmcurator>=0.1.0" in manifest["dependencies"]["pip"]
    assert set(manifest["tags"]) == {"infrastructure", "optional"}
    assert manifest["auto_enable"] is False
    assert manifest["config_schema"]["type"] == "object"


def test_entry_point_importable_and_matches_manifest():
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    module_path, class_name = manifest["entry_point"].split(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    assert cls is CuratorPlugin


def test_config_schema_accepts_valid_config():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    jsonschema.validate({"max_suggestions": 10}, manifest["config_schema"])
    jsonschema.validate({}, manifest["config_schema"])


def test_config_schema_rejects_bad_config():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"max_suggestions": 0}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"max_suggestions": 99}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"max_suggestions": "many"}, schema)


# ── lifecycle ────────────────────────────────────────────────────────────


def test_on_init_accepts_valid_config():
    assert _validate_config({"max_suggestions": 3}) == {"max_suggestions": 3}
    assert _validate_config({}) == {"max_suggestions": 5}
    plugin = make_plugin({"max_suggestions": 7})
    assert plugin._max_suggestions == 7


@pytest.mark.parametrize(
    "bad",
    [
        {"max_suggestions": 0},
        {"max_suggestions": 26},
        {"max_suggestions": -1},
        {"max_suggestions": "many"},
        {"max_suggestions": 2.5},
        {"max_suggestions": True},
    ],
)
def test_on_init_rejects_invalid_config(bad):
    with pytest.raises(PluginValidationError):
        _validate_config(bad)
    with pytest.raises(PluginValidationError):
        make_plugin(bad)


def test_suspend_resume_round_trip(stub_swarmcurator):
    plugin = make_plugin({"max_suggestions": 4})
    plugin.refresh_catalog(stub_catalog())

    snapshot = plugin.on_suspend()
    # JSON-serializable, no secrets, minimal config echo.
    json.dumps(snapshot)
    assert snapshot["plugin"] == PLUGIN_NAME
    assert snapshot["version"] == "0.1.0"
    assert snapshot["config"] == {"max_suggestions": 4}
    assert snapshot["catalog_names"] == sorted(stub_catalog())
    assert snapshot["advisory_only"] is True
    assert "secret" not in json.dumps(snapshot).lower()

    resumed = CuratorPlugin()
    resumed.on_init(make_context({}))
    resumed.on_resume(snapshot)
    assert resumed._max_suggestions == 4
    # Resume with empty state keeps defaults, never crashes.
    resumed.on_resume({})


def test_register_tools_returns_empty():
    assert CuratorPlugin().register_tools() == []


# ── disabled by default (real loader scan) ───────────────────────────────


def _scan_curator(tmp_path, monkeypatch, config=None):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
    plugins_dir = tmp_path / "plugins"
    shutil.copytree(PLUGIN_DIR, plugins_dir / "curator")
    ctx_config = {}
    if config is not None:
        ctx_config = {"plugin_configs": {PLUGIN_NAME: config}}
    ctx = PluginContext(config=ctx_config, db_connection=None, state_dir=str(tmp_path))
    loader = PluginLoader(core_version="0.2.0", plugins_dir=str(plugins_dir))
    loader.scan_and_load_plugins(ctx)
    return loader


def test_disabled_by_default_on_scan(tmp_path, monkeypatch):
    loader = _scan_curator(tmp_path, monkeypatch)
    assert PLUGIN_NAME in loader.loaded_plugins
    status = loader.plugin_status(PLUGIN_NAME)
    assert status["loaded"] is True
    assert status["enabled"] is False
    # The loader picked up the advisory capability contract.
    contract = loader.registered_capability_contracts[PLUGIN_NAME]
    assert contract["mutates_state"] is False


def test_disable_enable_preserves_state(tmp_path, monkeypatch):
    loader = _scan_curator(tmp_path, monkeypatch, config={"max_suggestions": 9})
    plugin = loader.loaded_plugins[PLUGIN_NAME]
    assert plugin._max_suggestions == 9

    loader.enable(PLUGIN_NAME)
    assert loader.plugin_status(PLUGIN_NAME)["enabled"] is True

    loader.disable(PLUGIN_NAME)
    assert loader.plugin_status(PLUGIN_NAME)["enabled"] is False

    loader.enable(PLUGIN_NAME)
    assert loader.loaded_plugins[PLUGIN_NAME]._max_suggestions == 9


# ── routes ───────────────────────────────────────────────────────────────


def test_routes_registered_and_get_only():
    routes = CuratorPlugin().register_api_routes()
    assert {(r["method"], r["path"]) for r in routes} == {
        ("GET", "/api/curator/suggest"),
        ("GET", "/api/curator/catalog"),
    }
    for route in routes:
        assert route["method"] == "GET"
        assert route["method"] not in ("POST", "PUT", "DELETE", "PATCH")
        assert route["handler"].startswith("curator.plugin:CuratorPlugin.")


def test_suggest_happy_path(stub_swarmcurator):
    plugin = make_plugin()
    plugin.refresh_catalog(stub_catalog())

    out = plugin.suggest(q="cron schedule jobs")
    assert out["query"] == "cron schedule jobs"
    assert out["catalog_size"] == 3
    assert out["advisory_only"] is True

    suggestions = out["suggestions"]
    assert suggestions, "expected ranked suggestions"
    top = suggestions[0]
    assert top["capability"] == "prismatic-cron"
    assert top["reasons"], "each suggestion must carry reasons"
    assert any("cron" in r for r in top["reasons"])
    assert top["advisory"] is True
    # Scores rank desc.
    scores = [s["score"] for s in suggestions]
    assert scores == sorted(scores, reverse=True)
    # Fingerprint is stable and matches the stub primitive's scheme.
    assert top["fingerprint"] == _fingerprint(
        PLUGIN_NAME, "prismatic-cron", "cron schedule jobs"
    )
    assert (
        plugin.suggest(q="cron schedule jobs")["suggestions"][0]["fingerprint"]
        == top["fingerprint"]
    )


def test_suggest_respects_limit(stub_swarmcurator):
    plugin = make_plugin({"max_suggestions": 10})
    plugin.refresh_catalog(stub_catalog())
    out = plugin.suggest(q="infrastructure", limit=1)
    assert len(out["suggestions"]) == 1


@pytest.mark.parametrize("query", ["", "   ", "!!!"])
def test_suggest_empty_query_returns_no_suggestions(stub_swarmcurator, query):
    plugin = make_plugin()
    plugin.refresh_catalog(stub_catalog())
    out = plugin.suggest(q=query)
    assert out["suggestions"] == []
    assert out["catalog_size"] == 3
    assert out["advisory_only"] is True


def test_suggest_no_match_returns_empty(stub_swarmcurator):
    plugin = make_plugin()
    plugin.refresh_catalog(stub_catalog())
    out = plugin.suggest(q="zzzzzqqqqq")
    assert out["suggestions"] == []


def test_catalog_route_returns_snapshot(stub_swarmcurator):
    plugin = make_plugin()
    plugin.refresh_catalog(stub_catalog())
    out = plugin.catalog()
    assert out["count"] == 3
    assert [e["name"] for e in out["capabilities"]] == sorted(stub_catalog())
    assert out["advisory_only"] is True


def test_refresh_catalog_deep_copies_and_never_mutates_loader_dicts(
    stub_swarmcurator,
):
    plugin = make_plugin()
    contracts = stub_catalog()
    before = copy.deepcopy(contracts)
    plugin.refresh_catalog(contracts)
    assert contracts == before
    # Mutating the caller's dicts afterwards does not affect the snapshot.
    contracts["prismatic-mesh"]["tags"].append("mutated")
    assert "mutated" not in plugin._catalog["prismatic-mesh"]["tags"]


# ── missing primitive degradation ────────────────────────────────────────


def test_missing_primitive_raises_clear_error(no_swarmcurator):
    plugin = make_plugin()
    plugin.refresh_catalog(stub_catalog())
    with pytest.raises(RuntimeError, match="prismatic-curator requires"):
        plugin.suggest(q="cron")


def test_loader_scan_unaffected_by_missing_primitive(
    tmp_path, monkeypatch, no_swarmcurator
):
    loader = _scan_curator(tmp_path, monkeypatch)
    assert PLUGIN_NAME in loader.loaded_plugins
    plugin = loader.loaded_plugins[PLUGIN_NAME]
    assert plugin.register_api_routes()[0]["method"] == "GET"


# ── BOUNDARY TEST (advisory-only scope guard) ─────────────────────────────
#
# The curator stays a capability plugin only while it stays advisory: it
# may return recommendations and must never auto-attach, auto-select,
# auto-enable, or rewire anything.


def test_boundary_route_table_is_get_only():
    """No write route may exist for the plugin to act through."""
    routes = CuratorPlugin().register_api_routes()
    assert routes, "expected registered routes"
    for route in routes:
        assert route["method"] == "GET", (
            f"non-GET route {route['method']} {route['path']}: "
            "an advisory plugin must not expose write routes"
        )


def test_boundary_no_mutation_semantics_in_plugin_source():
    """No method may reference enable/attach/mutate semantics."""
    src = Path(curator_plugin.__file__).read_text()
    for needle in ("loader.enable", ".attach(", "disable(", "unload("):
        assert needle not in src, (
            f"{needle!r} found in plugin source: the curator must not "
            "reference plugin/loader mutation semantics"
        )


def test_boundary_suggest_leaves_all_state_untouched(stub_swarmcurator):
    """Calling suggest twice mutates nothing — plugin or loader side."""
    plugin = make_plugin()
    loader_contracts = stub_catalog()
    plugin.refresh_catalog(loader_contracts)

    before_plugin_state = copy.deepcopy(vars(plugin))
    before_loader_contracts = copy.deepcopy(loader_contracts)

    first = plugin.suggest(q="cron schedule")
    second = plugin.suggest(q="mesh peers")

    assert vars(plugin) == before_plugin_state
    assert loader_contracts == before_loader_contracts
    # Fresh objects per call: no shared mutable state leaks out.
    assert first is not second
    assert first["suggestions"] is not second["suggestions"]


def test_boundary_capability_contract_declares_no_mutation():
    contract = CuratorPlugin().capability_contract()
    assert contract["mutates_state"] is False
    assert contract["write_routes"] == []
