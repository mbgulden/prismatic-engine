"""
PluginLoader — dynamic plugin discovery, validation, and registration.

Scans ``$PRISMATIC_HOME/plugins/`` for ``plugin-manifest.yaml`` files,
validates version constraints, dynamically imports Python modules, and
exposes loaded plugins, registered personas, and tools to the event loop.

All hook execution is wrapped in try-catch isolation — a crashing plugin
never brings down the dispatcher daemon.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from prismatic.interface.plugin import (
    PluginContext,
    PrismaticPlugin,
    PluginValidationError,
)

logger = logging.getLogger("prismatic.loader")


class PluginLoader:
    """
    Orchestrates discovery, validation, and dynamic loading of Prismatic
    plugins from the target plugin directory.
    """

    def __init__(self, core_version: str, plugins_dir: str) -> None:
        self.core_version = core_version
        self.plugins_dir = plugins_dir
        self.loaded_plugins: Dict[str, PrismaticPlugin] = {}
        self.registered_personas: Dict[str, Dict[str, Any]] = {}
        self.registered_tools: List[Dict[str, Any]] = []

    # ── public API ─────────────────────────────────────────────────────

    def scan_and_load_plugins(self, context: PluginContext) -> None:
        """
        Scan ``$PRISMATIC_HOME/plugins/`` for ``plugin-manifest.yaml``
        files, validate requirements, and dynamically register plugins.
        """
        if not os.path.exists(self.plugins_dir):
            logger.warning("Plugin directory does not exist: %s", self.plugins_dir)
            return

        for entry in os.scandir(self.plugins_dir):
            if not entry.is_dir():
                continue

            manifest_path = Path(entry.path) / "plugin-manifest.yaml"
            if not manifest_path.exists():
                continue

            try:
                self._load_plugin(manifest_path, context)
            except Exception:
                logger.error(
                    "Failed to load plugin from %s",
                    manifest_path.parent.name,
                    exc_info=True,
                )

    def execute_hook(self, hook_name: str, *args: Any, **kwargs: Any) -> None:
        """
        Execute *hook_name* across every registered plugin in try-catch
        isolation.  A single plugin failure is logged but does not
        interrupt the dispatcher event loop.
        """
        for name, plugin in self.loaded_plugins.items():
            if not hasattr(plugin, hook_name):
                continue
            try:
                hook_func = getattr(plugin, hook_name)
                hook_func(*args, **kwargs)
            except Exception:
                logger.error(
                    "Plugin '%s' failed during hook '%s'",
                    name,
                    hook_name,
                    exc_info=True,
                )

    # ── internal ───────────────────────────────────────────────────────

    def _load_plugin(self, manifest_path: Path, context: PluginContext) -> None:
        with open(manifest_path, "r") as fh:
            manifest = yaml.safe_load(fh)

        name = manifest.get("name")
        version = manifest.get("version")
        entry_point = manifest.get("entry_point")
        core_constraint = manifest.get("core_version_constraint")

        if not all([name, version, entry_point, core_constraint]):
            raise PluginValidationError(
                f"Missing required fields in manifest: {manifest_path}"
            )

        # 1. Core version validation
        specifier = SpecifierSet(core_constraint)
        if Version(self.core_version) not in specifier:
            raise PluginValidationError(
                f"Core version '{self.core_version}' does not satisfy "
                f"constraint '{core_constraint}' for plugin '{name}'."
            )

        # 2. Dynamic import
        module_path, class_name = entry_point.split(":")
        plugin_root = str(manifest_path.parent)

        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        try:
            module = importlib.import_module(module_path)
            plugin_class = getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            raise PluginValidationError(
                f"Failed to import entry point '{entry_point}': {exc}"
            ) from exc

        if not issubclass(plugin_class, PrismaticPlugin):
            raise PluginValidationError(
                f"Class '{class_name}' must inherit from PrismaticPlugin."
            )

        # 3. Instantiate + fire on_init in isolation
        plugin_instance = plugin_class()
        try:
            plugin_instance.on_init(context)
        except Exception as exc:
            raise PluginValidationError(
                f"Exception raised during on_init execution: {exc}"
            ) from exc

        self.loaded_plugins[name] = plugin_instance

        # 4. Register personas
        for persona in manifest.get("personas", []):
            persona_id = persona.get("id")
            self.registered_personas[persona_id] = persona
            logger.info(
                "Registered persona '%s' from plugin '%s'",
                persona_id,
                name,
            )

        # 5. Register tools
        try:
            tools = plugin_instance.register_tools()
            self.registered_tools.extend(tools)
        except Exception:
            logger.error("Plugin '%s' failed to register tools", name, exc_info=True)

        logger.info("Successfully loaded plugin '%s' (v%s)", name, version)


# ── GRO-2228: PWP pipeline hook orchestrator ───────────────────────────────

from prismatic.interface.hooks import (  # noqa: E402
    HOOK_ON_PRE_PIPELINE,
    HOOK_ON_POST_PIPELINE,
    HOOK_ON_ERROR,
    HOOK_ON_DEPLOY,
)

# A PWP pipeline stage is a ``(name, callable)`` pair.  The callable
# receives the pipeline context dict and returns any JSON-serializable
# value that will be stored under ``result["stages"][i]["output"]``.
Stage = Tuple[str, Callable[[Dict[str, Any]], Any]]

# GRO-3713: canonical PWP theme provenance fields recorded in deployment
# manifests.  Keep the manifest keys camelCase to match the theme master plan
# and generated JSON artifacts.  The alias lists accept common engine/Python
# snake_case inputs so callers do not need to hard-code duplicate conventions.
PWP_THEME_PROVENANCE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "themeId": ("themeId", "theme_id"),
    "themeVersion": ("themeVersion", "theme_version"),
    "tokenHash": ("tokenHash", "token_hash"),
    "moduleHash": ("moduleHash", "module_hash"),
    "contentHash": ("contentHash", "content_hash"),
    "engineVersion": ("engineVersion", "engine_version"),
    "sourceCommit": (
        "sourceCommit",
        "source_commit",
        "gitCommit",
        "git_commit",
        "commit",
    ),
}


def _first_present(*sources: Dict[str, Any], aliases: Tuple[str, ...]) -> Any:
    """Return the first non-empty provenance value from *sources*."""
    for source in sources:
        for alias in aliases:
            value = source.get(alias)
            if value not in (None, ""):
                return value
    return None


def build_pwp_deployment_manifest(
    *,
    core_version: str,
    pipeline_id: str,
    context: Dict[str, Any],
    artifact: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the deployment manifest attached to PWP deploy artifacts.

    Theme provenance may be supplied either flat in ``context``/``artifact`` or
    under ``themeProvenance``/``theme_provenance``.  The returned manifest uses
    canonical camelCase keys and preserves deploy traceability even before the
    later run-state/rollback issues add persistence.
    """

    context_provenance = (
        context.get("themeProvenance") or context.get("theme_provenance") or {}
    )
    artifact_provenance = (
        artifact.get("themeProvenance") or artifact.get("theme_provenance") or {}
    )
    if not isinstance(context_provenance, dict):
        context_provenance = {}
    if not isinstance(artifact_provenance, dict):
        artifact_provenance = {}

    manifest: Dict[str, Any] = {
        "pipelineId": pipeline_id,
        "engineVersion": core_version,
    }

    for key, aliases in PWP_THEME_PROVENANCE_ALIASES.items():
        value = _first_present(
            context_provenance,
            artifact_provenance,
            context,
            artifact,
            aliases=aliases,
        )
        if value is not None:
            manifest[key] = value

    # ``engineVersion`` always falls back to the loader core version so every
    # manifest can be traced to an engine compatibility boundary.
    manifest.setdefault("engineVersion", core_version)
    return manifest


