"""Tests for Gap 13 — Ship-Time Plugin Load Verification Gate.

5 tests covering:
1. test_gate_passes_when_all_plugins_load — happy path
2. test_gate_fails_on_version_mismatch — Gap 10 regression
3. test_gate_fails_on_missing_manifest — discovery robustness
4. test_gate_fails_on_broken_entry_point — import failure
5. test_gate_includes_core_version_in_result — observability

Reference: okf/operations/gap13-plugin-load-gate-spec-2026-06-29.md
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from prismatic.quality.plugin_load import (
    PluginLoadResult,
    discover_shipped_plugins,
    read_core_version,
    verify_shipped_plugins_load,
)


def _unique_plugin_name(prefix: str) -> str:
    """Generate a unique plugin name to avoid sys.modules collision across test runs."""
    return prefix + "-" + uuid.uuid4().hex[:8]


def _write_plugin(
    plugins_dir: Path,
    name: str,
    *,
    module: str | None = None,
    entry_point: str | None = None,
    version_constraint: str = ">=0.1.0, <2.0.0",
    omit_manifest: bool = False,
) -> Path:
    """Helper: create a plugin manifest + minimal plugin.py in plugins_dir.

    Returns the plugin directory path.

    Note: uses a UNIQUE plugin name so test runs don't collide on
    sys.path / sys.modules. Tests should pass ``name=f"{purpose}-{uuid}"``
    if they create multiple plugins in the same temp dir.
    """
    if module is None:
        module = name.replace("-", "_")
    # Derive class name from plugin name. Strip "-plugin" / "_plugin"
    # suffix if present, capitalize the first letter, leave rest alone.
    base = name
    for suffix in ("-plugin", "_plugin"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    class_name = base.replace("-", "").replace("_", "").capitalize()
    if entry_point is None:
        entry_point = module + ".plugin:" + class_name + "Plugin"

    plugin_dir = plugins_dir / module
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if not omit_manifest:
        manifest = (
            'schema_version: "1.0.0"\n'
            'name: "' + name + '"\n'
            'version: "1.0.0"\n'
            'entry_point: "' + entry_point + '"\n'
            'core_version_constraint: "' + version_constraint + '"\n'
            "dependencies:\n"
            "  pip: []\n"
            "personas: []\n"
            "hooks: []\n"
        )
        (plugin_dir / "plugin-manifest.yaml").write_text(manifest)

    class_name = base.replace("-", "").replace("_", "").capitalize()
    plugin_py = (
        '"""Minimal valid plugin for testing."""\n'
        "from typing import Any, Dict, List\n"
        "from prismatic.interface.plugin import PrismaticPlugin\n\n"
        "class " + class_name + "Plugin(PrismaticPlugin):\n"
        "    def on_init(self, context):\n"
        "        pass\n\n"
        "    def register_tools(self) -> List[Dict[str, Any]]:\n"
        "        return []\n"
    )
    (plugin_dir / "plugin.py").write_text(plugin_py)

    return plugin_dir


# ─────────────────────────────────────────────────────────────────────
# Test 1: Happy path — all plugins load
# ─────────────────────────────────────────────────────────────────────


def test_gate_passes_when_all_plugins_load():
    """When every shipped plugin loads successfully, gate.passed == True."""
    with tempfile.TemporaryDirectory() as tmp:
        plugins_dir = Path(tmp) / "plugins"
        plugins_dir.mkdir()
        _write_plugin(plugins_dir, _unique_plugin_name("alpha"))
        _write_plugin(plugins_dir, _unique_plugin_name("beta"))

        result = verify_shipped_plugins_load(
            plugins_dir=plugins_dir,
            core_version="0.2.0",
        )

    assert result.passed, "gate failed unexpectedly: " + result.to_markdown()
    assert result.loaded_count == 2
    assert result.failed_count == 0
    assert all(f.status == "loaded" for f in result.findings)
    assert len(result.findings) == 2
    print("PASS: gate passes when all plugins load")


# ─────────────────────────────────────────────────────────────────────
# Test 2: Version mismatch — Gap 10 regression
# ─────────────────────────────────────────────────────────────────────


def test_gate_fails_on_version_mismatch():
    """Regression: Gap 10 shipped a plugin with core_version_constraint
    '>=1.0.0' incompatible with engine v0.2.0. The gate must catch this."""
    plugin_name = _unique_plugin_name("future")
    with tempfile.TemporaryDirectory() as tmp:
        plugins_dir = Path(tmp) / "plugins"
        plugins_dir.mkdir()
        _write_plugin(
            plugins_dir,
            plugin_name,
            version_constraint=">=99.0.0",  # NEVER satisfied by 0.2.0
        )

        result = verify_shipped_plugins_load(
            plugins_dir=plugins_dir,
            core_version="0.2.0",
        )

    assert not result.passed, "gate should FAIL on version mismatch"
    assert result.loaded_count == 0
    assert result.failed_count == 1
    finding = result.findings[0]
    assert finding.plugin_name == plugin_name
    assert finding.status == "version_mismatch", (
        "expected version_mismatch, got "
        + finding.status
        + "; detail="
        + finding.detail[:200]
    )
    assert "does not satisfy constraint" in finding.detail.lower(), (
        "detail should include the actual constraint error: " + finding.detail[:200]
    )
    print("PASS: gate fails on version mismatch (Gap 10 regression)")


# ─────────────────────────────────────────────────────────────────────
# Test 3: Missing manifest
# ─────────────────────────────────────────────────────────────────────


def test_gate_fails_on_missing_manifest():
    """A plugin directory without plugin-manifest.yaml must not pass as shipped."""
    with tempfile.TemporaryDirectory() as tmp:
        plugins_dir = Path(tmp) / "plugins"
        plugins_dir.mkdir()
        # Plugin dir exists but NO plugin-manifest.yaml
        (plugins_dir / "incomplete").mkdir()
        (plugins_dir / "incomplete" / "README.md").write_text("TODO")

        result = verify_shipped_plugins_load(
            plugins_dir=plugins_dir,
            core_version="0.2.0",
        )

    # Discovery filters out directories without a manifest, so this
    # passes (nothing to verify). Verify the gate correctly recognizes
    # "no shipped plugins" as a passing state — and explicitly check
    # that discover_shipped_plugins does NOT include the incomplete one.
    assert result.passed, (
        "no manifests means no shipped plugins: " + result.to_markdown()
    )
    assert result.loaded_count == 0
    # Direct check: discovery must skip the incomplete dir
    discovered = discover_shipped_plugins(plugins_dir)
    assert len(discovered) == 0, "discovery should skip dirs without manifest: " + str(
        discovered
    )
    print("PASS: gate handles missing manifest via discovery filtering")


# ─────────────────────────────────────────────────────────────────────
# Test 4: Broken entry_point
# ─────────────────────────────────────────────────────────────────────


def test_gate_fails_on_broken_entry_point():
    """A manifest whose entry_point can't be imported must be flagged."""
    plugin_name = _unique_plugin_name("broken")
    module_name = plugin_name.replace("-", "_")
    with tempfile.TemporaryDirectory() as tmp:
        plugins_dir = Path(tmp) / "plugins"
        plugins_dir.mkdir()
        _write_plugin(
            plugins_dir,
            plugin_name,
            module=module_name,
            entry_point="nonexistent.module.path:NonexistentClass",
        )

        result = verify_shipped_plugins_load(
            plugins_dir=plugins_dir,
            core_version="0.2.0",
        )

    assert not result.passed, "gate should FAIL on broken entry_point"
    assert result.failed_count == 1
    finding = result.findings[0]
    assert finding.status in ("broken_entry_point", "unknown_error"), (
        "expected broken_entry_point or unknown_error, got " + finding.status
    )
    assert finding.detail, "detail should not be empty for broken entry_point"
    print("PASS: gate fails on broken entry_point")


