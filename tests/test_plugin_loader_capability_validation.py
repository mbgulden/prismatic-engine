"""Integration tests: PluginLoader capability + provider validation.

4 tests that exercise the Gap 10 Plugin Discovery Hardening — the loader
must reject plugins that require unavailable capabilities, reject plugins
that block the active provider, reject plugins with version mismatches,
and warn (not reject) plugins that declare unknown capabilities.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from prismatic.core.registry import PluginLoader
from prismatic.interface.plugin import PluginContext
from prismatic.review.registry import ReviewerRegistry


_CORE_VERSION = "1.0.0"


def _write_manifest_only(
    plugins_dir: Path,
    *,
    package: str,
    name: str,
    manifest_body: str,
) -> Path:
    """Write a plugin dir containing ONLY a manifest (no plugin.py).

    Used for validation-rejection tests where we don't want the loader
    to reach the import step. A missing plugin module would raise
    PluginValidationError at import time, which would mask the earlier
    capability / provider / version rejection we actually want to test.
    """
    plugin_dir = plugins_dir / package
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin-manifest.yaml").write_text(textwrap.dedent(manifest_body))
    return plugin_dir


def _make_context(
    plugins_dir: Path,
    *,
    environment_capabilities: set[str] | None = None,
    active_provider: str | None = None,
    provider_versions: dict[str, str] | None = None,
) -> PluginContext:
    ctx = PluginContext(
        config={
            "plugins_dir": str(plugins_dir),
            "environment_capabilities": environment_capabilities or set(),
            "active_provider": active_provider,
            "provider_versions": provider_versions or {},
        },
        db_connection=None,
        state_dir=str(plugins_dir),
    )
    ctx.review_registry = ReviewerRegistry()  # type: ignore[attr-defined]
    return ctx


# ─────────────────────────────────────────────────────────────────────
# Test 1 — loader rejects a plugin that requires an unsupported capability
# ─────────────────────────────────────────────────────────────────────


def test_loader_rejects_unsupported_required_capability(tmp_path: Path, caplog) -> None:
    """If a manifest declares required_capabilities: ['gpu'] but the
    environment capability set is empty, PluginLoader must skip the
    plugin (NOT add it to loaded_plugins) and log the rejection at
    ERROR level. The loader wraps _load_plugin in try/except so
    exceptions are caught at the loop level — the observable behavior
    is that the plugin is absent from loaded_plugins after the call."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_manifest_only(
        plugins_dir,
        package="gpu_plugin",
        name="gpu-plugin",
        manifest_body=f"""\
            schema_version: "1.0.0"
            name: "gpu-plugin"
            version: "1.0.0"
            entry_point: "gpu_plugin.plugin:GpuPlugin"
            core_version_constraint: ">={_CORE_VERSION}, <2.0.0"
            dependencies:
              pip: []
            personas: []
            required_capabilities:
              - "gpu"
            """,
    )

    # Environment exposes NO gpu capability.
    ctx = _make_context(plugins_dir)
    loader = PluginLoader(core_version=_CORE_VERSION, plugins_dir=str(plugins_dir))
    with caplog.at_level(logging.ERROR, logger="prismatic.loader"):
        loader.scan_and_load_plugins(ctx)

    # The plugin was rejected — not in loaded_plugins.
    assert "gpu-plugin" not in loader.loaded_plugins
    # And the rejection reason was logged at ERROR. The loader uses
    # ``logger.error(..., exc_info=True)`` so the actual
    # PluginValidationError is in ``record.exc_info[1]``.
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        r.exc_info is not None and "gpu" in str(r.exc_info[1]) for r in error_records
    ), (
        f"Expected ERROR log mentioning 'gpu'; got: {[r.getMessage() for r in error_records]}"
    )


# ─────────────────────────────────────────────────────────────────────
# Test 2 — loader rejects a plugin that blocks the active provider
# ─────────────────────────────────────────────────────────────────────


