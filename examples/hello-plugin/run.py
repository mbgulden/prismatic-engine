"""Run the hello-plugin example.

Validates the manifest with the real validator, imports the entry point the
way PluginLoader does (manifest parent dir on sys.path), fires on_init against
a stub context, and proves the check registered.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

sys.path.insert(0, str(REPO))  # the prismatic package
sys.path.insert(0, str(HERE))  # my_greeter package dir (as the loader does)


def main() -> None:
    from prismatic.plugin_architecture import (
        load_manifest,
        validate_manifest_payload,
    )

    manifest_path = HERE / "my_greeter" / "plugin-manifest.yaml"
    manifest = load_manifest(manifest_path)
    result = validate_manifest_payload(manifest.raw, manifest_path)
    assert not result["errors"], result["errors"]
    name = manifest.to_dict()["name"]
    print(f"manifest valid: {name}")

    module_path, class_name = manifest.to_dict()["entry_point"].split(":")
    module = importlib.import_module(module_path)
    plugin_cls = getattr(module, class_name)
    plugin = plugin_cls()

    registered: dict = {}

    class StubRegistry:
        def register_check(self, check_name: str, fn) -> None:
            registered[check_name] = fn

    context = SimpleNamespace(config={}, review_registry=StubRegistry())
    plugin.on_init(context)
    assert plugin.register_tools() == []
    assert "greeter-check" in registered, "check was not registered"
    print("check registered: greeter-check")
    print("HELLO_PLUGIN_EXAMPLE_OK")


if __name__ == "__main__":
    main()
