"""Tests for the prismatic-router capability plugin (plugins/router/).

Policy-based ingress classification: "request class -> capability", exposed
via capability_contract(). Classification is a pure function of
(rules, input): advisory only — it classifies, never dispatches.

The swarmrouter primitive is NEVER imported for real here (it is broken
against current main): every primitive-touching test injects a stub via
sys.modules.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugins" / "router"

sys.path.insert(0, str(REPO_ROOT / "plugins"))

from router.plugin import (  # noqa: E402
    PLUGIN_NAME,
    RouterPlugin,
    classify_request,
    plugin_state_dir,
    validate_rule,
)
from prismatic.interface.plugin import (  # noqa: E402
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)
from prismatic.core.registry import (  # noqa: E402
    PluginLoader,
    set_default_plugin_loader,
)


# ── stub primitive ─────────────────────────────────────────────────────


@pytest.fixture()
def stub_swarmrouter(monkeypatch):
    """Inject a stub swarmrouter into sys.modules (never the real one).

    The stub exposes the documented seam: swarmrouter.routing with
    capability_known(name) -> bool.
    """
    import router.plugin as router_plugin

    fake_pkg = types.ModuleType("swarmrouter")
    fake_routing = types.ModuleType("swarmrouter.routing")
    fake_routing.capability_known = lambda name: (
        name
        in {
            "prismatic-consensus",
            "prismatic-mesh",
        }
    )
    fake_pkg.routing = fake_routing

    monkeypatch.setitem(sys.modules, "swarmrouter", fake_pkg)
    monkeypatch.setitem(sys.modules, "swarmrouter.routing", fake_routing)
    monkeypatch.setattr(router_plugin, "_swarmrouter", None)
    yield fake_routing


# ── helpers ────────────────────────────────────────────────────────────


def make_context(tmp_home: Path, rules, **extra) -> PluginContext:
    os.environ["PRISMATIC_HOME"] = str(tmp_home)
    config = {"rules": rules}
    config.update(extra)
    return PluginContext(config=config, db_connection=None, state_dir=str(tmp_home))


def make_rule(request_class="code-review", capability="prismatic-consensus", **kw):
    r = {"request_class": request_class, "capability": capability}
    r.update(kw)
    return r


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    yield RouterPlugin()


@pytest.fixture(autouse=True)
def _reset_default_plugin_loader():
    yield
    set_default_plugin_loader(None)


# ── manifest ───────────────────────────────────────────────────────────


def test_manifest_parses_with_required_fields():
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["name"] == "prismatic-router"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "router.plugin:RouterPlugin"
    assert manifest["core_version_constraint"] == ">=0.2.0, <2.0.0"
    assert "swarmrouter>=0.1.0" in manifest["dependencies"]["pip"]
    assert set(manifest["tags"]) == {"infrastructure", "optional"}
    assert manifest["auto_enable"] is False
    assert manifest["config_schema"]["type"] == "object"
    paths = {(e["method"], e["path"]) for e in manifest["endpoints"]}
    assert ("GET", "/api/router/rules") in paths
    assert ("POST", "/api/router/classify") in paths


def test_entry_point_importable():
    module = importlib.import_module("router.plugin")
    cls = getattr(module, "RouterPlugin")
    assert issubclass(cls, PrismaticPlugin)


def test_no_top_level_swarmrouter_import():
    """Loader scans every plugin module: a top-level import would crash base installs."""
    source = (PLUGIN_DIR / "plugin.py").read_text()
    top_level_imports = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
        and "swarmrouter" in line
        and "TYPE_CHECKING" not in line
    ]
    assert top_level_imports == []


def test_config_schema_accepts_valid_rules_config():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    config = {
        "default_capability": "prismatic-mesh",
        "rules": [
            {
                "request_class": "code-review",
                "capability": "prismatic-consensus",
                "policy_notes": "Reviews need quorum before merge.",
                "priority": 10,
            },
            {"request_class": "nightly-backup", "capability": "prismatic-cron"},
        ],
    }
    jsonschema.validate(config, schema)  # must not raise


def test_config_schema_rejects_bad_rules():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    bad = {"rules": [{"request_class": "x"}]}  # capability missing
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


# ── lifecycle ──────────────────────────────────────────────────────────


def test_on_init_loads_valid_rules(plugin, tmp_path):
    rules = [
        make_rule(policy_notes="Needs quorum.", priority=5),
        make_rule(request_class="nightly-backup", capability="prismatic-cron"),
    ]
    plugin.on_init(make_context(tmp_path, rules, default_capability="prismatic-mesh"))
    table = plugin.rules_table()
    assert [r["request_class"] for r in table["rules"]] == [
        "code-review",
        "nightly-backup",
    ]
    assert table["rules"][0]["priority"] == 5
    assert table["rules"][0]["policy_notes"] == "Needs quorum."
    assert table["default_capability"] == "prismatic-mesh"


def test_on_init_rejects_invalid_rules(plugin, tmp_path):
    with pytest.raises(PluginValidationError):
        plugin.on_init(
            make_context(tmp_path, [{"request_class": "x"}])
        )  # no capability
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, [{"capability": "y"}]))  # no class
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, [make_rule(priority="high")]))
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, [make_rule(priority=True)]))
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, [make_rule(priority=-1)]))
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, "not-a-list"))
    with pytest.raises(PluginValidationError):
        plugin.on_init(make_context(tmp_path, [], default_capability="  "))


def test_on_init_accepts_loader_nested_plugin_configs(plugin, tmp_path, monkeypatch):
    """The loader nests config at context.config["plugin_configs"][name]."""
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    rules = [make_rule(request_class="loader-class")]
    ctx = PluginContext(
        config={"plugin_configs": {"prismatic-router": {"rules": rules}}},
        db_connection=None,
        state_dir=str(tmp_path),
    )
    plugin.on_init(ctx)
    assert [r["request_class"] for r in plugin.rules_table()["rules"]] == [
        "loader-class"
    ]


def test_suspend_resume_round_trips_rules(plugin, tmp_path, stub_swarmrouter):
    rules = [
        make_rule(policy_notes="Quorum path.", priority=7),
        make_rule(request_class="ingest", capability="prismatic-mesh"),
    ]
    plugin.on_init(make_context(tmp_path, rules, default_capability="prismatic-mesh"))

    snapshot = plugin.on_suspend()
    json.dumps(snapshot)  # JSON-serializable, no secrets
    assert {r["request_class"] for r in snapshot["rules"]} == {
        "code-review",
        "ingest",
    }
    assert snapshot["default_capability"] == "prismatic-mesh"
    assert "secret" not in json.dumps(snapshot).lower()

    resumed = RouterPlugin()
    resumed.on_resume(snapshot)
    assert resumed.rules_table() == plugin.rules_table()
    # classification still works after resume
    out = resumed.classify("code-review")
    assert out["capability"] == "prismatic-consensus"
    assert out["decision"] == "matched"


def test_on_resume_with_empty_state_keeps_config_rules(plugin, tmp_path):
    """First enable passes {} (no state file yet): config rules must survive."""
    plugin.on_init(make_context(tmp_path, [make_rule(request_class="keep-me")]))
    plugin.on_resume({})
    assert [r["request_class"] for r in plugin.rules_table()["rules"]] == ["keep-me"]


def test_on_resume_does_not_resurrect_rules_when_config_emptied(tmp_path, monkeypatch):
    """An explicitly emptied config stays empty even with a snapshot on hand."""
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    first = RouterPlugin()
    first.on_init(make_context(tmp_path, [make_rule(request_class="old")]))
    snapshot = first.on_suspend()

    cleared = RouterPlugin()
    cleared.on_init(make_context(tmp_path, []))  # explicit empty rules
    cleared.on_resume(snapshot)
    assert cleared.rules_table()["rules"] == []


# ── disabled by default + enable/disable via a real loader scan ────────


def _scan_loader(tmp_path, monkeypatch, config=None):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "home"))
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    link = plugins_dir / "router"
    if not link.exists():
        link.symlink_to(PLUGIN_DIR, target_is_directory=True)
    ctx = PluginContext(
        config=dict(config or {}), db_connection=None, state_dir=str(tmp_path)
    )
    loader = PluginLoader(core_version="0.2.0", plugins_dir=str(plugins_dir))
    loader.scan_and_load_plugins(ctx)
    return loader


def test_scan_registers_router_but_starts_disabled(tmp_path, monkeypatch):
    loader = _scan_loader(
        tmp_path,
        monkeypatch,
        config={"plugin_configs": {"prismatic-router": {"rules": []}}},
    )
    assert PLUGIN_NAME in loader.loaded_plugins
    status = loader.plugin_status(PLUGIN_NAME)
    assert status["enabled"] is False
    # core never imports it: no module-level swarmrouter import happened
    assert "swarmrouter" not in sys.modules


def test_disable_enable_preserves_rules(tmp_path, monkeypatch, stub_swarmrouter):
    rules = [make_rule(request_class="code-review")]
    loader = _scan_loader(
        tmp_path,
        monkeypatch,
        config={
            "plugin_configs": {
                "prismatic-router": {
                    "rules": rules,
                    "default_capability": "prismatic-mesh",
                }
            }
        },
    )
    loader.enable(PLUGIN_NAME)
    before = loader.loaded_plugins[PLUGIN_NAME].rules_table()
    assert [r["request_class"] for r in before["rules"]] == ["code-review"]

    loader.disable(PLUGIN_NAME)
    assert loader.plugin_status(PLUGIN_NAME)["enabled"] is False

    loader.enable(PLUGIN_NAME)
    after = loader.loaded_plugins[PLUGIN_NAME].rules_table()
    assert after == before


# ── routes ─────────────────────────────────────────────────────────────


def test_register_api_routes(plugin):
    routes = plugin.register_api_routes()
    by_path = {(r["method"], r["path"]): r for r in routes}
    assert ("GET", "/api/router/rules") in by_path
    assert ("POST", "/api/router/classify") in by_path
    assert by_path[("GET", "/api/router/rules")]["handler"] == (
        "router.plugin:RouterPlugin.rules_table"
    )
    assert by_path[("POST", "/api/router/classify")]["handler"] == (
        "router.plugin:RouterPlugin.classify_endpoint"
    )


def test_classify_matched_rule(plugin, tmp_path, stub_swarmrouter):
    plugin.on_init(
        make_context(
            tmp_path,
            [
                make_rule(policy_notes="Needs quorum.", priority=5),
                make_rule(
                    request_class="code-review",
                    capability="other-cap",
                    priority=1,
                ),
            ],
        )
    )
    out = plugin.classify_endpoint({"request_class": "code-review"})
    assert out["advisory"] is True
    assert out["decision"] == "matched"
    assert out["capability"] == "prismatic-consensus"  # highest priority wins
    assert out["matched_rule"]["priority"] == 5
    assert out["policy_notes"] == "Needs quorum."
    assert out["capability_known"] is True  # stub taxonomy annotation
    assert out["primitive"] == "swarmrouter"


def test_classify_no_match_without_default(plugin, tmp_path, stub_swarmrouter):
    plugin.on_init(make_context(tmp_path, [make_rule()]))
    out = plugin.classify_endpoint({"request_class": "something-else"})
    assert out["decision"] == "no-match"
    assert out["capability"] is None
    assert out["matched_rule"] is None
    assert out["advisory"] is True


def test_classify_no_match_uses_default_capability(plugin, tmp_path, stub_swarmrouter):
    plugin.on_init(
        make_context(tmp_path, [make_rule()], default_capability="prismatic-mesh")
    )
    out = plugin.classify_endpoint({"request_class": "something-else"})
    assert out["decision"] == "default"
    assert out["capability"] == "prismatic-mesh"
    assert out["capability_known"] is True


def test_classify_endpoint_error_paths(plugin, tmp_path, stub_swarmrouter):
    plugin.on_init(make_context(tmp_path, [make_rule()]))
    assert plugin.classify_endpoint("nope")["error"] == "invalid payload"
    assert plugin.classify_endpoint({})["error"] == "invalid payload"
    assert plugin.classify_endpoint({"request_class": "  "})["error"] == (
        "invalid payload"
    )
    assert plugin.classify_endpoint({"request_class": 42})["error"] == "invalid payload"


def test_classify_request_pure_function_needs_no_primitive(monkeypatch):
    """The mapping itself is a pure function of (rules, input)."""
    import router.plugin as router_plugin

    monkeypatch.setattr(router_plugin, "_swarmrouter", None)
    assert "swarmrouter" not in sys.modules

    rules = [validate_rule(make_rule())]
    before = copy.deepcopy(rules)
    out = classify_request(rules, "code-review")
    assert out["decision"] == "matched"
    assert out["capability"] == "prismatic-consensus"
    assert rules == before  # input untouched


# ── missing primitive degradation ──────────────────────────────────────


def test_missing_primitive_raises_clear_runtime_error(
    plugin, tmp_path, monkeypatch, stub_swarmrouter
):
    """With the primitive absent, use-time calls raise RuntimeError — but the
    loader scan that loaded the module is unaffected."""
    import router.plugin as router_plugin

    plugin.on_init(make_context(tmp_path, [make_rule()]))
    monkeypatch.delitem(sys.modules, "swarmrouter")
    monkeypatch.delitem(sys.modules, "swarmrouter.routing")
    monkeypatch.setattr(router_plugin, "_swarmrouter", None)

    with pytest.raises(RuntimeError, match="prismatic-router requires"):
        plugin.classify("code-review")

    # the route degrades to an error payload, never a 500 traceback
    out = plugin.classify_endpoint({"request_class": "code-review"})
    assert out["error"] == "primitive not installed"
    assert "requires" in out["detail"]


def test_broken_primitive_raises_clear_runtime_error(
    plugin, tmp_path, monkeypatch, stub_swarmrouter
):
    """A primitive whose import blows up (version skew) degrades the same way."""
    import router.plugin as router_plugin

    plugin.on_init(make_context(tmp_path, [make_rule()]))

    monkeypatch.delitem(sys.modules, "swarmrouter")
    monkeypatch.delitem(sys.modules, "swarmrouter.routing")
    monkeypatch.setattr(router_plugin, "_swarmrouter", None)

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "swarmrouter" or name.startswith("swarmrouter."):
            raise ImportError("simulated skew: dispatcher imports removed private")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(RuntimeError, match="prismatic-router requires"):
        plugin.classify("code-review")


# ── boundary: it classifies, never dispatches ──────────────────────────


BANNED_CAPABILITY_WORDS = {
    "dispatch",
    "execute",
    "enqueue",
    "forward",
    "send_request",
    "route_request",
}


def test_classify_has_zero_side_effects(plugin, tmp_path, stub_swarmrouter):
    """Call classify twice: identical results, plugin state byte-identical."""
    plugin.on_init(
        make_context(tmp_path, [make_rule()], default_capability="prismatic-mesh")
    )
    before_rules = copy.deepcopy(plugin._rules)
    before_table = copy.deepcopy(plugin.rules_table())
    before_snapshot = plugin.on_suspend()

    first = plugin.classify_endpoint({"request_class": "code-review"})
    second = plugin.classify_endpoint({"request_class": "code-review"})

    assert first == second
    assert plugin._rules == before_rules
    assert plugin.rules_table() == before_table
    assert plugin.on_suspend()["rules"] == before_snapshot["rules"]
    # no-match path is equally side-effect free
    plugin.classify_endpoint({"request_class": "unknown-class"})
    assert plugin._rules == before_rules


def test_no_dispatch_execute_or_enqueue_anywhere(plugin):
    """No method or route on the plugin may dispatch, execute, or enqueue."""
    for name in BANNED_CAPABILITY_WORDS:
        assert not hasattr(plugin, name), f"RouterPlugin must not define {name!r}"

    # Source scan: flag real dispatch-family *code* (definitions / calls),
    # ignoring the docstrings that explicitly deny such paths exist.
    source = (PLUGIN_DIR / "plugin.py").read_text()
    code_hits = []
    for lineno, line in enumerate(source.splitlines(), 1):
        low = line.lower()
        if re.search(
            r"\bdef\s+(dispatch|enqueue|execute)\b"
            r"|\.(dispatch|enqueue|execute)\s*\("
            r"|\b(dispatch|enqueue)\s*\(",
            low,
        ):
            if any(neg in low for neg in ("never", "no ", "not ", "n't", "without")):
                continue
            code_hits.append((lineno, line.strip()))
    assert code_hits == [], f"found dispatch-family code: {code_hits}"

    routes = plugin.register_api_routes()
    assert len(routes) == 2
    for route in routes:
        assert route["method"] in ("GET", "POST")
        assert "dispatch" not in route["path"]
        assert "execute" not in route["path"]


def test_capability_contract_is_advisory_only(plugin, tmp_path, stub_swarmrouter):
    plugin.on_init(make_context(tmp_path, [make_rule()]))
    contract = plugin.capability_contract()
    assert contract["capability"] == "policy-based-ingress-classification"
    assert contract["plugin"] == PLUGIN_NAME
    assert contract["tools"] == []
    notes = " ".join(contract["notes"]).lower()
    assert "never dispatches" in notes
    json.dumps(contract)  # serializable


def test_register_tools_returns_empty(plugin):
    assert plugin.register_tools() == []


# ── validate_rule unit checks ──────────────────────────────────────────


def test_plugin_state_dir_under_prismatic_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    assert plugin_state_dir() == tmp_path / "plugin-state" / PLUGIN_NAME


def test_validate_rule_canonical_form():
    canonical = validate_rule(make_rule(policy_notes="n", priority=3))
    assert canonical == {
        "request_class": "code-review",
        "capability": "prismatic-consensus",
        "policy_notes": "n",
        "priority": 3,
    }
    assert validate_rule(make_rule())["policy_notes"] is None
    assert validate_rule(make_rule())["priority"] == 0


def test_validate_rule_rejects_bad_input():
    with pytest.raises(PluginValidationError):
        validate_rule("nope")
    with pytest.raises(PluginValidationError):
        validate_rule({"request_class": "", "capability": "x"})
    with pytest.raises(PluginValidationError):
        validate_rule({"request_class": "x", "capability": ""})
    with pytest.raises(PluginValidationError):
        validate_rule(make_rule(policy_notes=42))
    with pytest.raises(PluginValidationError):
        validate_rule(make_rule(priority=1.5))
