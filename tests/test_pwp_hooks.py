"""
Tests for GRO-2228 — PWP hook stubs in prismatic.interface.hooks,
prismatic.interface.plugin, and prismatic.core.registry.PWPPluginRunner.

These tests verify the four acceptance criteria spelled out in the
Linear issue:

1. All 4 PWP hooks (pre-pipeline, post-pipeline, on-error, on-deploy)
   are exposed as canonical hook-name constants in
   ``prismatic.interface.hooks``.
2. The :class:`PrismaticPlugin` ABC exposes optional method stubs for
   each of the 4 hooks.
3. A test plugin (``plugins/pwp_hook_test_plugin``) can register for
   all 4 hooks via its manifest and receive every event.
4. Hooks fire in the correct order during a PWP pipeline run, and a
   crashing plugin is isolated from the runner.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

# Ensure the engine root is importable when this file is run directly
# by ``pytest tests/test_pwp_hooks.py`` from anywhere.
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from prismatic.core.registry import PWPPluginRunner, PluginLoader
from prismatic.interface.hooks import (
    HOOK_NAMES,
    HOOK_ON_DEPLOY,
    HOOK_ON_ERROR,
    HOOK_ON_POST_PIPELINE,
    HOOK_ON_PRE_PIPELINE,
    PWP_HOOK_NAMES,
)
from prismatic.interface.plugin import PluginContext, PrismaticPlugin


# ── 1. Hook-name constants exposed correctly ──────────────────────────────


def test_all_four_pwp_hooks_are_canonical_constants() -> None:
    """GRO-2228 acceptance: All 4 hooks (pre, post, error, deploy) exposed."""
    assert HOOK_ON_PRE_PIPELINE == "on_pre_pipeline"
    assert HOOK_ON_POST_PIPELINE == "on_post_pipeline"
    assert HOOK_ON_ERROR == "on_error"
    assert HOOK_ON_DEPLOY == "on_deploy"
    # And they're in the canonical HOOK_NAMES list (used for manifest
    # validation by the loader).
    for name in PWP_HOOK_NAMES:
        assert name in HOOK_NAMES, f"{name} missing from HOOK_NAMES"


def test_pwp_hook_grouping_helper() -> None:
    """PWP_HOOK_NAMES is the PWP subset of HOOK_NAMES, exactly 4 items."""
    assert set(PWP_HOOK_NAMES) == {
        HOOK_ON_PRE_PIPELINE,
        HOOK_ON_POST_PIPELINE,
        HOOK_ON_ERROR,
        HOOK_ON_DEPLOY,
    }
    assert len(PWP_HOOK_NAMES) == 4


# ── 2. PrismaticPlugin ABC exposes optional stubs ──────────────────────────


def test_prismatic_plugin_exposes_pwp_method_stubs() -> None:
    """GRO-2228 acceptance: plugin authors can override the 4 hooks."""

    class _Dummy(PrismaticPlugin):
        def on_init(self, context: PluginContext) -> None:  # type: ignore[override]
            return

        def register_tools(self):  # type: ignore[override]
            return []

    inst = _Dummy()
    # All 4 PWP hooks are present and callable as no-ops by default.
    assert callable(inst.on_pre_pipeline)
    assert callable(inst.on_post_pipeline)
    assert callable(inst.on_error)
    assert callable(inst.on_deploy)
    # They default to no-ops (return None).
    assert inst.on_pre_pipeline("p1", {}) is None
    assert inst.on_post_pipeline("p1", {"status": "succeeded"}) is None
    assert inst.on_error("p1", RuntimeError("boom"), "build") is None
    assert inst.on_deploy("p1", "cloudflare-pages", {}) is None


# ── 3. PWPPluginRunner fires hooks in the correct order ────────────────────


def _build_loader_with_recording_plugin() -> PluginLoader:
    """
    Construct a PluginLoader pointing at a temp plugins/ directory
    that contains a copy of the PWPHookTestPlugin manifest + module.

    Returns a loader with the plugin already loaded.
    """
    # Import the fixture plugin (it self-registers its class-level
    # ``events`` list).  ``pwp_hook_test_plugin`` is a *package*, so
    # its parent (``plugins/``) must be on sys.path.
    plugins_root = _REPO_ROOT / "plugins"
    if str(plugins_root) not in sys.path:
        sys.path.insert(0, str(plugins_root))
    from pwp_hook_test_plugin.plugin import PWPHookTestPlugin

    # Reset events between tests.
    PWPHookTestPlugin.events = []

    plugin_dir = plugins_root / "pwp_hook_test_plugin"
    assert (plugin_dir / "plugin.py").exists()
    assert (plugin_dir / "plugin-manifest.yaml").exists()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Copy the plugin directory to the temp plugins root.
        dst = tmp_path / "pwp_hook_test_plugin"
        dst.mkdir()
        # Re-create the package marker so the loader can import it.
        (dst / "__init__.py").write_text("")
        (dst / "plugin.py").write_text(
            (plugin_dir / "plugin.py").read_text()
        )
        (dst / "plugin-manifest.yaml").write_text(
            (plugin_dir / "plugin-manifest.yaml").read_text()
        )

        loader = PluginLoader(core_version="1.0.0", plugins_dir=str(tmp_path))
        context = PluginContext(
            config={},
            db_connection=None,
            state_dir=tmp,
            telemetry_client=None,
            lock_manager=None,
        )
        loader.scan_and_load_plugins(context)
        assert "pwp-hook-test-plugin" in loader.loaded_plugins
        return loader


def test_pwp_runner_fires_hooks_in_order_on_success() -> None:
    """GRO-2228 acceptance: hooks fire in the correct order."""
    loader = _build_loader_with_recording_plugin()
    runner = PWPPluginRunner(loader)

    def stage_one(_ctx: Dict[str, Any]) -> str:
        return "one"

    def stage_two(_ctx: Dict[str, Any]) -> str:
        return "two"

    result = runner.run(
        pipeline_id="GRO-2228-success",
        context={"issue_id": "GRO-2228"},
        stages=[("build", stage_one), ("test", stage_two)],
        deploy_target="cloudflare-pages",
        deploy_artifact_provider=lambda _r: {"url": "https://example.test"},
    )

    assert result["status"] == "succeeded"
    assert [s["name"] for s in result["stages"]] == ["build", "test"]

    from pwp_hook_test_plugin.plugin import PWPHookTestPlugin

    hook_names = [e["hook"] for e in PWPHookTestPlugin.events]
    assert hook_names == [
        "on_pre_pipeline",
        "on_post_pipeline",
        "on_deploy",
    ], f"Unexpected hook order: {hook_names}"


def test_pwp_runner_fires_on_error_and_reraises() -> None:
    """GRO-2228 acceptance: on_error fires on failure, exception re-raises."""
    loader = _build_loader_with_recording_plugin()
    runner = PWPPluginRunner(loader)

    def stage_ok(_ctx: Dict[str, Any]) -> str:
        return "ok"

    def stage_boom(_ctx: Dict[str, Any]) -> None:
        raise ValueError("intentional boom")

    with pytest.raises(ValueError, match="intentional boom"):
        runner.run(
            pipeline_id="GRO-2228-fail",
            context={},
            stages=[("build", stage_ok), ("publish", stage_boom)],
        )

    from pwp_hook_test_plugin.plugin import PWPHookTestPlugin

    hook_names = [e["hook"] for e in PWPHookTestPlugin.events]
    # on_pre_pipeline, then on_error.  No on_post_pipeline, no on_deploy.
    assert hook_names == ["on_pre_pipeline", "on_error"]

    err_event = PWPHookTestPlugin.events[-1]
    assert err_event["stage"] == "publish"
    assert err_event["exc_type"] == "ValueError"


def test_pwp_runner_skips_deploy_on_failure() -> None:
    """on_deploy does NOT fire when a stage fails (best-effort notification)."""
    loader = _build_loader_with_recording_plugin()
    runner = PWPPluginRunner(loader)

    def stage_boom(_ctx: Dict[str, Any]) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        runner.run(
            pipeline_id="GRO-2228-fail-no-deploy",
            context={},
            stages=[("build", stage_boom)],
            deploy_target="cloudflare-pages",
            deploy_artifact_provider=lambda _r: {"url": "x"},
        )

    from pwp_hook_test_plugin.plugin import PWPHookTestPlugin

    hook_names = [e["hook"] for e in PWPHookTestPlugin.events]
    assert "on_deploy" not in hook_names


# ── 4. Crash isolation: a plugin that throws must not abort the runner ─────


def test_pwp_runner_isolates_crashing_plugin_hooks() -> None:
    """A plugin that raises inside a hook must NOT crash the runner."""

    class CrashingPWPPlugin(PrismaticPlugin):
        def on_init(self, context: PluginContext) -> None:  # type: ignore[override]
            return

        def register_tools(self):  # type: ignore[override]
            return []

        def on_pre_pipeline(self, pipeline_id, context):  # type: ignore[override]
            raise RuntimeError("plugin crash in on_pre_pipeline")

        def on_post_pipeline(self, pipeline_id, result):  # type: ignore[override]
            raise RuntimeError("plugin crash in on_post_pipeline")

        def on_error(self, pipeline_id, exc, stage):  # type: ignore[override]
            return

        def on_deploy(self, pipeline_id, target, artifact):  # type: ignore[override]
            return

    loader = PluginLoader(core_version="1.0.0", plugins_dir="/nonexistent")
    # Inject the plugin directly (bypass manifest discovery for this test).
    loader.loaded_plugins["crashing"] = CrashingPWPPlugin()

    runner = PWPPluginRunner(loader)

    def stage(_ctx: Dict[str, Any]) -> str:
        return "ok"

    # Must not raise even though the plugin's hooks crash.
    result = runner.run(
        pipeline_id="GRO-2228-isolation",
        context={},
        stages=[("build", stage)],
    )
    assert result["status"] == "succeeded"
