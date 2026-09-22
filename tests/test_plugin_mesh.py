"""Tests for the prismatic-mesh capability plugin (prismatic/shipped_plugins/mesh/).

Written against the generic plugin lifecycle contract:
  PrismaticPlugin.on_suspend() -> dict / on_resume(state: dict),
  PluginLoader.enable/disable/unload(name), plugin state persisted at
  $PRISMATIC_HOME/plugin-state/<name>/state.json, manifest config_schema
  validated at attach, auto_enable: false.

The swarmmesh primitive is NOT installed on the test box (and not on
PyPI): every test that needs it installs a sys.modules stub. The missing
primitive is never imported for real.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "prismatic" / "shipped_plugins" / "mesh"

sys.path.insert(0, str(REPO_ROOT / "prismatic" / "shipped_plugins"))

import mesh.plugin as mesh_plugin  # noqa: E402
from mesh.plugin import (  # noqa: E402
    PLUGIN_NAME,
    MeshPlugin,
    plugin_state_dir,
)
from prismatic.core.registry import (  # noqa: E402
    PluginLoader,
    set_default_plugin_loader,
)
from prismatic.interface.plugin import (  # noqa: E402
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)


# ── swarmmesh stub ────────────────────────────────────────────────────


def _install_swarmmesh_stub(monkeypatch) -> None:
    """Inject a fake swarmmesh primitive; never touch the real one."""
    import types

    swarmmesh = types.ModuleType("swarmmesh")
    addressing = types.ModuleType("swarmmesh.addressing")

    def normalize_address(address):
        addr = str(address).strip().lower()
        if not addr:
            raise ValueError("empty address")
        if any(ch.isspace() for ch in addr):
            raise ValueError(f"address contains whitespace: {address!r}")
        return addr

    addressing.normalize_address = normalize_address
    swarmmesh.addressing = addressing
    monkeypatch.setitem(sys.modules, "swarmmesh", swarmmesh)
    monkeypatch.setitem(sys.modules, "swarmmesh.addressing", addressing)
    # reset the plugin's lazy-import cache so it picks up the stub
    monkeypatch.setattr(mesh_plugin, "_swarmmesh_normalize", None)


def _remove_swarmmesh(monkeypatch) -> None:
    """Scrub any swarmmesh (stub or real) from sys.modules + reset cache."""
    monkeypatch.delitem(sys.modules, "swarmmesh", raising=False)
    monkeypatch.delitem(sys.modules, "swarmmesh.addressing", raising=False)
    monkeypatch.setattr(mesh_plugin, "_swarmmesh_normalize", None)


def _real_swarmmesh_present() -> bool:
    try:
        return importlib.util.find_spec("swarmmesh") is not None
    except Exception:
        return False


def make_context(tmp_home: Path, **plugin_cfg) -> PluginContext:
    os.environ["PRISMATIC_HOME"] = str(tmp_home)
    return PluginContext(
        config=dict(plugin_cfg), db_connection=None, state_dir=str(tmp_home)
    )


def announce(
    plugin: MeshPlugin, peer_id="guest-1", address="guest://worker-1:9000", **kw
):
    payload = {"peer_id": peer_id, "address": address}
    payload.update(kw)
    return plugin.announce_peer(payload)


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    _install_swarmmesh_stub(monkeypatch)
    return MeshPlugin()


@pytest.fixture(autouse=True)
def _reset_default_plugin_loader():
    """PluginLoader registers itself as the process default on init;
    reset it after every test so loader instances never leak between
    tests in this file."""
    yield
    set_default_plugin_loader(None)


# ── manifest ─────────────────────────────────────────────────────────


def test_manifest_parses_with_required_fields():
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["name"] == "prismatic-mesh"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "mesh.plugin:MeshPlugin"
    assert manifest["core_version_constraint"] == ">=0.2.0, <2.0.0"
    assert "swarmmesh>=0.1.0" in manifest["dependencies"]["pip"]
    assert set(manifest["tags"]) == {"infrastructure", "optional"}
    assert manifest["auto_enable"] is False
    assert manifest["config_schema"]["type"] == "object"
    assert manifest["config_schema"]["required"] == []
    for event in ("peer_registered", "peer_heartbeat", "peer_deregistered"):
        assert event in manifest["audit_events"]


def test_entry_point_importable():
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    module_path, class_name = manifest["entry_point"].split(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    assert issubclass(cls, PrismaticPlugin)
    assert cls is MeshPlugin


def test_config_schema_accepts_valid_config():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    jsonschema.validate(
        {"max_peers": 50, "heartbeat_ttl_sec": 300, "default_trust_tier": "trusted"},
        schema,
    )  # must not raise
    jsonschema.validate({}, schema)  # empty config is fine


def test_config_schema_rejects_bad_config():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"max_peers": 0}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"default_trust_tier": "root"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"heartbeat_ttl_sec": -1}, schema)


# ── lifecycle: init ──────────────────────────────────────────────────


def test_on_init_accepts_valid_config(plugin, tmp_path):
    ctx = make_context(
        tmp_path, max_peers=10, heartbeat_ttl_sec=60, default_trust_tier="trusted"
    )
    plugin.on_init(ctx)
    assert plugin._max_peers == 10
    assert plugin._heartbeat_ttl_sec == 60
    assert plugin._default_trust_tier == "trusted"
    assert plugin.peers_status() == []


@pytest.mark.parametrize(
    "bad_cfg",
    [
        {"max_peers": 0},
        {"max_peers": -5},
        {"max_peers": "many"},
        {"heartbeat_ttl_sec": -1},
        {"default_trust_tier": "root"},
        {"default_trust_tier": ""},
    ],
)
def test_on_init_rejects_invalid_config(tmp_path, monkeypatch, bad_cfg):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    _install_swarmmesh_stub(monkeypatch)
    p = MeshPlugin()
    with pytest.raises(PluginValidationError):
        p.on_init(make_context(tmp_path, **bad_cfg))


def test_on_init_accepts_loader_nested_plugin_configs(tmp_path, monkeypatch):
    """The generic PluginLoader nests the validated plugin config at
    context.config["plugin_configs"]["prismatic-mesh"]; on_init must read
    from there, not only from a top-level config."""
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    _install_swarmmesh_stub(monkeypatch)
    p = MeshPlugin()
    ctx = PluginContext(
        config={"plugin_configs": {"prismatic-mesh": {"max_peers": 7}}},
        db_connection=None,
        state_dir=str(tmp_path),
    )
    p.on_init(ctx)
    assert p._max_peers == 7


def test_on_init_does_not_require_primitive(tmp_path, monkeypatch):
    """Base installs have no swarmmesh: attach/enable must still succeed."""
    if _real_swarmmesh_present():
        pytest.skip("real swarmmesh installed; base-install check not applicable")
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    _remove_swarmmesh(monkeypatch)
    p = MeshPlugin()
    p.on_init(make_context(tmp_path))  # must not raise


# ── announce / peers / deregister ────────────────────────────────────


def test_announce_registers_new_peer(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path))
    result = announce(plugin)
    assert result["registered"] is True
    peer = result["peer"]
    assert peer["peer_id"] == "guest-1"
    assert peer["address"] == "guest://worker-1:9000"
    assert peer["trust_tier"] == "untrusted"  # config default
    assert peer["announce_count"] == 1
    assert peer["stale"] is False
    assert peer["first_seen"] and peer["last_seen"]

    events = plugin.presence_events()
    assert len(events) == 1
    assert events[0]["event"] == "peer_registered"
    assert events[0]["peer_id"] == "guest-1"


def test_announce_normalizes_address_via_primitive(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path))
    result = announce(plugin, address="  GUEST://Worker-2:9000  ")
    assert result["peer"]["address"] == "guest://worker-2:9000"


def test_announce_second_time_is_heartbeat(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path))
    announce(plugin)
    result = announce(plugin, address="guest://worker-1:9001")
    assert result["registered"] is False
    assert result["peer"]["announce_count"] == 2
    assert result["peer"]["address"] == "guest://worker-1:9001"
    kinds = [e["event"] for e in plugin.presence_events()]
    assert kinds == ["peer_registered", "peer_heartbeat"]


def test_peers_status_lists_sorted_table(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path))
    announce(plugin, peer_id="zeta")
    announce(plugin, peer_id="alpha")
    peers = plugin.peers_status()
    assert [p["peer_id"] for p in peers] == ["alpha", "zeta"]
    assert set(peers[0]) == {
        "peer_id",
        "address",
        "trust_tier",
        "first_seen",
        "last_seen",
        "announce_count",
        "stale",
    }


def test_stale_flag_computed_at_read_time(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, heartbeat_ttl_sec=3600))
    announce(plugin)
    assert plugin.peers_status()[0]["stale"] is False
    # backdate last_seen past the TTL: no background thread involved
    plugin._peers["guest-1"]["last_seen"] = "2020-01-01T00:00:00+00:00"
    assert plugin.peers_status()[0]["stale"] is True


def test_deregister_removes_peer(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path))
    announce(plugin)
    out = plugin.deregister_peer("guest-1")
    assert out == {"peer_id": "guest-1", "removed": True}
    assert plugin.peers_status() == []
    assert plugin.presence_events()[-1]["event"] == "peer_deregistered"

    out = plugin.deregister_peer("guest-1")  # already gone
    assert out["removed"] is False
    # no new event for a no-op
    assert plugin.presence_events()[-1]["event"] == "peer_deregistered"


def test_announce_rejects_bad_payloads(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path))
    with pytest.raises(PluginValidationError):
        plugin.announce_peer("not-a-dict")
    with pytest.raises(PluginValidationError):
        plugin.announce_peer({"address": "guest://x"})
    with pytest.raises(PluginValidationError):
        plugin.announce_peer({"peer_id": "   ", "address": "guest://x"})
    with pytest.raises(PluginValidationError):
        plugin.announce_peer({"peer_id": "g", "address": "   "})
    with pytest.raises(PluginValidationError):
        plugin.announce_peer(
            {"peer_id": "g", "address": "guest://x", "trust_tier": "root"}
        )
    with pytest.raises(PluginValidationError):
        plugin.announce_peer({"peer_id": "g", "address": "has whitespace"})
    with pytest.raises(PluginValidationError):
        plugin.deregister_peer("")


def test_max_peers_rejects_new_but_allows_heartbeat(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, max_peers=1))
    announce(plugin, peer_id="one")
    with pytest.raises(PluginValidationError, match="max_peers"):
        announce(plugin, peer_id="two")
    # heartbeat for the known peer still works at capacity
    result = announce(plugin, peer_id="one")
    assert result["registered"] is False


def test_presence_events_forwarded_to_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    _install_swarmmesh_stub(monkeypatch)

    seen = []

    class FakeTelemetry:
        def record_event(self, event):
            seen.append(event)

    p = MeshPlugin()
    ctx = make_context(tmp_path)
    ctx.telemetry_client = FakeTelemetry()
    p.on_init(ctx)
    announce(p)
    assert len(seen) == 1
    assert seen[0]["event"] == "peer_registered"
    assert seen[0]["peer_id"] == "guest-1"


# ── suspend / resume ─────────────────────────────────────────────────


def test_suspend_resume_round_trips_peer_table(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path))
    announce(plugin, peer_id="a", trust_tier="trusted")
    announce(plugin, peer_id="b")

    snapshot = plugin.on_suspend()
    assert snapshot["plugin"] == PLUGIN_NAME
    assert {p["peer_id"] for p in snapshot["peers"]} == {"a", "b"}
    assert snapshot["peers"][0]["announce_count"] == 1
    # JSON-serializable, no secrets: only the documented record keys
    json.dumps(snapshot)
    for peer in snapshot["peers"]:
        assert set(peer) == {
            "peer_id",
            "address",
            "trust_tier",
            "first_seen",
            "last_seen",
            "announce_count",
        }
    # belt-and-braces file written at the contract path
    assert (plugin_state_dir() / "state.json").exists()

    resumed = MeshPlugin()
    resumed.on_resume(snapshot)
    peers = {p["peer_id"]: p for p in resumed.peers_status()}
    assert set(peers) == {"a", "b"}
    assert peers["a"]["trust_tier"] == "trusted"
    assert [e["event"] for e in resumed.presence_events()] == [
        "peer_registered",
        "peer_registered",
    ]


def test_on_resume_with_empty_state_is_safe(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path))
    plugin.on_resume({})
    assert plugin.peers_status() == []


def test_on_init_restores_peers_from_state_file(tmp_path, monkeypatch):
    """Belt-and-braces: a fresh instance re-reads the loader's state.json
    even when no suspend snapshot flows through on_resume()."""
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    _install_swarmmesh_stub(monkeypatch)
    first = MeshPlugin()
    first.on_init(make_context(tmp_path))
    announce(first, peer_id="survivor")
    first.on_suspend()

    second = MeshPlugin()
    second.on_init(make_context(tmp_path))  # no on_resume call
    assert [p["peer_id"] for p in second.peers_status()] == ["survivor"]


# ── loader: scan / disable / enable ──────────────────────────────────


def _stage_plugin_for_scan(tmp_plugins: Path) -> None:
    dest = tmp_plugins / "mesh"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(PLUGIN_DIR / "plugin-manifest.yaml", dest / "plugin-manifest.yaml")
    shutil.copy(PLUGIN_DIR / "plugin.py", dest / "plugin.py")


def test_loader_scan_registers_but_disables_plugin(tmp_path, monkeypatch):
    """Fresh loader scan: plugin is registered (routes known) but starts
    DISABLED — auto_enable: false. Simulates a base install: no
    swarmmesh present, and the scan must not crash."""
    if _real_swarmmesh_present():
        pytest.skip("real swarmmesh installed; base-install check not applicable")
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
    _remove_swarmmesh(monkeypatch)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _stage_plugin_for_scan(plugins_dir)

    loader = PluginLoader(core_version="0.2.0", plugins_dir=str(plugins_dir))
    ctx = PluginContext(config={}, db_connection=None, state_dir=str(tmp_path))
    loader.scan_and_load_plugins(ctx)

    status = loader.plugin_status("prismatic-mesh")
    assert status["enabled"] is False
    assert status["loaded"] is True
    assert status["state_preserved"] is False

    routes = [
        r for r in loader.registered_api_routes if r["plugin"] == "prismatic-mesh"
    ]
    assert {(r["method"], r["path"]) for r in routes} == {
        ("GET", "/api/mesh/peers"),
        ("POST", "/api/mesh/announce"),
        ("DELETE", "/api/mesh/peers/{id}"),
    }
    assert loader.registered_tools == []


def test_disable_enable_preserves_peer_table(tmp_path, monkeypatch):
    """Full loader cycle: enable → announce → unload (persists state to
    $PRISMATIC_HOME) → enable → peer table restored."""
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
    _install_swarmmesh_stub(monkeypatch)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _stage_plugin_for_scan(plugins_dir)

    loader = PluginLoader(core_version="0.2.0", plugins_dir=str(plugins_dir))
    ctx = PluginContext(config={}, db_connection=None, state_dir=str(tmp_path))
    loader.scan_and_load_plugins(ctx)

    loader.enable("prismatic-mesh")
    plugin = loader.loaded_plugins["prismatic-mesh"]
    plugin.announce_peer({"peer_id": "kept", "address": "guest://kept:1"})

    loader.unload("prismatic-mesh")  # disable → persist → drop instance
    state_path = (
        Path(os.environ["PRISMATIC_HOME"])
        / "plugin-state"
        / "prismatic-mesh"
        / "state.json"
    )
    assert state_path.exists()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["version"] == 1
    assert [p["peer_id"] for p in saved["state"]["peers"]] == ["kept"]

    loader.enable("prismatic-mesh")  # re-load + resume with preserved state
    restored = loader.loaded_plugins["prismatic-mesh"]
    assert [p["peer_id"] for p in restored.peers_status()] == ["kept"]
    assert loader.plugin_status("prismatic-mesh")["enabled"] is True


# ── missing primitive degradation ────────────────────────────────────


def test_missing_primitive_announce_raises_clear_error(tmp_path, monkeypatch):
    """Without swarmmesh, announce-time use fails with a clear
    RuntimeError naming the package — never an ImportError traceback."""
    if _real_swarmmesh_present():
        pytest.skip("real swarmmesh installed; degradation check not applicable")
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    _remove_swarmmesh(monkeypatch)
    p = MeshPlugin()
    p.on_init(make_context(tmp_path))  # attach/enable path stays green
    with pytest.raises(RuntimeError) as excinfo:
        p.announce_peer({"peer_id": "g1", "address": "guest://g1:1"})
    message = str(excinfo.value)
    assert "swarmmesh" in message
    assert "pip install" in message


def test_missing_primitive_peers_listing_still_works(tmp_path, monkeypatch):
    """The peer table is the plugin's own state: listing it needs no
    primitive, so reads degrade gracefully."""
    if _real_swarmmesh_present():
        pytest.skip("real swarmmesh installed; degradation check not applicable")
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    _install_swarmmesh_stub(monkeypatch)
    p = MeshPlugin()
    p.on_init(make_context(tmp_path))
    announce(p, peer_id="listed")
    _remove_swarmmesh(monkeypatch)
    peers = p.peers_status()
    assert [x["peer_id"] for x in peers] == ["listed"]
    assert p.register_api_routes()  # route table unaffected too


# ── boundary: no workload routing / placement / choreography ─────────


def test_boundary_no_harness_surface():
    """Mesh records who is present — never what anyone should do next.
    Assert there is no workload-routing, placement, sequencing, or
    choreography surface anywhere in the plugin or its routes."""
    routes = MeshPlugin().register_api_routes()
    assert {(r["method"], r["path"]) for r in routes} == {
        ("GET", "/api/mesh/peers"),
        ("POST", "/api/mesh/announce"),
        ("DELETE", "/api/mesh/peers/{id}"),
    }

    forbidden = re.compile(
        r"(?i)(dispatch|choreograph|next_step|sequence_steps|pick_tool|"
        r"select_tool|call_llm|invoke_llm|route_workload|place_workload|"
        r"assign_task|schedule_task)"
    )
    # Only methods defined BY this plugin count: lifecycle hooks inherited
    # from the PrismaticPlugin ABC (e.g. on_issue_dispatch) are framework
    # plumbing, not plugin surface.
    harnessy = [
        name
        for name in vars(MeshPlugin)
        if forbidden.search(name) and not name.startswith("__")
    ]
    assert harnessy == [], f"harness-like methods present: {harnessy}"

    # The forbidden words may only appear in *negated* boundary statements
    # ("never routes workloads", "no dispatch path", ...), never as live
    # code or affirmative claims.
    for path in (
        PLUGIN_DIR / "plugin.py",
        PLUGIN_DIR / "plugin-manifest.yaml",
        PLUGIN_DIR / "README.md",
    ):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            lowered = line.lower()
            hits = [w for w in ("dispatch", "choreograph", "sandbox") if w in lowered]
            if hits:
                assert "never" in lowered or "no " in lowered or "not " in lowered, (
                    f"{path.name}:{lineno}: non-negated boundary word {hits}: {line.strip()!r}"
                )

    contract = MeshPlugin().capability_contract()
    assert "never routes workloads" in " ".join(contract["notes"]).lower()


def test_register_tools_returns_empty():
    assert MeshPlugin().register_tools() == []


def test_capability_contract_states_boundary():
    contract = MeshPlugin().capability_contract()
    assert contract["capability"] == "mesh-presence"
    assert contract["plugin"] == PLUGIN_NAME
    assert contract["tools"] == []
    notes = " ".join(contract["notes"]).lower()
    assert "never routes workloads" in notes
    assert "never decides placement" in notes
    assert "no polling loop" in notes