def attach_pwp_deployment_manifest(
    *,
    core_version: str,
    pipeline_id: str,
    context: Dict[str, Any],
    artifact: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a copy of *artifact* with a PWP deployment manifest attached."""

    enriched = dict(artifact)
    existing_manifest = enriched.get("deploymentManifest")
    if existing_manifest is None:
        existing_manifest = enriched.get("deployment_manifest")
    manifest: Dict[str, Any] = {}
    if isinstance(existing_manifest, dict):
        manifest.update(existing_manifest)
    manifest.update(
        build_pwp_deployment_manifest(
            core_version=core_version,
            pipeline_id=pipeline_id,
            context=context,
            artifact=artifact,
        )
    )
    enriched["deploymentManifest"] = manifest
    enriched.pop("deployment_manifest", None)
    return enriched


class PWPPluginRunner:
    """
    Orchestrates a PWP (Prismatic Web Plugin) pipeline run.

    Wraps a list of stage callables with the 4 PWP hooks:

    1. ``on_pre_pipeline``  — fires *once* before any stage runs
    2. ``on_post_pipeline`` — fires *once* if every stage succeeded
    3. ``on_error``         — fires *once* if any stage raises (the
                              exception is re-raised after the hook
                              returns so callers can still see it)
    4. ``on_deploy``        — fires *once* after a successful
                              post-pipeline, if a ``deploy_target`` is
                              provided to :meth:`run`

    All hook dispatches go through :meth:`PluginLoader.execute_hook`,
    so a crashing plugin can never abort the run.

    Example::

        runner = PWPPluginRunner(loader)
        result = runner.run(
            pipeline_id="GRO-2228-abc",
            context={"issue_id": "GRO-2228", "branch": "ned/GRO-2228"},
            stages=[
                ("build",  build_site),
                ("test",   run_tests),
            ],
            deploy_target="cloudflare-pages",
        )

    Args:
        loader: A :class:`PluginLoader` instance whose ``loaded_plugins``
            will be notified at each hook.
    """

    def __init__(self, loader: "PluginLoader") -> None:
        self.loader = loader

    def run(
        self,
        pipeline_id: str,
        context: Dict[str, Any],
        stages: Iterable[Stage],
        deploy_target: Optional[str] = None,
        deploy_artifact_provider: Optional[
            Callable[[Dict[str, Any]], Dict[str, Any]]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Run a PWP pipeline.

        Args:
            pipeline_id: Unique identifier for the run; threaded through
                every hook invocation.
            context: Read-only metadata describing the pipeline.
            stages: Iterable of ``(stage_name, callable)`` pairs.  Each
                callable is invoked with the post-pipeline ``context``
                dict (mutations are visible to later stages).
            deploy_target: If provided, ``on_deploy`` fires after
                ``on_post_pipeline`` with this target name.
            deploy_artifact_provider: Optional callable that, given the
                aggregated ``result`` dict, returns the artifact dict
                forwarded to ``on_deploy``.  Required when
                ``deploy_target`` is set.

        Returns:
            The aggregated ``result`` dict.  Contains at minimum::

                {
                    "status": "succeeded",
                    "stages": [{"name": ..., "ok": True, "output": ...}, ...],
                }

        Raises:
            BaseException: Re-raises the first exception any stage
                raised, *after* firing the ``on_error`` hook.
        """
        self.loader.execute_hook(HOOK_ON_PRE_PIPELINE, pipeline_id, context)

        result: Dict[str, Any] = {"status": "running", "stages": []}
        try:
            for stage_name, stage_fn in stages:
                try:
                    output = stage_fn(context)
                except BaseException as exc:
                    result["status"] = "failed"
                    result["failed_stage"] = stage_name
                    self.loader.execute_hook(
                        HOOK_ON_ERROR, pipeline_id, exc, stage_name
                    )
                    raise
                result["stages"].append(
                    {"name": stage_name, "ok": True, "output": output}
                )
        finally:
            # No-op finally block — kept for symmetry with the
            # orchestrator's future resource-cleanup hook.
            pass

        result["status"] = "succeeded"
        self.loader.execute_hook(HOOK_ON_POST_PIPELINE, pipeline_id, result)

        if deploy_target is not None:
            if deploy_artifact_provider is None:
                raise ValueError(
                    "deploy_artifact_provider is required when deploy_target is set"
                )
            artifact = deploy_artifact_provider(result)
            artifact = attach_pwp_deployment_manifest(
                core_version=self.loader.core_version,
                pipeline_id=pipeline_id,
                context=context,
                artifact=artifact,
            )
            result["deploymentManifest"] = artifact["deploymentManifest"]
            self.loader.execute_hook(
                HOOK_ON_DEPLOY, pipeline_id, deploy_target, artifact
            )

        return result
