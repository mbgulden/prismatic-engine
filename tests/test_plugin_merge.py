"""Tests for the prismatic-merge capability plugin.

Written against the generic plugin lifecycle contract:
  PrismaticPlugin.on_init / on_suspend() -> dict / on_resume(state: dict),
  PluginLoader.enable/disable/unload(name), plugin state persisted at
  $PRISMATIC_HOME/plugin-state/<name>/state.json, manifest config_schema
  validated at attach, auto_enable: false.

The swarmmerge primitive is NOT installed on the box and NOT on PyPI.
Tests stub it via sys.modules injection — the real primitive is never
imported. Apply is stateless: the same strategy + inputs always produce the
same output, and the plugin holds no kernel state.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "prismatic" / "shipped_plugins" / "merge"

sys.path.insert(0, str(REPO_ROOT / "prismatic" / "shipped_plugins"))

import merge.plugin as merge_plugin  # noqa: E402
from merge.plugin import (  # noqa: E402
    PLUGIN_NAME,
    MergePlugin,
    MergeRequestError,
    plugin_state_dir,
    validate_strategy,
)
from prismatic.interface.plugin import (  # noqa: E402
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)


def _make_stub_swarmmerge() -> types.ModuleType:
    """Build a deterministic fake swarmmerge primitive module."""
    mod = types.ModuleType("swarmmerge")
    strategies = types.ModuleType("swarmmerge.strategies")

    def last_writer_wins(inputs):
        merged = {}
        for doc in inputs["documents"]:
            merged.update(doc)
        return {"merged": merged, "count": len(inputs["documents"])}

    def concat_lists(inputs):
        out = []
        for lst in inputs["lists"]:
            out.extend(lst)
        return {"merged": out}

    strategies.last_writer_wins = last_writer_wins
    strategies.concat_lists = concat_lists
    mod.strategies = strategies
    return mod


@pytest.fixture()
def stub_swarmmerge(monkeypatch):
    """Inject a deterministic fake swarmmerge via sys.modules.

    The real primitive is not installed and not on PyPI — it is never
    imported. The plugin's lazy-import cache is reset so each test starts
    clean; monkeypatch undoes everything afterwards.
    """
    mod = _make_stub_swarmmerge()
    monkeypatch.setitem(sys.modules, "swarmmerge", mod)
    monkeypatch.setitem(sys.modules, "swarmmerge.strategies", mod.strategies)
    monkeypatch.setattr(merge_plugin, "_swarmmerge", None)
    return mod


@pytest.fixture()
def no_swarmmerge(monkeypatch):
    """Guarantee the primitive is absent: clear the lazy cache and drop any
    stub from sys.modules, so _swarmmerge_or_raise() must fail."""
    monkeypatch.setattr(merge_plugin, "_swarmmerge", None)
    monkeypatch.delitem(sys.modules, "swarmmerge", raising=False)
    monkeypatch.delitem(sys.modules, "swarmmerge.strategies", raising=False)


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    return MergePlugin()


def make_context(tmp_home: Path, strategies, **extra) -> PluginContext:
    os.environ["PRISMATIC_HOME"] = str(tmp_home)
    config = {"strategies": strategies}
    config.update(extra)
    return PluginContext(config=config, db_connection=None, state_dir=str(tmp_home))


def strategy_cfg(
    name="last-writer-wins",
    handler="strategies.last_writer_wins",
    description="Later documents overwrite earlier keys.",
):
    return {"name": name, "description": description, "handler": handler}


# ── manifest ─────────────────────────────────────────────────────────────


def test_manifest_parses_with_required_fields():
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["name"] == "prismatic-merge"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "merge.plugin:MergePlugin"
    assert manifest["core_version_constraint"] == ">=0.2.0, <2.0.0"
    assert "swarmmerge>=0.1.0" in manifest["dependencies"]["pip"]
    assert set(manifest["tags"]) == {"infrastructure", "optional"}
    assert manifest["auto_enable"] is False
    assert manifest["config_schema"]["type"] == "object"
    paths = {(e["method"], e["path"]) for e in manifest["endpoints"]}
    assert ("GET", "/api/merge/strategies") in paths
    assert ("POST", "/api/merge/apply") in paths


def test_manifest_validates_against_core_validator():
    from prismatic.plugin_architecture import validate_manifest_payload

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    result = validate_manifest_payload(manifest, PLUGIN_DIR / "plugin-manifest.yaml")
    assert result["errors"] == []


def test_entry_point_importable():
    module = importlib.import_module("merge.plugin")
    cls = getattr(module, "MergePlugin")
    assert issubclass(cls, PrismaticPlugin)


def test_config_schema_accepts_valid_strategies_config():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    config = {
        "strategies": [
            {
                "name": "last-writer-wins",
                "description": "Later documents overwrite earlier keys.",
                "handler": "strategies.last_writer_wins",
            },
            {
                "name": "concat-lists",
                "description": "Concatenate input lists in order.",
                "handler": "strategies.concat_lists",
            },
        ]
    }
    jsonschema.validate(config, schema)  # must not raise
    jsonschema.validate({}, schema)  # strategies optional


def test_config_schema_rejects_bad_strategy():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    bad = {
        "strategies": [{"name": "Bad Name!", "description": "x"}]
    }  # no handler, bad name
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


# ── lifecycle ────────────────────────────────────────────────────────────


def test_on_init_valid_config(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    listed = plugin.list_strategies()
    assert listed == [
        {
            "name": "last-writer-wins",
            "description": "Later documents overwrite earlier keys.",
            "handler": "strategies.last_writer_wins",
        }
    ]


def test_on_init_accepts_loader_nested_plugin_configs(plugin, tmp_path, monkeypatch):
    """The generic PluginLoader nests the validated plugin config at
    context.config["plugin_configs"]["prismatic-merge"]; on_init must read
    strategies from there, not only from a top-level config."""
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    ctx = PluginContext(
        config={
            "plugin_configs": {
                "prismatic-merge": {"strategies": [strategy_cfg(name="nested")]}
            }
        },
        db_connection=None,
        state_dir=str(tmp_path),
    )
    plugin.on_init(ctx)
    assert [s["name"] for s in plugin.list_strategies()] == ["nested"]


def test_on_init_rejects_duplicate_strategy_names(plugin, tmp_path):
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, [strategy_cfg(), strategy_cfg()]))


def test_on_init_rejects_non_list_strategies(plugin, tmp_path):
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, {"not": "a list"}))


@pytest.mark.parametrize(
    "bad",
    [
        {
            "name": "Bad Name",
            "description": "d",
            "handler": "strategies.x",
        },  # not a slug
        {"name": "", "description": "d", "handler": "strategies.x"},
        {
            "name": "ok-name",
            "description": "",
            "handler": "strategies.x",
        },  # empty description
        {"name": "ok-name", "description": "d", "handler": ""},  # empty handler
        {"name": "ok-name", "handler": "strategies.x"},  # missing description
        "not-a-dict",
    ],
)
def test_validate_strategy_rejects_bad_definitions(bad):
    with pytest.raises(PluginValidationError):
        validate_strategy(bad)


def test_suspend_resume_round_trip(plugin, tmp_path):
    cfgs = [
        strategy_cfg(),
        strategy_cfg(
            name="concat-lists",
            handler="strategies.concat_lists",
            description="Concatenate input lists in order.",
        ),
    ]
    plugin.on_init(make_context(tmp_path, cfgs))

    snapshot = plugin.on_suspend()
    json.dumps(snapshot)  # JSON-serializable
    blob = json.dumps(snapshot).lower()
    for secret_word in ("password", "secret", "token", "api_key"):
        assert secret_word not in blob  # no secrets in suspend state
    assert {s["name"] for s in snapshot["strategies"]} == {
        "last-writer-wins",
        "concat-lists",
    }
    assert (plugin_state_dir() / "state.json").exists()  # belt-and-braces file

    resumed = MergePlugin()
    resumed.on_resume(snapshot)
    assert [s["name"] for s in resumed.list_strategies()] == [
        "last-writer-wins",
        "concat-lists",
    ]


def test_on_resume_with_empty_state_keeps_config_strategies(plugin, tmp_path):
    """First enable passes {} (no state file yet): the strategies on_init
    loaded from config must survive — on_resume must not wipe them."""
    plugin.on_init(make_context(tmp_path, [strategy_cfg(name="keep-me")]))
    plugin.on_resume({})
    assert [s["name"] for s in plugin.list_strategies()] == ["keep-me"]


def test_on_resume_does_not_resurrect_strategies_when_config_emptied(plugin, tmp_path):
    """An explicitly emptied config stays empty even when a suspend snapshot
    holds old definitions."""
    plugin.on_init(make_context(tmp_path, [strategy_cfg(name="old")]))
    snapshot = plugin.on_suspend()

    cleared = MergePlugin()
    cleared.on_init(make_context(tmp_path, []))  # explicit empty strategies
    cleared.on_resume(snapshot)
    assert cleared.list_strategies() == []


def test_disabled_by_default_fresh_loader_scan(tmp_path, monkeypatch, no_swarmmerge):
    """Fresh loader scan registers the plugin DISABLED, and the scan never
    touches the (absent) swarmmerge primitive."""
    import os as _os

    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _os.symlink(PLUGIN_DIR, plugins_dir / "merge")

    from prismatic.core.registry import PluginLoader

    loader = PluginLoader(core_version="0.2.0", plugins_dir=str(plugins_dir))
    ctx = PluginContext(config={}, db_connection=None, state_dir=str(tmp_path))
    loader.scan_and_load_plugins(ctx)  # must not raise without swarmmerge

    assert "prismatic-merge" in loader.loaded_plugins
    status = loader.plugin_status("prismatic-merge")
    assert status["loaded"] is True
    assert status["enabled"] is False
    inst = loader.loaded_plugins["prismatic-merge"]
    assert {r["path"] for r in inst.register_api_routes()} == {
        "/api/merge/strategies",
        "/api/merge/apply",
    }


# ── routes ───────────────────────────────────────────────────────────────


def test_register_api_routes(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    routes = plugin.register_api_routes()
    by_path = {r["path"]: r for r in routes}
    assert by_path["/api/merge/strategies"]["method"] == "GET"
    assert (
        by_path["/api/merge/strategies"]["handler"]
        == "merge.plugin:MergePlugin.list_strategies"
    )
    assert by_path["/api/merge/apply"]["method"] == "POST"
    assert (
        by_path["/api/merge/apply"]["handler"] == "merge.plugin:MergePlugin.apply_merge"
    )


def test_strategies_list_payload(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    payload = plugin.list_strategies()
    assert payload == [
        {
            "name": "last-writer-wins",
            "description": "Later documents overwrite earlier keys.",
            "handler": "strategies.last_writer_wins",
        }
    ]


def test_register_tools_returns_empty():
    assert MergePlugin().register_tools() == []


# ── apply ────────────────────────────────────────────────────────────────


def test_apply_happy_path(plugin, tmp_path, stub_swarmmerge):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    out = plugin.apply_merge(
        "last-writer-wins", {"documents": [{"a": 1, "b": 2}, {"b": 3, "c": 4}]}
    )
    assert out["strategy"] == "last-writer-wins"
    assert out["result"] == {"merged": {"a": 1, "b": 3, "c": 4}, "count": 2}
    assert out["applied_at"]  # utc iso timestamp present


def test_apply_second_strategy(plugin, tmp_path, stub_swarmmerge):
    plugin.on_init(
        make_context(
            tmp_path,
            [
                strategy_cfg(),
                strategy_cfg(
                    name="concat-lists",
                    handler="strategies.concat_lists",
                    description="Concatenate input lists in order.",
                ),
            ],
        )
    )
    out = plugin.apply_merge("concat-lists", {"lists": [[1, 2], [3]]})
    assert out["result"] == {"merged": [1, 2, 3]}


def test_apply_unknown_strategy_errors(plugin, tmp_path, stub_swarmmerge):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    with pytest.raises(MergeRequestError, match="unknown merge strategy"):
        plugin.apply_merge("nope", {"documents": []})


def test_apply_unknown_strategy_names_known_ones(plugin, tmp_path, stub_swarmmerge):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    with pytest.raises(MergeRequestError) as excinfo:
        plugin.apply_merge("nope", {})
    assert "last-writer-wins" in str(excinfo.value)


def test_apply_rejects_malformed_inputs(plugin, tmp_path, stub_swarmmerge):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    with pytest.raises(MergeRequestError):  # inputs must be a dict
        plugin.apply_merge("last-writer-wins", ["not", "a", "dict"])
    with pytest.raises(MergeRequestError):  # inputs must be JSON-serializable
        plugin.apply_merge("last-writer-wins", {"x": object()})
    with pytest.raises(MergeRequestError):  # strategy must be a non-empty string
        plugin.apply_merge("", {"documents": []})


def test_apply_rejects_handler_escaping_primitive_namespace(
    plugin, tmp_path, stub_swarmmerge
):
    plugin.on_init(
        make_context(
            tmp_path,
            [
                strategy_cfg(name="evil-private", handler="strategies._secret"),
                strategy_cfg(name="evil-missing", handler="nope.not_here"),
            ],
        )
    )
    with pytest.raises(MergeRequestError, match="invalid path segment"):
        plugin.apply_merge("evil-private", {})
    with pytest.raises(MergeRequestError, match="not found in swarmmerge"):
        plugin.apply_merge("evil-missing", {})


def test_apply_without_primitive_raises_clear_error(plugin, tmp_path, no_swarmmerge):
    """Missing primitive -> clear RuntimeError at use time (never a bare
    traceback); the registry itself still works."""
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    with pytest.raises(RuntimeError, match="swarmmerge"):
        plugin.apply_merge("last-writer-wins", {"documents": []})
    assert plugin.list_strategies()[0]["name"] == "last-writer-wins"


def test_apply_strategy_exception_is_audited_and_propagates(
    plugin, tmp_path, stub_swarmmerge, monkeypatch
):
    def boom(inputs):
        raise ValueError("strategy blew up")

    monkeypatch.setattr(stub_swarmmerge.strategies, "boom", boom, raising=False)
    plugin.on_init(
        make_context(tmp_path, [strategy_cfg(name="boom", handler="strategies.boom")])
    )
    with pytest.raises(ValueError, match="strategy blew up"):
        plugin.apply_merge("boom", {})
    events = plugin.recent_audit_events()
    assert events[-1]["event"] == "merge_failed"
    assert "strategy blew up" in events[-1]["error"]


# ── audit ────────────────────────────────────────────────────────────────


def test_apply_emits_one_audit_event_per_call(plugin, tmp_path, stub_swarmmerge):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    plugin.apply_merge("last-writer-wins", {"documents": [{"a": 1}]})
    plugin.apply_merge("last-writer-wins", {"documents": [{"b": 2}]})
    with pytest.raises(MergeRequestError):
        plugin.apply_merge("nope", {})

    ring = plugin.recent_audit_events()
    assert [e["event"] for e in ring] == [
        "merge_applied",
        "merge_applied",
        "merge_failed",
    ]
    assert all(e["plugin"] == PLUGIN_NAME for e in ring)
    assert all(len(e["inputs_digest"]) == 64 for e in ring)
    assert ring[0]["ok"] is True and ring[-1]["ok"] is False

    lines = (plugin_state_dir() / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["event"] == "merge_applied"
    assert first["strategy"] == "last-writer-wins"


# ── statelessness ────────────────────────────────────────────────────────


def test_apply_is_stateless_same_inputs_identical_output(
    plugin, tmp_path, stub_swarmmerge
):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    inputs = {"documents": [{"a": 1, "b": 2}, {"b": 3, "c": 4}]}
    first = plugin.apply_merge("last-writer-wins", inputs)
    second = plugin.apply_merge("last-writer-wins", inputs)
    assert first["result"] == second["result"]
    assert first["strategy"] == second["strategy"]
    # apply calls do not mutate the registry
    assert [s["name"] for s in plugin.list_strategies()] == ["last-writer-wins"]


def test_apply_writes_nothing_outside_plugin_state_dir(
    plugin, tmp_path, stub_swarmmerge
):
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    plugin.apply_merge("last-writer-wins", {"documents": [{"a": 1}]})
    plugin.on_suspend()
    allowed = plugin_state_dir()
    new_files = {p for p in tmp_path.rglob("*") if p.is_file()} - before
    assert new_files, "expected the plugin to write its audit/state files"
    for path in new_files:
        assert allowed in path.parents, f"file written outside state dir: {path}"


# ── boundary ─────────────────────────────────────────────────────────────


def test_boundary_no_kernel_imports():
    """The plugin must never import kernel transaction/lock/ledger paths —
    only the plugin interface."""
    import re as _re

    src = (PLUGIN_DIR / "plugin.py").read_text()
    # Module-docstring prose may name the paths the plugin promises NOT to
    # touch; strip it so the marker check only sees executable code.
    code = _re.sub(r'''"""[\s\S]*?"""''', "", src, count=1)
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert (
                "prismatic" not in stripped or "prismatic.interface.plugin" in stripped
            ), f"kernel import in plugin source: {line}"
    for marker in ("swarmlock", "ledger", "swarmgate", "lock_manager", "transaction"):
        assert marker not in code.lower(), f"kernel marker {marker!r} in plugin source"


def test_boundary_merge_never_kernel_driven():
    """Merge is guest-callable only: the plugin defines no kernel-driven
    hooks that could merge on the kernel's behalf."""
    src = (PLUGIN_DIR / "plugin.py").read_text()
    for hook in (
        "def before_task_execution",
        "def after_task_execution",
        "def on_issue_dispatch",
        "def on_state_transition",
        "def on_pipeline_stage",
        "def on_review_complete",
    ):
        assert hook not in src, f"kernel-driven hook {hook} in plugin source"


def test_boundary_no_sandboxed_language():
    blob = (
        (PLUGIN_DIR / "plugin.py").read_text()
        + (PLUGIN_DIR / "README.md").read_text()
        + (PLUGIN_DIR / "plugin-manifest.yaml").read_text()
    ).lower()
    assert "sandbox" not in blob


def test_capability_contract_shape(plugin, tmp_path):
    plugin.on_init(make_context(tmp_path, [strategy_cfg()]))
    contract = plugin.capability_contract()
    assert contract["capability"] == "merge-strategy-registry"
    assert contract["plugin"] == "prismatic-merge"
    assert contract["tools"] == []
    assert len(contract["strategies"]) == 1
    assert "never driven by the kernel" in contract["description"]
