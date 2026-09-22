"""Tests for the prismatic-consensus capability plugin (plugins/consensus/).

Written against the generic plugin lifecycle contract:
  PrismaticPlugin.on_init(context) -> None,
  PluginLoader.enable/disable/unload(name), plugin state persisted at
  /plugin-state/<name>/state.json, manifest config_schema
  validated at attach, auto_enable: false.

The swarmconsensus primitive is NOT installed on this box and NOT on PyPI,
so every test stubs it via sys.modules injection -- the plugin imports it
lazily at use time (never at module top level), so the loader scan and the
plugin import itself never touch the real package.

Scope under test: propose/approve/status routes, quorum met at threshold,
expiry evaluated at query time (event-driven on read, no sweeper),
no self-approval, no duplicate approval, suspend/resume round-trip of open
quorums, missing-primitive degradation, and the boundary assertion that no
code path writes gate decisions (the kernel reads the plugin; the plugin
never drives the kernel).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugins" / "consensus"
PLUGIN_SRC = PLUGIN_DIR / "plugin.py"

sys.path.insert(0, str(REPO_ROOT / "plugins"))

import consensus.plugin as consensus_plugin  # noqa: E402
from consensus.plugin import (  # noqa: E402
    PLUGIN_NAME,
    ConsensusPlugin,
    _iso,
    _reset_swarmconsensus_cache,
    _swarmconsensus_or_raise,
    _utcnow,
    plugin_state_dir,
)
from prismatic.interface.plugin import (  # noqa: E402
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)


def _install_stub(monkeypatch):
    """Stub the swarmconsensus primitive via sys.modules injection."""
    _reset_swarmconsensus_cache()
    fake = types.ModuleType("swarmconsensus")
    fake_quorum = types.ModuleType("swarmconsensus.quorum")

    def evaluate_quorum(*, required_approvals, approvals, expires_at, now):
        # Mirrors the documented contract: met on threshold, failed past
        # expiry (UTC ISO-8601 strings compare lexicographically), else
        # pending.
        if approvals >= required_approvals:
            return "met"
        if now > expires_at:
            return "failed"
        return "pending"

    fake_quorum.evaluate_quorum = evaluate_quorum
    fake.quorum = fake_quorum
    monkeypatch.setitem(sys.modules, "swarmconsensus", fake)
    monkeypatch.setitem(sys.modules, "swarmconsensus.quorum", fake_quorum)
    return fake


@pytest.fixture(autouse=True)
def stub_swarmconsensus(monkeypatch):
    _install_stub(monkeypatch)
    yield
    _reset_swarmconsensus_cache()


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    return ConsensusPlugin()


def make_context(tmp_home: Path, config: dict) -> PluginContext:
    os.environ["PRISMATIC_HOME"] = str(tmp_home)
    return PluginContext(config=config, db_connection=None, state_dir=str(tmp_home))


def valid_config(**overrides):
    cfg = {
        "default_required_approvals": 2,
        "max_required_approvals": 5,
        "default_expiry_sec": 3600,
        "max_expiry_sec": 86400,
        "quorum_policy": {
            "deploy-prod": {
                "required_approvals": 3,
                "description": "Production deploys",
            },
        },
    }
    cfg.update(overrides)
    return cfg


def propose(plugin, subject="ship it", proposer="michael", **kw):
    payload = {"subject": subject, "proposer": proposer}
    payload.update(kw)
    return plugin.propose_quorum(payload)


# -- manifest ------------------------------------------------------------


def test_manifest_parses_with_required_fields():
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["name"] == "prismatic-consensus"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "consensus.plugin:ConsensusPlugin"
    assert manifest["core_version_constraint"] == ">=0.2.0, <2.0.0"
    assert "swarmconsensus>=0.1.0" in manifest["dependencies"]["pip"]
    assert set(manifest["tags"]) == {"infrastructure", "optional"}
    assert manifest["auto_enable"] is False
    assert manifest["config_schema"]["type"] == "object"
    methods_paths = {(e["method"], e["path"]) for e in manifest["endpoints"]}
    assert methods_paths == {
        ("POST", "/api/consensus/propose"),
        ("POST", "/api/consensus/approve"),
        ("GET", "/api/consensus/status/{id}"),
    }


def test_entry_point_importable_and_is_plugin():
    import importlib

    module = importlib.import_module("consensus.plugin")
    cls = getattr(module, "ConsensusPlugin")
    assert issubclass(cls, PrismaticPlugin)
    assert consensus_plugin.PLUGIN_NAME == "prismatic-consensus"


def test_config_schema_accepts_valid_config():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    jsonschema.validate(valid_config(), manifest["config_schema"])  # must not raise
    jsonschema.validate({}, manifest["config_schema"])  # all-optional


def test_config_schema_rejects_bad_policy_entry():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    bad = valid_config(quorum_policy={"x": {"required_approvals": 0}})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


# -- lifecycle: on_init --------------------------------------------------


def test_on_init_accepts_valid_config(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    assert plugin._default_required == 2
    assert plugin._max_required == 5
    assert plugin._quorum_policy["deploy-prod"]["required_approvals"] == 3
    assert (plugin_state_dir()).is_dir()


@pytest.mark.parametrize(
    "bad_cfg",
    [
        {"default_required_approvals": 0},
        {"default_required_approvals": True},
        {"max_required_approvals": 1, "default_required_approvals": 2},
        {"default_expiry_sec": 999999, "max_expiry_sec": 60},
        {"quorum_policy": {"x": {"required_approvals": 0}}},
        {"quorum_policy": {"x": {"required_approvals": 99}}},  # > max 5
        {"quorum_policy": {"x": "not-an-object"}},
        {"quorum_policy": ["not-an-object"]},
    ],
)
def test_on_init_rejects_invalid_config(plugin, tmp_path, bad_cfg):
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, valid_config(**bad_cfg)))


def test_on_init_accepts_loader_nested_plugin_configs(plugin, tmp_path, monkeypatch):
    """The generic PluginLoader nests the validated plugin config at
    context.config["plugin_configs"]["prismatic-consensus"]; on_init must
    read from there, not only from a top-level config."""
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    ctx = PluginContext(
        config={"plugin_configs": {"prismatic-consensus": valid_config()}},
        db_connection=None,
        state_dir=str(tmp_path),
    )
    plugin.on_init(ctx)
    assert plugin._quorum_policy["deploy-prod"]["required_approvals"] == 3


# -- suspend / resume round-trip -----------------------------------------


def test_suspend_resume_round_trips_open_quorums(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    r1 = propose(
        plugin,
        subject="deploy",
        proposer="michael",
        required_approvals=2,
        expires_in_sec=3600,
    )
    r2 = propose(
        plugin,
        subject="refund",
        proposer="ops-lead",
        decision="deploy-prod",
        expires_in_sec=7200,
    )
    assert r1["ok"] and r2["ok"]
    plugin.approve_quorum({"id": r1["id"], "approver": "alice"})

    snapshot = plugin.on_suspend()
    assert snapshot["plugin"] == "prismatic-consensus"
    assert len(snapshot["proposals"]) == 2
    # belt-and-braces file written at the contract path
    assert (plugin_state_dir() / "state.json").exists()

    resumed = ConsensusPlugin()
    resumed.on_resume(snapshot)
    assert set(resumed._proposals) == {r1["id"], r2["id"]}
    # Partial approval state survives the round-trip...
    st1 = resumed.quorum_status(r1["id"])
    assert st1["ok"] and st1["status"] == "pending"
    assert [a["approver"] for a in st1["approvals"]] == ["alice"]
    # ...and the quorum can still be met after resume.
    done = resumed.approve_quorum({"id": r1["id"], "approver": "bob"})
    assert done["ok"] and done["status"] == "met"
    # Policy-bound proposal keeps its decision + required count.
    st2 = resumed.quorum_status(r2["id"])
    assert st2["required_approvals"] == 3
    assert st2["decision"] == "deploy-prod"


def test_suspend_snapshot_is_json_serializable_and_secret_free(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    r = propose(plugin, proposer="michael", expires_in_sec=3600)
    plugin.approve_quorum({"id": r["id"], "approver": "alice"})
    snapshot = plugin.on_suspend()
    round_tripped = json.loads(json.dumps(snapshot))  # must not raise
    assert round_tripped["proposals"]
    for proposal in round_tripped["proposals"]:
        for approval in proposal["approvals"]:
            # Caller identities only -- never secrets.
            assert set(approval.keys()) == {"approver", "at"}
    blob = json.dumps(snapshot).lower()
    for secret_word in ("password", "api_key", "apikey", "bearer", "client_secret"):
        assert secret_word not in blob


def test_on_resume_merges_without_clobbering_live_state(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    live = propose(plugin, subject="live", proposer="michael", expires_in_sec=3600)
    snapshot = plugin.on_suspend()
    # A second proposal opened after the snapshot must survive the merge.
    later = propose(plugin, subject="later", proposer="ops", expires_in_sec=3600)
    plugin.on_resume(snapshot)
    assert set(plugin._proposals) == {live["id"], later["id"]}


def test_on_resume_ignores_malformed_records(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    plugin.on_resume(
        {
            "proposals": [
                {
                    "id": "nope",
                    "approvals": "not-a-list",
                    "required_approvals": 2,
                    "expires_at": _iso(_utcnow()),
                },
                "not-a-dict",
                {
                    "id": "alsono",
                    "approvals": [],
                    "required_approvals": "two",
                    "expires_at": _iso(_utcnow()),
                },
            ]
        }
    )
    assert plugin._proposals == {}


# -- disabled by default ---------------------------------------------------


def _scan_loader(tmp_path, monkeypatch):
    """Fresh loader scan over a plugins dir containing only consensus."""
    from prismatic.core.registry import PluginLoader

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "consensus").symlink_to(PLUGIN_DIR, target_is_directory=True)
    loader = PluginLoader(core_version="1.0.0", plugins_dir=str(plugins_dir))
    ctx = PluginContext(config={}, db_connection=None, state_dir=str(tmp_path))
    loader.scan_and_load_plugins(ctx)
    return loader


def test_loader_scan_registers_but_does_not_enable(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    loader = _scan_loader(tmp_path, monkeypatch)
    assert PLUGIN_NAME in loader.loaded_plugins
    assert loader.enabled_plugins[PLUGIN_NAME] is False
    assert isinstance(loader.loaded_plugins[PLUGIN_NAME], ConsensusPlugin)


def test_core_never_imports_plugin():
    """Importing core modules must not pull the plugin module in."""
    code = (
        "import sys; "
        "import prismatic.core.registry; "
        "import prismatic.interface.plugin; "
        'assert "consensus.plugin" not in sys.modules, '
        '"core imported the consensus plugin module"'
    )
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        cwd=str(REPO_ROOT),
        capture_output=True,
    )


# -- routes ----------------------------------------------------------------


def test_register_api_routes(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    routes = plugin.register_api_routes()
    by_path = {(r["method"], r["path"]): r for r in routes}
    assert ("POST", "/api/consensus/propose") in by_path
    assert ("POST", "/api/consensus/approve") in by_path
    assert ("GET", "/api/consensus/status/{id}") in by_path
    # Every route stays inside the plugin's own namespace.
    for r in routes:
        assert r["path"].startswith("/api/consensus/")
        assert r["handler"].startswith("consensus.plugin:ConsensusPlugin.")


def test_propose_approve_status_happy_path_reaches_quorum(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    opened = propose(
        plugin,
        subject="ship v2",
        proposer="michael",
        required_approvals=2,
        expires_in_sec=3600,
    )
    assert opened["ok"] is True
    assert opened["status"] == "pending"
    pid = opened["id"]

    first = plugin.approve_quorum({"id": pid, "approver": "alice"})
    assert first["ok"] and first["status"] == "pending"
    assert first["approvals"] == 1

    status = plugin.quorum_status(pid)
    assert status["ok"] and status["status"] == "pending"
    assert status["required_approvals"] == 2
    assert [a["approver"] for a in status["approvals"]] == ["alice"]

    second = plugin.approve_quorum({"id": pid, "approver": "bob"})
    assert second["ok"] and second["status"] == "met"
    assert second["approvals"] == 2

    # Quorum met: further approvals are closed.
    closed = plugin.approve_quorum({"id": pid, "approver": "carol"})
    assert closed["ok"] is False
    assert closed["error"] == "proposal_not_pending"


def test_quorum_failed_on_expiry(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    opened = propose(plugin, proposer="michael", expires_in_sec=3600)
    pid = opened["id"]
    # Push the expiry into the past: status is computed at query time,
    # event-driven on read -- no background sweeper involved.
    plugin._proposals[pid]["expires_at"] = _iso(_utcnow() - timedelta(seconds=1))

    status = plugin.quorum_status(pid)
    assert status["ok"] and status["status"] == "failed"

    late = plugin.approve_quorum({"id": pid, "approver": "alice"})
    assert late["ok"] is False
    assert late["error"] == "proposal_not_pending"


def test_self_approval_rejected(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    opened = propose(plugin, proposer="michael", expires_in_sec=3600)
    rejected = plugin.approve_quorum({"id": opened["id"], "approver": "michael"})
    assert rejected["ok"] is False
    assert rejected["error"] == "self_approval"


def test_double_approval_by_same_caller_rejected(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    opened = propose(
        plugin, proposer="michael", required_approvals=3, expires_in_sec=3600
    )
    first = plugin.approve_quorum({"id": opened["id"], "approver": "alice"})
    assert first["ok"] is True
    again = plugin.approve_quorum({"id": opened["id"], "approver": "alice"})
    assert again["ok"] is False
    assert again["error"] == "duplicate_approval"
    # The duplicate did not inflate the approval count.
    assert len(plugin.quorum_status(opened["id"])["approvals"]) == 1


def test_approve_unknown_proposal_rejected(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    result = plugin.approve_quorum({"id": "does-not-exist", "approver": "alice"})
    assert result["ok"] is False
    assert result["error"] == "unknown_proposal"
    assert plugin.quorum_status("does-not-exist")["error"] == "unknown_proposal"


def test_propose_validates_inputs(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    assert propose(plugin, subject="", proposer="michael")["error"] == "invalid_request"
    assert propose(plugin, subject="x", proposer="")["error"] == "invalid_request"
    assert propose(plugin, required_approvals=0)["error"] == "invalid_request"
    assert propose(plugin, required_approvals=99)["error"] == "invalid_request"
    assert propose(plugin, expires_in_sec=0)["error"] == "invalid_request"
    assert propose(plugin, expires_at="not-a-time")["error"] == "invalid_request"
    past = _iso(_utcnow() - timedelta(seconds=10))
    assert propose(plugin, expires_at=past)["error"] == "invalid_request"
    assert (
        propose(
            plugin, expires_at=_iso(_utcnow() + timedelta(seconds=5)), expires_in_sec=60
        )["error"]
        == "invalid_request"
    )


def test_quorum_policy_decision_drives_required_approvals(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, valid_config()))
    opened = propose(
        plugin, proposer="michael", decision="deploy-prod", expires_in_sec=3600
    )
    assert opened["ok"] and opened["required_approvals"] == 3
    status = plugin.quorum_status(opened["id"])
    assert status["decision"] == "deploy-prod"

    unknown = propose(plugin, proposer="michael", decision="no-such-decision")
    assert unknown["ok"] is False
    assert unknown["error"] == "unknown_decision"

    conflict = propose(
        plugin, proposer="michael", decision="deploy-prod", required_approvals=2
    )
    assert conflict["ok"] is False
    assert conflict["error"] == "conflicting_required_approvals"


def test_register_tools_returns_empty(plugin):
    assert plugin.register_tools() == []


# -- missing primitive -------------------------------------------------------


def test_missing_primitive_raises_clear_error_at_use_time(
    plugin, tmp_path, monkeypatch
):
    """With swarmconsensus absent, use-time calls raise the clear
    RuntimeError; the import itself (and the loader scan) never crashes."""
    monkeypatch.delitem(sys.modules, "swarmconsensus", raising=False)
    monkeypatch.delitem(sys.modules, "swarmconsensus.quorum", raising=False)
    _reset_swarmconsensus_cache()

    plugin.on_init(make_context(tmp_path, valid_config()))
    with pytest.raises(RuntimeError, match="swarmconsensus"):
        _swarmconsensus_or_raise()
    # Re-importing the module must not raise either.
    import importlib

    importlib.reload(consensus_plugin)


def test_routes_degrade_without_traceback_when_primitive_missing(
    plugin, tmp_path, monkeypatch
):
    monkeypatch.delitem(sys.modules, "swarmconsensus", raising=False)
    monkeypatch.delitem(sys.modules, "swarmconsensus.quorum", raising=False)
    _reset_swarmconsensus_cache()

    plugin.on_init(make_context(tmp_path, valid_config()))
    opened = propose(plugin, proposer="michael", expires_in_sec=3600)
    assert opened["ok"] is False
    assert opened["error"] == "primitive_not_installed"
    assert "swarmconsensus" in opened["detail"]


def test_loader_scan_unaffected_by_missing_primitive(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "swarmconsensus", raising=False)
    monkeypatch.delitem(sys.modules, "swarmconsensus.quorum", raising=False)
    _reset_swarmconsensus_cache()
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    loader = _scan_loader(tmp_path, monkeypatch)
    assert PLUGIN_NAME in loader.loaded_plugins
    assert loader.enabled_plugins[PLUGIN_NAME] is False


# -- boundary: the plugin never drives the kernel ------------------------------

# Symbols that would indicate a gate-write code path: writing gate
# decisions, calling into the gate, or deciding anything itself. None may
# appear in the plugin source -- the arrow is fixed: the kernel reads the
# plugin; the plugin never drives the kernel.
_GATE_WRITE_SYMBOLS = [
    "swarmgate",
    "gate.decision",
    "write_decision",
    "record_decision",
    "GateWriter",
    "gate_writer",
    "approve_decision",
    "deny_decision",
    "decide_gate",
    "gate_client",
    "from prismatic.gate",
    "import gate",
    "set_decision",
    "update_gate",
    "escalate(",
    "evaluate_gate",
]


def test_no_gate_write_symbols_in_plugin_source():
    src = PLUGIN_SRC.read_text(encoding="utf-8")
    found = [sym for sym in _GATE_WRITE_SYMBOLS if sym in src]
    assert not found, f"gate-write symbols present in plugin source: {found}"


def test_plugin_imports_nothing_but_the_plugin_interface():
    src = PLUGIN_SRC.read_text(encoding="utf-8")
    prismatic_imports = [
        line.strip()
        for line in src.splitlines()
        if re.match(r"\s*(from|import)\s+prismatic", line)
    ]
    assert prismatic_imports, "expected at least the interface import"
    for line in prismatic_imports:
        assert "prismatic.interface.plugin" in line, (
            f"plugin imports beyond the interface contract: {line!r}"
        )


def test_scope_guard_quoted_in_source():
    """The fixed arrow direction is stated in the module docstring and the
    capability contract -- grep-able evidence of the boundary."""
    src = PLUGIN_SRC.read_text(encoding="utf-8")
    assert "the kernel reads the plugin; the plugin never drives the kernel" in src


def test_plugin_decides_nothing_itself():
    """The plugin records approvals; the quorum math lives in the
    primitive and the DECISION lives with the gate evaluator consulting
    the status route. No method name may suggest the plugin decides."""
    decision_verbs = [
        "decide",
        "approve_proposal",
        "deny_proposal",
        "grant",
        "reject_proposal",
        "authorize",
    ]
    methods = [m for m in dir(ConsensusPlugin) if not m.startswith("__")]
    hits = [m for m in methods if any(v in m for v in decision_verbs)]
    assert not hits, f"plugin methods suggest decision-making: {hits}"
    src = PLUGIN_SRC.read_text(encoding="utf-8")
    assert "never approves or denies anything itself" in src
