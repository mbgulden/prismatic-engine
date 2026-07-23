from __future__ import annotations

import ast
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.domain import PWPDomainService  # noqa: E402
from plugins.pwp.prismatic_adapter import (  # noqa: E402
    CAPABILITY_ROUTER_CONSUMER,
    PE_PLUGIN_PROTOCOL,
    PWPDesignTokenPlugin,
)


def test_domain_layer_has_no_prismatic_runtime_imports() -> None:
    domain_path = _REPO_ROOT / "plugins" / "pwp" / "domain.py"
    tree = ast.parse(domain_path.read_text(encoding="utf-8"))
    imports = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert not any(
        module == "prismatic" or module.startswith("prismatic.") for module in imports
    )


def test_package_root_defers_adapter_import() -> None:
    package_path = _REPO_ROOT / "plugins" / "pwp" / "__init__.py"
    tree = ast.parse(package_path.read_text(encoding="utf-8"))
    eager_imports = [
        node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)
    ]

    assert "prismatic_adapter" not in eager_imports
    assert (
        PWPDomainService().capability_contract()["plugin_id"]
        == "pwp-design-token-plugin"
    )


def test_adapter_is_the_explicit_pe_boundary() -> None:
    adapter = PWPDesignTokenPlugin(domain=PWPDomainService())
    contract = adapter.connection_contract()

    assert PE_PLUGIN_PROTOCOL == "prismatic.interface.plugin >=0.2.0,<2.0.0"
    assert (
        CAPABILITY_ROUTER_CONSUMER == "prismatic.capability_router (PE-owned consumer)"
    )
    assert contract["pe_plugin_protocol"] == PE_PLUGIN_PROTOCOL
    assert contract["capability_router_consumer"] == CAPABILITY_ROUTER_CONSUMER
    assert {tool["name"] for tool in adapter.register_tools()} == {
        "pwp_credentials_refresh",
        "pwp_credentials_status",
    }
