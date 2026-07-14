import os
import shutil
import sys
import yaml
import zipfile
import tarfile
import logging
from pathlib import Path
from typing import Any, Dict, List

from prismatic import __version__
from prismatic.core.registry import PluginLoader
from prismatic.interface.plugin import PluginContext
from prismatic.plugins.lifecycle_manager import PluginLifecycleSandboxManager, PluginState

logger = logging.getLogger("prismatic.plugins.cli")

MOCK_REGISTRY = {
    "example-plugin": {
        "name": "example-plugin",
        "version": "1.0.0",
        "description": "Reference example plugin demonstrating the PrismaticPlugin ABC contract",
        "author": "Ned (agent:ned)",
        "entry_point": "example_plugin.plugin:ExamplePlugin",
        "core_version_constraint": ">=0.1.0",
        "code": """
from prismatic.interface.plugin import PrismaticPlugin, PluginContext, AgentContract
from typing import Any, Dict, List

class ExamplePlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        pass
    def register_tools(self) -> List[Dict[str, Any]]:
        return []
"""
    },
    "dummy-plugin": {
        "name": "dummy-plugin",
        "version": "1.0.0",
        "description": "A dummy plugin for integration testing",
        "author": "Test Author",
        "entry_point": "dummy_plugin.plugin:DummyPlugin",
        "core_version_constraint": ">=0.1.0",
        "code": """
from prismatic.interface.plugin import PrismaticPlugin, PluginContext, AgentContract
from typing import Any, Dict, List

class DummyPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        print("DummyPlugin initialized!")
    def register_tools(self) -> List[Dict[str, Any]]:
        return [{"name": "dummy_tool", "description": "A dummy tool", "parameters": {}}]
"""
    },
    "search-synthesizer": {
        "name": "search-synthesizer",
        "version": "1.1.0",
        "description": "Advanced research synthesis and search aggregator plugin",
        "author": "Agy (agent:agy)",
        "entry_point": "search_synthesizer.plugin:SearchSynthesizer",
        "core_version_constraint": ">=0.1.0",
        "code": """
from prismatic.interface.plugin import PrismaticPlugin, PluginContext, AgentContract
from typing import Any, Dict, List

class SearchSynthesizer(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        pass
    def register_tools(self) -> List[Dict[str, Any]]:
        return []
"""
    }
}

def get_plugins_dir() -> Path:
    prismatic_home = os.environ.get("PRISMATIC_HOME") or os.environ.get("HOME") or os.path.expanduser("~")
    home = os.environ.get("PRISMATIC_HOME", os.path.join(prismatic_home, "work"))
    pdir = Path(home) / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir

def print_table(rows: List[List[str]]) -> None:
    if not rows:
        return
    widths = [max(len(str(c)) for c in col) for col in zip(*rows)]
    for i, row in enumerate(rows):
        line = "  ".join(str(c).ljust(w) for c, w in zip(row, widths))
        print(line)
        if i == 0:
            print("  ".join("-" * w for w in widths))

def handle_search(query: str | None) -> None:
    print(f"\n  Marketplace Search results for '{query or ''}'")
    print("  " + "─" * 60)
    
    rows = [["Name", "Version", "Description"]]
    for name, info in MOCK_REGISTRY.items():
        if query is None or query.lower() in name.lower() or query.lower() in info["description"].lower():
            desc = info["description"]
            if len(desc) > 50:
                desc = desc[:47] + "..."
            rows.append([name, info["version"], desc])
            
    print_table(rows)
    print()