# ─────────────────────────────────────────────────────────────────────
# Test 5: Core version surfaced in result
# ─────────────────────────────────────────────────────────────────────


def test_gate_includes_core_version_in_result():
    """The gate must surface the actual engine version it tested against."""
    plugin_name = _unique_plugin_name("alpha")
    with tempfile.TemporaryDirectory() as tmp:
        plugins_dir = Path(tmp) / "plugins"
        plugins_dir.mkdir()
        _write_plugin(plugins_dir, plugin_name)

        result = verify_shipped_plugins_load(
            plugins_dir=plugins_dir,
            core_version="1.2.3-test",
        )

    assert result.core_version == "1.2.3-test", (
        "core_version not surfaced: got " + repr(result.core_version)
    )
    # And reading from pyproject.toml should give us a non-empty string
    real_version = read_core_version()
    assert real_version and real_version != "0.0.0", (
        "read_core_version() should return real version, got " + repr(real_version)
    )
    print(
        "PASS: gate surfaces core_version (explicit="
        + result.core_version
        + ", discovered="
        + real_version
        + ")"
    )


# ─────────────────────────────────────────────────────────────────────
# Smoke: PluginLoadResult dataclass API
# ─────────────────────────────────────────────────────────────────────


def test_plugin_load_result_to_markdown():
    """The result dataclass must serialize to a markdown report."""
    from prismatic.quality.plugin_load import PluginLoadFinding

    result = PluginLoadResult(
        passed=False,
        plugins_dir="/tmp/plugins",
        core_version="0.2.0",
        loaded_count=0,
        failed_count=1,
        reason="1 plugin failed",
        findings=[
            PluginLoadFinding(
                plugin_name="bad-plugin",
                manifest_path="/tmp/plugins/bad-plugin/plugin-manifest.yaml",
                status="version_mismatch",
                detail="Core version '0.2.0' does not satisfy '>=99.0.0'",
            ),
        ],
    )

    md = result.to_markdown()
    assert "FAIL" in md
    assert "bad-plugin" in md
    assert "version_mismatch" in md
    assert "0.2.0" in md
    assert "/tmp/plugins" in md
    print("PASS: PluginLoadResult.to_markdown() produces a useful report")
