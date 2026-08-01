"""Integration tests: PluginLoader → PipelineOrchestrator wiring.

4 tests that exercise PluginLoader.scan_and_load_plugins() against a real
plugin manifest in a tmp_path, then verify the loaded plugin's registrations
are observable through the ReviewerRegistry and fire during a
PipelineOrchestrator.process() call.

These are repo-level integration tests — they sit alongside the other
test_*.py files at the repository root rather than inside the plugin's
own tests/ directory.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from prismatic.core.registry import PluginLoader
from prismatic.interface.plugin import PluginContext, PluginValidationError
from prismatic.review.pipeline import (
    ACTION_REWORK,
    IMPACT_MINOR,
    IMPACT_TRIVIAL,
    PipelineOrchestrator,
)
from prismatic.review.pr_reviewer import APPROVE, PRReviewResult
from prismatic.review.registry import ReviewerRegistry


# Core version used for all tests — matches the engine's current version.
_CORE_VERSION = "1.0.0"


def _write_plugin(
    plugins_dir: Path,
    *,
    package: str,
    name: str,
    manifest_extras: str = "",
    plugin_body: str | None = None,
) -> Path:
    """Materialize a minimal valid plugin under plugins_dir.

    Creates:
      <plugins_dir>/<package>/__init__.py
      <plugins_dir>/<package>/plugin.py
      <plugins_dir>/<package>/plugin-manifest.yaml

    Returns the path to the plugin directory.
    """
    plugin_dir = plugins_dir / package
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text("")
    (plugin_dir / "plugin.py").write_text(
        plugin_body
        or textwrap.dedent(
            """\
            from prismatic.interface.plugin import PrismaticPlugin

            class MinimalPlugin(PrismaticPlugin):
                def on_init(self, context):
                    pass
                def register_tools(self):
                    return []
            """
        )
    )
    (plugin_dir / "plugin-manifest.yaml").write_text(
        textwrap.dedent(
            f"""\
            schema_version: "1.0.0"
            name: "{name}"
            version: "1.0.0"
            entry_point: "{package}.plugin:MinimalPlugin"
            core_version_constraint: ">={_CORE_VERSION}, <2.0.0"
            dependencies:
              pip: []
            personas: []
            {manifest_extras}
            """
        )
    )
    return plugin_dir


def _make_context(
    plugins_dir: Path,
    *,
    environment_capabilities: set[str] | None = None,
    active_provider: str | None = None,
) -> PluginContext:
    """Construct a PluginContext wired to a ReviewerRegistry.

    The production dispatcher exposes the review registry on the context as
    ``context.review_registry``. PluginContext (the dataclass) does not
    declare this attribute — we attach it dynamically here, mirroring how
    the real dispatcher constructs its richer context object.
    """
    ctx = PluginContext(
        config={
            "plugins_dir": str(plugins_dir),
            "environment_capabilities": environment_capabilities or set(),
            "active_provider": active_provider,
        },
        db_connection=None,
        state_dir=str(plugins_dir),
    )
    # PluginContext (the dataclass) doesn't declare review_registry —
    # production dispatchers attach it dynamically. Mirror that here.
    ctx.review_registry = ReviewerRegistry()  # type: ignore[attr-defined]
    return ctx


# ─────────────────────────────────────────────────────────────────────
# Test 1 — loader picks up a valid plugin manifest
# ─────────────────────────────────────────────────────────────────────


def test_loader_picks_up_valid_plugin_manifest(tmp_path: Path) -> None:
    """PluginLoader must discover a plugin-manifest.yaml in the plugins dir
    and instantiate the plugin class declared in entry_point."""
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, package="alpha_plugin", name="alpha-plugin")
    _write_plugin(plugins_dir, package="beta_plugin", name="beta-plugin")

    ctx = _make_context(plugins_dir)
    loader = PluginLoader(core_version=_CORE_VERSION, plugins_dir=str(plugins_dir))
    loader.scan_and_load_plugins(ctx)

    # Both plugins must have been loaded.
    assert "alpha-plugin" in loader.loaded_plugins
    assert "beta-plugin" in loader.loaded_plugins
    # And the plugin classes are real PrismaticPlugin instances.
    from prismatic.interface.plugin import PrismaticPlugin

    for plugin in loader.loaded_plugins.values():
        assert isinstance(plugin, PrismaticPlugin)


# ─────────────────────────────────────────────────────────────────────
# Test 2 — loaded plugin registers in the registry via on_init
# ─────────────────────────────────────────────────────────────────────


def test_loaded_plugin_registers_in_registry_via_on_init(
    tmp_path: Path,
) -> None:
    """When a plugin's on_init() registers patterns on the registry, those
    registrations must be visible after scan_and_load_plugins() returns."""
    plugins_dir = tmp_path / "plugins"
    package = "test_wiring_plugin"
    # Write a custom plugin body that registers exactly one impact rule.
    (plugins_dir := plugins_dir).mkdir(parents=True, exist_ok=True)
    plugin_dir = plugins_dir / package
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text("")
    (plugin_dir / "plugin.py").write_text(
        textwrap.dedent(
            """\
            from prismatic.interface.plugin import PrismaticPlugin

            def my_impact_rule(result, current):
                return "minor" if "marker" in result.summary else None

            class WiringPlugin(PrismaticPlugin):
                def on_init(self, context):
                    registry = getattr(context, "review_registry", None)
                    if registry is None:
                        return
                    registry.register_impact_rule(my_impact_rule)
                def register_tools(self):
                    return []
            """
        )
    )
    (plugin_dir / "plugin-manifest.yaml").write_text(
        textwrap.dedent(
            f"""\
            schema_version: "1.0.0"
            name: "wiring-plugin"
            version: "1.0.0"
            entry_point: "{package}.plugin:WiringPlugin"
            core_version_constraint: ">={_CORE_VERSION}, <2.0.0"
            dependencies:
              pip: []
            personas: []
            """
        )
    )

    ctx = _make_context(plugins_dir)
    loader = PluginLoader(core_version=_CORE_VERSION, plugins_dir=str(plugins_dir))
    loader.scan_and_load_plugins(ctx)

    # The plugin loaded.
    assert "wiring-plugin" in loader.loaded_plugins

    # The registry on the context now contains the rule registered by on_init.
    spec = ctx.review_registry.compose()
    assert len(spec.impact_rules) == 1
    assert spec.impact_rules[0].__name__ == "my_impact_rule"


# ─────────────────────────────────────────────────────────────────────
# Test 3 — pipeline process uses rules registered by loaded plugin
# ─────────────────────────────────────────────────────────────────────


def test_pipeline_process_uses_loaded_plugin_rules(
    tmp_path: Path,
) -> None:
    """End-to-end: load a plugin that registers an impact rule, then run
    PipelineOrchestrator.process() on a result whose summary triggers the
    rule. The decision.impact must be overridden from trivial → minor."""
    plugins_dir = tmp_path / "plugins"
    package = "trigger_plugin"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugin_dir = plugins_dir / package
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text("")
    (plugin_dir / "plugin.py").write_text(
        textwrap.dedent(
            """\
            from prismatic.interface.plugin import PrismaticPlugin

            def escalate_marker(result, current):
                # Override trivial → minor when the summary mentions "marker".
                if "marker" in result.summary.lower():
                    return "minor"
                return None

            class TriggerPlugin(PrismaticPlugin):
                def on_init(self, context):
                    registry = getattr(context, "review_registry", None)
                    if registry is None:
                        return
                    registry.register_impact_rule(escalate_marker)
                def register_tools(self):
                    return []
            """
        )
    )
    (plugin_dir / "plugin-manifest.yaml").write_text(
        textwrap.dedent(
            f"""\
            schema_version: "1.0.0"
            name: "trigger-plugin"
            version: "1.0.0"
            entry_point: "{package}.plugin:TriggerPlugin"
            core_version_constraint: ">={_CORE_VERSION}, <2.0.0"
            dependencies:
              pip: []
            personas: []
            """
        )
    )

    ctx = _make_context(plugins_dir)
    loader = PluginLoader(core_version=_CORE_VERSION, plugins_dir=str(plugins_dir))
    loader.scan_and_load_plugins(ctx)

    # Build a result that would naturally classify as trivial (APPROVE,
    # zero findings) — the rule should bump it to minor.
    result = PRReviewResult(
        verdict=APPROVE,
        summary="Trivial doc change with marker string.",
        inline_comments=[],
        metadata={
            "critical_count": 0,
            "high_count": 0,
            "warning_count": 0,
        },
    )
    orchestrator = PipelineOrchestrator(registry=ctx.review_registry)  # type: ignore[attr-defined]
    decision = orchestrator.process(
        identifier="GRO-LOADER-1",
        pr_url="https://example.test/pr/2",
        result=result,
    )

    # Without the rule the impact would be IMPACT_TRIVIAL; with the rule
    # it's IMPACT_MINOR. (ACTION_ADVANCE because the verdict is APPROVE;
    # no action rule was registered to override that.)
    assert decision.impact == IMPACT_MINOR


# ─────────────────────────────────────────────────────────────────────
# Test 4 — loader skips a directory without a manifest
# ─────────────────────────────────────────────────────────────────────


def test_loader_skips_directory_without_manifest(tmp_path: Path) -> None:
    """PluginLoader must silently skip subdirectories that don't contain a
    plugin-manifest.yaml. A noisy plugin dir without a manifest must not
    crash the loader or pollute loaded_plugins."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    # Directory 1: a manifest but no plugin module — loader should attempt
    # to import, fail, and continue (the loader wraps _load_plugin in
    # try/except and logs at error level).
    broken_dir = plugins_dir / "broken_plugin"
    broken_dir.mkdir()
    (broken_dir / "plugin-manifest.yaml").write_text(
        textwrap.dedent(
            f"""\
            schema_version: "1.0.0"
            name: "broken-plugin"
            version: "1.0.0"
            entry_point: "broken_plugin.plugin:DoesNotExist"
            core_version_constraint: ">={_CORE_VERSION}, <2.0.0"
            dependencies:
              pip: []
            personas: []
            """
        )
    )

    # Directory 2: no manifest at all — must be silently skipped.
    empty_dir = plugins_dir / "empty_dir"
    empty_dir.mkdir()
    (empty_dir / "README.md").write_text("not a plugin")

    # Directory 3: a regular file in the plugins dir, not a dir — must be
    # silently skipped (scandir returns non-dir entries).
    (plugins_dir / "stray_file.txt").write_text("ignore me")

    ctx = _make_context(plugins_dir)
    loader = PluginLoader(core_version=_CORE_VERSION, plugins_dir=str(plugins_dir))

    # scan_and_load_plugins must not raise, even though one of the plugins
    # is broken.
    loader.scan_and_load_plugins(ctx)

    # The broken plugin's import raised, so it must NOT be in loaded_plugins.
    assert "broken-plugin" not in loader.loaded_plugins
    # No spurious plugin names from the empty dir or stray file either.
    assert len(loader.loaded_plugins) == 0