def handle_info(name: str) -> None:
    pdir = get_plugins_dir()
    plugin_dir = pdir / name
    manifest = None
    installed = False

    if plugin_dir.is_dir():
        manifest_path = plugin_dir / "plugin-manifest.yaml"
        if manifest_path.is_file():
            try:
                with open(manifest_path, "r") as f:
                    manifest = yaml.safe_load(f)
                installed = True
            except Exception:
                pass

    if not manifest:
        if name in MOCK_REGISTRY:
            manifest = MOCK_REGISTRY[name]
        else:
            print(f"Plugin '{name}' not found locally or in marketplace registry.")
            return

    print(f"\n  Plugin Info: {manifest.get('name')}")
    print("  " + "─" * 40)
    print(f"  Version:         {manifest.get('version')}")
    print(f"  Description:     {manifest.get('description')}")
    print(f"  Author:          {manifest.get('author')}")
    print(f"  Entry Point:     {manifest.get('entry_point')}")
    print(f"  Core Constraint: {manifest.get('core_version_constraint')}")
    print(f"  Installed:       {'Yes' if installed else 'No'}")
    
    if installed:
        mgr = PluginLifecycleSandboxManager()
        status = mgr.get_plugin_status(name)
        print(f"  Status:          {status.get('state', 'STOPPED')}")
        print(f"  Enabled:         {status.get('enabled', True)}")
    print()

