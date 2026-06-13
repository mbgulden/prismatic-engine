I will view the architecture blueprint file at `/home/ubuntu/work/prismatic-engine/specs/core-architecture-v1.md` to ensure our plugin specification aligns with the core architecture.
I will list the contents of the `/home/ubuntu/work/prismatic-engine` directory to see if there is existing source code or interfaces we should align our Python classes with.
I will list the `/home/ubuntu/work/prismatic-engine/prismatic/` directory to see the current structure of the Python package.
I will list the `/home/ubuntu/work/prismatic-engine/specs` directory to see what specification files currently exist.
I will perform a search for references to `plugin` or `manifest` in the existing codebase to ensure alignment with any early-stage implementations or notes.
# Prismatic Engine Core: Plugin Interface Specification (v1.0)

This document provides the complete plugin interface specification for the Prismatic Engine Core, extending the architectural principles defined in [core-architecture-v1.md](file:///home/ubuntu/work/prismatic-engine/specs/core-architecture-v1.md) (Sections 5 and 5.1–5.4).

---

## 1. plugin-manifest.yaml Format & Schema

Each plugin must include a [plugin-manifest.yaml](file:///home/ubuntu/work/prismatic-engine/plugins/example_plugin/plugin-manifest.yaml) file at its root. Below is the full YAML schema definition followed by a concrete configuration example.

### 1.1 Complete YAML Schema

```yaml
# Schema version meta-information
schema_version: "1.0.0"

# Unique identifier of the plugin (alphanumeric, lowercase, dashes/underscores)
name: "string"

# Semantic version of the plugin
version: "string"

# Short summary of the plugin's capabilities
description: "string"

# Author identity (human name or agent moniker)
author: "string"

# Fully qualified Python class path acting as the entry point
entry_point: "string"

# SemVer range specifying which core engine versions are supported
core_version_constraint: "string"

# External dependencies to validate and load
dependencies:
  pip:
    - "string"

# Array of custom persona extensions introduced by the plugin
personas:
  - id: "string"
    displayName: "string"
    systemPrompt: "string"
    defaultAllowedDirectories:
      - "string"
    defaultReadOnlyDirectories:
      - "string"
    preferredHead: "string"
    maxActions: 0

# Explicit registration of lifecycle hooks the plugin subscribes to
hooks:
  - "string"
```

### 1.2 Concrete Manifest Example

```yaml
name: "vram-observability"
version: "1.0.0"
description: "Monitors GPU memory allocation and integrates warnings into the BudgetManager."
author: "Fred (agent:fred)"
entry_point: "vram_observability.plugin:VRAMObservabilityPlugin"
core_version_constraint: ">=1.0.0, <2.0.0"

dependencies:
  pip:
    - "GPUtil>=1.4.0"
    - "prometheus-client>=0.17.0"

personas:
  - id: "GPU-COMPUTE-OBSERVER"
    displayName: "GPU Compute Observability Specialist"
    systemPrompt: |
      You are the GPU Compute Observability Specialist. You analyze GPU usage,
      detect memory leaks, and advise on optimal batch configurations.
      --- HARD RESTRICTIONS ---
      1. Write reports only into the designated analytics folder.
    defaultAllowedDirectories:
      - "reports/gpu/"
    defaultReadOnlyDirectories:
      - "src/"
      - "infra/"
    preferredHead: "Headless API"
    maxActions: 15

hooks:
  - "on_init"
  - "before_task_execution"
  - "after_task_execution"
  - "on_state_transition"
```

---

## 2. PrismaticPlugin ABC and Data Classes

The interface classes reside in the core repository at [plugin.py](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/plugin.py).

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class PluginContext:
    """
    Context provided to plugins during initialization.
    Contains runtime configurations, shared state databases, and helper clients.
    """
    config: Dict[str, Any]
    db_connection: Any
    state_dir: str
    telemetry_client: Optional[Any] = None
    lock_manager: Optional[Any] = None


@dataclass
class AgentContract:
    """
    Contract defining directory access permissions and action limits for an agent run.
    """
    thread_id: str
    persona_id: str
    allowed_dirs: List[str] = field(default_factory=list)
    read_only_dirs: List[str] = field(default_factory=list)
    max_actions: int = 10
    execution_env: str = "production"


class PluginValidationError(Exception):
    """Raised when plugin validation or load operation fails."""
    pass


class PrismaticPlugin(ABC):
    """
    Abstract Base Class for all Prismatic Engine plugins.
    Plugins must inherit from this class and implement lifecycle hooks.
    """

    @abstractmethod
    def on_init(self, context: PluginContext) -> None:
        """
        Executed when the core dispatcher initializes.
        Used to set up databases, establish connections, or compile libraries.
        """
        pass

    @abstractmethod
    def register_tools(self) -> List[Dict[str, Any]]:
        """
        Return a list of tool definitions to append to agent contexts.
        Tool definitions must comply with the OpenAI/JSON Schema format.
        """
        return []

    def before_task_execution(self, contract: AgentContract) -> None:
        """
        Called immediately before an agent worker is spawned.
        Allows setting up lock variables, temporary files, or environment constraints.
        """
        pass

    def after_task_execution(self, contract: AgentContract, result: Dict[str, Any]) -> None:
        """
        Called immediately after an agent worker exits.
        Enables cleanup, log collection, or metrics collection.
        """
        pass

    def on_state_transition(self, issue_id: str, from_state: str, to_state: str) -> None:
        """
        Triggered when a Linear ticket or task changes status.
        Useful for syncing observability logs or archiving build outputs.
        """
        pass
```

---

## 3. Plugin Loader Design

The dynamic plugin loader is implemented within [registry.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/registry.py). It scans, validates, imports, and exposes plugins to the event loop.

```python
import os
import sys
import yaml
import importlib
import logging
from pathlib import Path
from typing import Dict, List, Any
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from prismatic.interface.plugin import (
    PluginContext, AgentContract, PrismaticPlugin, PluginValidationError
)

logger = logging.getLogger("prismatic.loader")

class PluginLoader:
    """
    Orchestrates discovery, validation, and dynamic loading of Prismatic plugins
    from the target plugin directory.
    """
    def __init__(self, core_version: str, plugins_dir: str):
        self.core_version = core_version
        self.plugins_dir = plugins_dir
        self.loaded_plugins: Dict[str, PrismaticPlugin] = {}
        self.registered_personas: Dict[str, Dict[str, Any]] = {}
        self.registered_tools: List[Dict[str, Any]] = []

    def scan_and_load_plugins(self, context: PluginContext) -> None:
        """
        Scans $PRISMATIC_HOME/plugins/ for plugin-manifest.yaml files, 
        validates requirements, and dynamically registers plugins.
        """
        if not os.path.exists(self.plugins_dir):
            logger.warning(f"Plugin directory does not exist: {self.plugins_dir}")
            return

        for entry in os.scandir(self.plugins_dir):
            if not entry.is_dir():
                continue

            manifest_path = Path(entry.path) / "plugin-manifest.yaml"
            if not manifest_path.exists():
                continue

            try:
                self._load_plugin(manifest_path, context)
            except Exception as e:
                logger.error(f"Failed to load plugin from {manifest_path.parent.name}: {e}", exc_info=True)

    def _load_plugin(self, manifest_path: Path, context: PluginContext) -> None:
        with open(manifest_path, 'r') as f:
            manifest = yaml.safe_load(f)

        name = manifest.get("name")
        version = manifest.get("version")
        entry_point = manifest.get("entry_point")
        core_constraint = manifest.get("core_version_constraint")

        if not all([name, version, entry_point, core_constraint]):
            raise PluginValidationError(f"Missing required fields in manifest: {manifest_path}")

        # 1. Core Version Validation
        specifier = SpecifierSet(core_constraint)
        if Version(self.core_version) not in specifier:
            raise PluginValidationError(
                f"Core version '{self.core_version}' does not satisfy constraint '{core_constraint}' "
                f"for plugin '{name}'."
            )

        # 2. Dynamic Import
        module_path, class_name = entry_point.split(":")
        plugin_root = str(manifest_path.parent)
        
        # Inject plugin directory to sys.path to resolve sub-packages
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        try:
            module = importlib.import_module(module_path)
            plugin_class = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise PluginValidationError(f"Failed to import entry point '{entry_point}': {e}") from e

        # Ensure plugin inherits from ABC
        if not issubclass(plugin_class, PrismaticPlugin):
            raise PluginValidationError(f"Class '{class_name}' must inherit from PrismaticPlugin.")

        # 3. Instantiate and Run on_init in Try-Catch Isolation
        plugin_instance = plugin_class()
        try:
            plugin_instance.on_init(context)
        except Exception as e:
            raise PluginValidationError(f"Exception raised during on_init execution: {e}") from e

        # Register plugin instance
        self.loaded_plugins[name] = plugin_instance

        # 4. Register Personas
        for persona in manifest.get("personas", []):
            persona_id = persona.get("id")
            self.registered_personas[persona_id] = persona
            logger.info(f"Registered persona '{persona_id}' from plugin '{name}'")

        # 5. Register Tools
        try:
            tools = plugin_instance.register_tools()
            self.registered_tools.extend(tools)
        except Exception as e:
            logger.error(f"Plugin '{name}' failed to register tools: {e}")

        logger.info(f"Successfully loaded plugin '{name}' (v{version})")

    def execute_hook(self, hook_name: str, *args, **kwargs) -> None:
        """
        Executes a hook across all registered plugins in try-catch isolation.
        Prevents plugin failures from interrupting the dispatcher event loop.
        """
        for name, plugin in self.loaded_plugins.items():
            if not hasattr(plugin, hook_name):
                continue
            try:
                hook_func = getattr(plugin, hook_name)
                hook_func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Plugin '{name}' failed during hook '{hook_name}': {e}", 
                    exc_info=True
                )
```

---

## 4. Sandboxing Rules

Plugins run under sandbox constraints designed to protect the integrity of the core dispatcher daemon.

### 4.1 Path Validation

Path validation logic resides inside [contracts.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/contracts.py). Every tool exposed by a plugin that interacts with the file system must invoke [validate_path](file:///home/ubuntu/work/prismatic-engine/prismatic/core/contracts.py#L40) before performing any file IO.

```python
import os
from prismatic.interface.plugin import AgentContract

class SecurityException(Exception):
    """Raised when file IO operations violate contract boundaries."""
    pass

def validate_path(target_path: str, contract: AgentContract, read_only: bool = False) -> str:
    """
    Validates that a target file path is within the allowed directories of the AgentContract.
    
    Args:
        target_path: The file path to validate.
        contract: The AgentContract holding directory bounds.
        read_only: If True, checks against allowed_dirs + read_only_dirs.
                   If False, checks strictly against allowed_dirs (write access).
                   
    Returns:
        The normalized absolute path if valid.
        
    Raises:
        SecurityException: If the path is outside the allowed boundaries.
    """
    # 1. Resolve target path (collapses symlinks, relative segments, and relative paths)
    abs_target = os.path.abspath(os.path.realpath(target_path))
    
    # 2. Compile base list of allowed folders
    allowed_bases = [os.path.abspath(os.path.realpath(d)) for d in contract.allowed_dirs]
    if read_only:
        allowed_bases.extend([os.path.abspath(os.path.realpath(d)) for d in contract.read_only_dirs])
        
    # 3. Match directories against bases ensuring no prefix-bypass (e.g. /app/data matching /app/database)
    for base in allowed_bases:
        prefix = base if base.endswith(os.path.sep) else base + os.path.sep
        if abs_target == base or abs_target.startswith(prefix):
            return abs_target
            
    # 4. Raise safety exception
    raise SecurityException(
        f"Security Violation: Access to path '{target_path}' (resolved to '{abs_target}') is blocked. "
        f"Task is restricted to allowed directories: {contract.allowed_dirs}"
        + (f" and read-only directories: {contract.read_only_dirs}" if read_only else "")
    )
```

### 4.2 Security Exceptions on Writes Outside Bounds
When a plugin or an agent tool tries to write to paths outside `contract.allowed_dirs`:
1. The path validator throws a [SecurityException](file:///home/ubuntu/work/prismatic-engine/prismatic/core/contracts.py#L15).
2. The executing tool catches this specific error, logs a security alert containing the violating path, and returns a detailed failure response to the agent context.
3. The dispatcher increments the threat telemetry counter in `BudgetManager`. If security violations exceed a threshold (default: 1), the agent's execution is aborted immediately to prevent further attempts.

### 4.3 Plugin Crash Isolation
- **Hook Isolation:** All hooks called via [PluginLoader.execute_hook](file:///home/ubuntu/work/prismatic-engine/prismatic/core/registry.py#L93) are executed inside isolated `try-except Exception` scopes. A crash in a plugin during `before_task_execution` or `on_state_transition` is logged to the core daemon's error log but does not interrupt other plugins or the [dispatcher.py](file:///home/ubuntu/work/prismatic-engine/prismatic/dispatcher.py) process.
- **Subprocess Isolation:** The actual agent execution occurs in dedicated worker subprocesses. This ensures that any memory leaks, segfaults, or unhandled exceptions arising inside plugin-registered tools running inside the agent's task context will terminate only that specific worker process. The parent dispatcher daemon remains stable, intercepts the exit signal, updates the state database, and marks the task as failed.

---
### Summary of Completed Work

I have designed and specified the complete plugin interface architecture for the Prismatic Engine Core:
1. **[plugin-manifest.yaml](file:///home/ubuntu/work/prismatic-engine/plugins/example_plugin/plugin-manifest.yaml)**: Described all required manifest keys (author, entry_point, core constraint, dependencies, hooks, and full persona definitions) with a validated YAML schema and a production-grade configuration example.
2. **[PrismaticPlugin](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/plugin.py#L40) ABC**: Provided the complete Python abstract base class and typing setup along with [PluginContext](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/plugin.py#L10) and [AgentContract](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/plugin.py#L22) dataclasses.
3. **[PluginLoader](file:///home/ubuntu/work/prismatic-engine/prismatic/core/registry.py#L15)**: Provided the concrete logic to scan the filesystem, enforce version constraints using `packaging`, dynamically import Python modules, and execute hooks in isolation.
4. **Sandboxing & Isolation**: Coded the [validate_path](file:///home/ubuntu/work/prismatic-engine/prismatic/core/contracts.py#L40) function to prevent directory traversal attacks, defined security boundaries, and outlined crash isolation policies for both dispatcher hooks and worker subprocesses.
