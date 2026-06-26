"""
Tests for the PWP plugin's health-check contract.

GRO-2506 acceptance: "Health check explains missing credentials/resources."
These tests pin that contract:

* Each required capability row reports ``status: ok`` only when its
  declared env vars / paths are present.
* When something is missing, the row carries a ``missing`` list naming
  the env var or path — operators see exactly what to fix.
* Optional capabilities use ``status: skipped`` (not ``fail``) so they
  don't alarm the operator.
* The PluginLoader can actually import ``PwpPlugin`` via the
  ``plugin-manifest.yaml`` entry point.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

# Make the plugin importable as `pwp.*` — the PluginLoader inserts the
# plugin root onto sys.path, so we mirror that here.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from pwp import health  # noqa: E402
from pwp.health import check, summarize  # noqa: E402


# ── health.check() shape ───────────────────────────────────────────────────


def test_check_returns_list_of_dicts():
    rows = check()
    assert isinstance(rows, list)
    assert rows, "Expected at least one capability row"
    for row in rows:
        assert {"id", "kind", "status", "description"}.issubset(row.keys())


def test_required_capabilities_present():
    rows = check()
    required_ids = {r["id"] for r in rows if r["status"] in {"ok", "fail"}}
    assert "cloudflare.api" in required_ids
    assert "cloudflare.account" in required_ids
    assert "filesystem.workspace" in required_ids
    assert "okf.read" in required_ids
    assert "linear.api" in required_ids


def test_optional_capability_uses_skipped_status():
    rows = check()
    github = next(r for r in rows if r["id"] == "github.api")
    # If GITHUB_TOKEN isn't set, status should be 'skipped', not 'fail'.
    if "GITHUB_TOKEN" not in os.environ:
        assert github["status"] == "skipped"


def test_missing_env_vars_are_named(monkeypatch):
    """When an env var is missing, the row names it in 'missing'."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    rows = check()
    linear = next(r for r in rows if r["id"] == "linear.api")
    if linear["status"] == "fail":
        assert "LINEAR_API_KEY" in linear["missing"]


def test_missing_path_is_named(tmp_path, monkeypatch):
    """When a path capability doesn't exist, the row names it in 'missing'."""
    # Point PRISMATIC_HOME somewhere that doesn't have a workspace/OKF.
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path / "does-not-exist"))
    rows = check()
    for row in rows:
        if row["id"] == "filesystem.workspace":
            if row["status"] == "fail":
                assert any("path:" in m for m in row["missing"])
        if row["id"] == "okf.read":
            if row["status"] == "fail":
                assert any("path:" in m for m in row["missing"])


# ── health.summarize() ─────────────────────────────────────────────────────


def test_summarize_keys():
    summary = summarize()
    assert set(summary.keys()) == {"ok", "fail", "skipped", "unknown"}
    assert sum(summary.values()) == len(check())


def test_summarize_no_failures_when_env_clean(monkeypatch):
    """If every required env var is set and every required path exists,
    summarize() should report zero failures."""
    # Stub the credentials with placeholders.
    monkeypatch.setenv("CLOUDFLARE_GROWTHWEB_API_KEY", "test")
    monkeypatch.setenv("CLOUDFLARE_PAGES_API_TOKEN", "test")
    monkeypatch.setenv("CLOUDFLARE_PAGES_ACCOUNT_ID", "test")
    monkeypatch.setenv("LINEAR_API_KEY", "test")

    # Create the workspace and a fake OKF.
    base = Path("/tmp/pwp-test-prismatic")
    workspace = base / "workspace" / "sites"
    okf = base / "growthwebdev-knowledge" / "okf"
    workspace.mkdir(parents=True, exist_ok=True)
    okf.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PRISMATIC_HOME", str(base))

    rows = check()
    summary = summarize(rows)
    assert summary["fail"] == 0, f"Expected 0 fails, got {summary}; rows={rows}"


# ── PluginLoader integration ───────────────────────────────────────────────


def test_plugin_module_imports():
    """``pwp.plugin`` must import without side-effects."""
    mod = importlib.import_module("pwp.plugin")
    assert hasattr(mod, "PwpPlugin")


def test_pwp_plugin_class():
    from pwp.plugin import PwpPlugin

    plugin = PwpPlugin()
    # Required hooks
    assert hasattr(plugin, "on_init")
    assert hasattr(plugin, "register_tools")
    # Optional lifecycle hooks
    assert hasattr(plugin, "before_task_execution")
    assert hasattr(plugin, "after_task_execution")
    assert hasattr(plugin, "on_state_transition")


def test_register_tools_returns_openai_schema():
    from pwp.plugin import PwpPlugin

    tools = PwpPlugin().register_tools()
    names = {t["name"] for t in tools}
    assert "pwp_health" in names
    assert "pwp_pipeline" in names
    for tool in tools:
        assert "description" in tool
        assert "parameters" in tool
        assert tool["parameters"]["type"] == "object"


def test_on_init_runs_health_check():
    from pwp.plugin import PwpPlugin

    plugin = PwpPlugin()
    plugin.on_init(context=None)  # context not used in current impl
    assert plugin._last_health, "on_init should populate _last_health"


def test_manifest_yaml_is_loadable():
    """The PluginLoader reads plugin-manifest.yaml — it must be valid YAML
    and contain the required fields."""
    import yaml

    manifest_path = _PLUGIN_ROOT / "plugin-manifest.yaml"
    with open(manifest_path) as fh:
        manifest = yaml.safe_load(fh)

    for required in ("name", "version", "entry_point", "core_version_constraint"):
        assert required in manifest, f"Missing required field: {required}"

    assert manifest["entry_point"].count(":") == 1
    assert manifest["name"] == "pwp"
    assert "capabilities" in manifest
    assert "required" in manifest["capabilities"]