def handle_install(path_or_name: str, start: bool, sandbox: bool, force: bool) -> None:
    pdir = get_plugins_dir()
    src_path = Path(path_or_name)
    temp_dir = None
    name = None
    
    print(f"Installing plugin '{path_or_name}'...")
    
    # 1. Locate and unpack plugin
    if src_path.is_dir():
        manifest_path = src_path / "plugin-manifest.yaml"
        if not manifest_path.is_file():
            print(f"Error: {manifest_path} not found.")
            sys.exit(1)
        with open(manifest_path, "r") as f:
            manifest = yaml.safe_load(f)
        name = manifest.get("name")
        if not name:
            print("Error: manifest must specify a 'name'")
            sys.exit(1)
        dest_dir = pdir / name
        if dest_dir.exists():
            if not force:
                print(f"Error: Plugin '{name}' is already installed. Use --force to overwrite.")
                sys.exit(1)
            shutil.rmtree(dest_dir)
        shutil.copytree(src_path, dest_dir)
    elif src_path.is_file():
        import tempfile
        temp_dir = Path(tempfile.mkdtemp())
        try:
            if zipfile.is_zipfile(src_path):
                with zipfile.ZipFile(src_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
            elif src_path.suffix in (".tar", ".gz", ".tgz"):
                with tarfile.open(src_path, 'r:*') as tar_ref:
                    tar_ref.extractall(temp_dir)
            else:
                print(f"Error: Unrecognized archive file format for '{path_or_name}'")
                sys.exit(1)
            
            manifest_path = None
            if (temp_dir / "plugin-manifest.yaml").is_file():
                manifest_path = temp_dir / "plugin-manifest.yaml"
            else:
                for sub in temp_dir.iterdir():
                    if sub.is_dir() and (sub / "plugin-manifest.yaml").is_file():
                        manifest_path = sub / "plugin-manifest.yaml"
                        break
            
            if not manifest_path:
                print("Error: No 'plugin-manifest.yaml' found in archive.")
                sys.exit(1)
                
            with open(manifest_path, "r") as f:
                manifest = yaml.safe_load(f)
            name = manifest.get("name")
            if not name:
                print("Error: manifest must specify a 'name'")
                sys.exit(1)
            
            dest_dir = pdir / name
            if dest_dir.exists():
                if not force:
                    print(f"Error: Plugin '{name}' is already installed. Use --force to overwrite.")
                    sys.exit(1)
                shutil.rmtree(dest_dir)
                
            shutil.copytree(manifest_path.parent, dest_dir)
        finally:
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir)
    else:
        name = path_or_name
        if name not in MOCK_REGISTRY:
            prismatic_home = os.environ.get("PRISMATIC_HOME") or os.environ.get("HOME") or os.path.expanduser("~")
            default_engine_root = os.path.join(prismatic_home, "work/prismatic-engine")
            engine_root = os.environ.get("PRISMATIC_ENGINE_ROOT", default_engine_root)
            bundled_dir = Path(engine_root) / "plugins" / name
            if bundled_dir.is_dir() and (bundled_dir / "plugin-manifest.yaml").is_file():
                dest_dir = pdir / name
                if dest_dir.exists():
                    if not force:
                        print(f"Error: Plugin '{name}' is already installed. Use --force to overwrite.")
                        sys.exit(1)
                    shutil.rmtree(dest_dir)
                shutil.copytree(bundled_dir, dest_dir)
                manifest_path = dest_dir / "plugin-manifest.yaml"
                with open(manifest_path, "r") as f:
                    manifest = yaml.safe_load(f)
            else:
                print(f"Error: Plugin '{name}' not found locally, and not in registry.")
                sys.exit(1)
        else:
            manifest = MOCK_REGISTRY[name]
            dest_dir = pdir / name
            if dest_dir.exists():
                if not force:
                    print(f"Error: Plugin '{name}' is already installed. Use --force to overwrite.")
                    sys.exit(1)
                shutil.rmtree(dest_dir)
            dest_dir.mkdir(parents=True)
            
            manifest_yaml = {
                "schema_version": "1.0.0",
                "name": manifest["name"],
                "version": manifest["version"],
                "description": manifest["description"],
                "author": manifest["author"],
                "entry_point": manifest["entry_point"],
                "core_version_constraint": manifest["core_version_constraint"],
                "dependencies": {"pip": []},
                "personas": [],
                "hooks": ["on_init"]
            }
            with open(dest_dir / "plugin-manifest.yaml", "w") as f:
                yaml.safe_dump(manifest_yaml, f)
            
            module_parts = manifest["entry_point"].split(":")[0].split(".")
            code_dir = dest_dir / "/".join(module_parts[:-1]) if len(module_parts) > 1 else dest_dir
            code_dir.mkdir(parents=True, exist_ok=True)
            with open(code_dir / f"{module_parts[-1]}.py", "w") as f:
                f.write(manifest["code"].strip())
            if len(module_parts) > 1:
                (dest_dir / "__init__.py").touch()
                (code_dir / "__init__.py").touch()

    # 2. Validation & loader registration
    manifest_path = pdir / name / "plugin-manifest.yaml"
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    
    core_constraint = manifest.get("core_version_constraint")
    try:
        specifier = SpecifierSet(core_constraint)
        if Version(__version__) not in specifier:
            msg = (f"Core version '{__version__}' does not satisfy "
                   f"constraint '{core_constraint}' for plugin '{name}'.")
            if not force:
                print(f"Error: {msg}")
                shutil.rmtree(pdir / name)
                sys.exit(1)
            else:
                print(f"Warning: {msg} (ignored due to --force)")
    except Exception as e:
        if not force:
            print(f"Error checking version constraint: {e}")
            shutil.rmtree(pdir / name)
            sys.exit(1)

    ctx = PluginContext(
        config={},
        db_connection=None,
        state_dir=str(Path.home() / ".prismatic" / "state"),
    )
    loader = PluginLoader(core_version=__version__, plugins_dir=str(pdir))
    try:
        loader._load_plugin(manifest_path, ctx)
    except Exception as e:
        print(f"Error registering plugin in PluginLoader: {e}")
        if not force:
            shutil.rmtree(pdir / name)
            sys.exit(1)

    print(f"Successfully installed plugin '{name}' (v{manifest.get('version')}) at {pdir / name}")

    # 3. Optionally start in sandbox
    if start:
        mgr = PluginLifecycleSandboxManager()
        config = {
            "image": manifest.get("image", "python:3.12-slim"),
            "cmd": manifest.get("cmd", ["python", "-c", "import time; time.sleep(3600)"]),
        }
        try:
            print(f"Starting plugin '{name}' in sandbox pod...")
            result = mgr.start_plugin(name, config)
            print(f"Plugin '{name}' started. State: {result.get('state')} (id={result.get('container_id')})")
        except Exception as e:
            print(f"Failed to start plugin in sandbox: {e}")