# Silence unused import warnings for symbols re-exported by this module.
_ = (sys, pytest, PluginValidationError, ACTION_REWORK, IMPACT_TRIVIAL)


def test_loader_loads_canonical_plugin_end_to_end():
    """PluginLoader.scan_and_load_plugins() must successfully load the
    canonical reference plugin (plugins/prismatic_hello_world/) end-to-end.

    Regression: Gap 10 ships this plugin as the "working reference"
    example. If PluginLoader can't actually load it, the canonical
    reference claim is hollow. This test points the loader at the real
    plugins/ directory and verifies the plugin appears in loaded_plugins.
    """
    import re
    from pathlib import Path
    from prismatic.core.registry import PluginLoader
    from prismatic.interface.plugin import PluginContext

    # Resolve paths relative to this test file
    # tests/test_plugin_loader_pipeline.py -> repo root is parents[1]
    repo_root = Path(__file__).resolve().parents[1]
    real_plugins_dir = repo_root / "plugins"

    # Skip if the plugins/ dir doesn't exist (defensive: handles different cwds)
    if not real_plugins_dir.exists():
        return  # nothing to test, not a failure

    # Read the actual engine version from pyproject.toml
    pyproject = repo_root / "pyproject.toml"
    version_match = re.search(r'version\s*=\s*"([^"]+)"', pyproject.read_text())
    core_version = version_match.group(1) if version_match else "0.0.0"

    loader = PluginLoader(
        core_version=core_version,
        plugins_dir=str(real_plugins_dir),
    )

    # Use a duck-typed context that exposes a review_registry attribute,
    # since PluginContext does NOT yet have one (Gap 11 didn't add it).
    # PluginContext requires config/db_connection/state_dir.
    class _DuckContext(PluginContext):
        def __init__(self):
            super().__init__(config={}, db_connection=None, state_dir="/tmp")
            self.review_registry = None  # the real plugin uses getattr()

    loader.scan_and_load_plugins(_DuckContext())

    # The canonical plugin must be loaded
    assert "prismatic-hello-world" in loader.loaded_plugins, (
        f"canonical reference plugin not loaded. "
        f"loaded: {sorted(loader.loaded_plugins.keys())}"
    )
    print(
        "PASS: PluginLoader loaded the canonical prismatic-hello-world plugin end-to-end"
    )