def test_loader_rejects_blocked_provider_constraint(tmp_path: Path, caplog) -> None:
    """If a manifest declares blocked_providers: ['claude-code'] and the
    active provider is 'claude-code', PluginLoader must skip the plugin
    and log the rejection at ERROR level. The loader catches the
    underlying PluginValidationError internally so the observable
    behavior is: plugin absent from loaded_plugins + ERROR log."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_manifest_only(
        plugins_dir,
        package="anti_claude_plugin",
        name="anti-claude-plugin",
        manifest_body=f"""\
            schema_version: "1.0.0"
            name: "anti-claude-plugin"
            version: "1.0.0"
            entry_point: "anti_claude_plugin.plugin:AntiClaudePlugin"
            core_version_constraint: ">={_CORE_VERSION}, <2.0.0"
            dependencies:
              pip: []
            personas: []
            blocked_providers:
              - "claude-code"
            """,
    )

    ctx = _make_context(
        plugins_dir,
        active_provider="claude-code",
    )
    loader = PluginLoader(core_version=_CORE_VERSION, plugins_dir=str(plugins_dir))
    with caplog.at_level(logging.ERROR, logger="prismatic.loader"):
        loader.scan_and_load_plugins(ctx)

    assert "anti-claude-plugin" not in loader.loaded_plugins
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        r.exc_info is not None and "claude-code" in str(r.exc_info[1])
        for r in error_records
    ), (
        f"Expected ERROR log mentioning 'claude-code'; got: {[r.getMessage() for r in error_records]}"
    )


# ─────────────────────────────────────────────────────────────────────
# Test 3 — loader rejects a plugin with a core_version_constraint mismatch
# ─────────────────────────────────────────────────────────────────────


def test_loader_rejects_version_mismatch(tmp_path: Path, caplog) -> None:
    """A manifest declaring core_version_constraint '>=99.0.0' against a
    running core of 1.0.0 must be rejected — the plugin must NOT end up
    in loaded_plugins and the rejection must be logged at ERROR level."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_manifest_only(
        plugins_dir,
        package="future_plugin",
        name="future-plugin",
        manifest_body="""\
            schema_version: "1.0.0"
            name: "future-plugin"
            version: "1.0.0"
            entry_point: "future_plugin.plugin:FuturePlugin"
            core_version_constraint: ">=99.0.0"
            dependencies:
              pip: []
            personas: []
            """,
    )

    ctx = _make_context(plugins_dir)
    loader = PluginLoader(core_version=_CORE_VERSION, plugins_dir=str(plugins_dir))
    with caplog.at_level(logging.ERROR, logger="prismatic.loader"):
        loader.scan_and_load_plugins(ctx)

    assert "future-plugin" not in loader.loaded_plugins
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    # Either the version number or the word "version" must appear in
    # the underlying PluginValidationError message.
    assert any(
        r.exc_info is not None
        and ("99.0.0" in str(r.exc_info[1]) or "version" in str(r.exc_info[1]).lower())
        for r in error_records
    ), (
        f"Expected ERROR log mentioning version mismatch; got: {[r.getMessage() for r in error_records]}"
    )


# ─────────────────────────────────────────────────────────────────────
# Test 4 — loader warns (but does not reject) on unknown capability
# ─────────────────────────────────────────────────────────────────────


def test_loader_warns_on_unknown_required_capability(tmp_path: Path, caplog) -> None:
    """A manifest declaring a required capability the host doesn't
    recognise (e.g. 'future_feature') must be WARNED, not rejected. The
    plugin should still appear in loaded_plugins because unknown
    capabilities are forward-compatibility hints — the loader cannot
    know whether they're truly available or not."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    # We need a real plugin.py to avoid masking the warning with an
    # import error. Write a minimal valid plugin + a manifest that
    # declares an unknown capability.
    plugin_dir = plugins_dir / "future_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").write_text("")
    (plugin_dir / "plugin.py").write_text(
        textwrap.dedent(
            """\
            from prismatic.interface.plugin import PrismaticPlugin

            class FuturePlugin(PrismaticPlugin):
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
            name: "future-feature-plugin"
            version: "1.0.0"
            entry_point: "future_plugin.plugin:FuturePlugin"
            core_version_constraint: ">={_CORE_VERSION}, <2.0.0"
            dependencies:
              pip: []
            personas: []
            required_capabilities:
              - "future_feature_unknown_to_host"
            """
        )
    )

    ctx = _make_context(plugins_dir)
    loader = PluginLoader(core_version=_CORE_VERSION, plugins_dir=str(plugins_dir))

    # Capture warnings from the prismatic.loader logger.
    with caplog.at_level(logging.WARNING, logger="prismatic.loader"):
        loader.scan_and_load_plugins(ctx)

    # Plugin loaded (warning is non-fatal for unknown capabilities).
    assert "future-feature-plugin" in loader.loaded_plugins

    # And the warning was emitted at WARNING level mentioning the
    # unknown capability.
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert any("future_feature_unknown_to_host" in msg for msg in warning_messages), (
        f"Expected a warning mentioning the unknown capability; got: {warning_messages}"
    )