def handle_update(name: str, path_or_name: str | None, force: bool) -> None:
    pdir = get_plugins_dir()
    plugin_dir = pdir / name
    if not plugin_dir.is_dir():
        print(f"Error: Plugin '{name}' is not installed.")
        sys.exit(1)

    mgr = PluginLifecycleSandboxManager()
    status = mgr.get_plugin_status(name)
    was_running = status.get("state") == "RUNNING"
    config = {}
    if was_running:
        import json
        config_json = status.get("config_json", "{}")
        try:
            config = json.loads(config_json)
        except Exception:
            config = {}
            
        print(f"Stopping plugin '{name}' before update...")
        try:
            mgr.stop_plugin(name, force=True)
        except Exception as e:
            print(f"Warning stopping plugin: {e}")

    install_source = path_or_name or name
    handle_install(install_source, start=False, sandbox=False, force=True)

    if was_running:
        print(f"Restarting plugin '{name}' after update...")
        try:
            result = mgr.start_plugin(name, config)
            print(f"Plugin '{name}' restarted. State: {result.get('state')} (id={result.get('container_id')})")
        except Exception as e:
            print(f"Failed to restart plugin after update: {e}")

def handle_remove(name: str, force: bool) -> None:
    pdir = get_plugins_dir()
    plugin_dir = pdir / name
    
    mgr = PluginLifecycleSandboxManager()
    status = mgr.get_plugin_status(name)
    
    print(f"Removing plugin '{name}'...")
    
    try:
        if status.get("state") != "NOT_FOUND":
            mgr.purge_plugin(name)
    except Exception as e:
        if not force:
            print(f"Error purging sandbox: {e}. Use --force to proceed.")
            sys.exit(1)
        else:
            print(f"Warning: Failed to purge sandbox: {e} (ignored due to --force)")
            
    if plugin_dir.is_dir():
        try:
            shutil.rmtree(plugin_dir)
        except Exception as e:
            if not force:
                print(f"Error deleting plugin directory: {e}. Use --force to proceed.")
                sys.exit(1)
            else:
                print(f"Warning: Failed to delete plugin directory: {e} (ignored due to --force)")
                
    print(f"Successfully removed plugin '{name}'")

def handle_list() -> None:
    pdir = get_plugins_dir()
    mgr = PluginLifecycleSandboxManager()
    
    print("\n  Installed Plugins")
    print("  " + "─" * 70)
    
    rows = [["Name", "Version", "Status", "Enabled", "Path"]]
    
    if pdir.is_dir():
        for sub in sorted(pdir.iterdir()):
            if sub.is_dir():
                manifest_path = sub / "plugin-manifest.yaml"
                if manifest_path.is_file():
                    try:
                        with open(manifest_path, "r") as f:
                            manifest = yaml.safe_load(f)
                        name = manifest.get("name", sub.name)
                        version = manifest.get("version", "unknown")
                        status_info = mgr.get_plugin_status(name)
                        state = status_info.get("state", "STOPPED")
                        enabled = str(status_info.get("enabled", True))
                        rows.append([name, version, state, enabled, str(sub)])
                    except Exception:
                        pass
                        
    print_table(rows)
    print()

def handle_enable(name: str) -> None:
    mgr = PluginLifecycleSandboxManager()
    result = mgr.enable_plugin(name)
    print(f"Plugin '{name}' enabled. Current state: {result.get('state')}")

def handle_disable(name: str) -> None:
    mgr = PluginLifecycleSandboxManager()
    result = mgr.disable_plugin(name)
    print(f"Plugin '{name}' disabled and stopped. Current state: {result.get('state')}")

def handle_logs(name: str) -> None:
    mgr = PluginLifecycleSandboxManager()
    logs = mgr.get_plugin_logs(name)
    print(f"\n--- Logs for plugin '{name}' ---")
    print(logs)
    print("--------------------------------")
