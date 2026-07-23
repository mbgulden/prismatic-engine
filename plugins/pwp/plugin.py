"""Backward-compatible PE adapter import path for PWP.

New standalone consumers should import PWP domain APIs, not this PE adapter.
"""

from .prismatic_adapter import (
    CAPABILITY_ROUTER_CONSUMER,
    PE_PLUGIN_PROTOCOL,
    PWP_CAPABILITY_CONTRACT,
    PWPDesignTokenPlugin,
)

__all__ = [
    "CAPABILITY_ROUTER_CONSUMER",
    "PE_PLUGIN_PROTOCOL",
    "PWP_CAPABILITY_CONTRACT",
    "PWPDesignTokenPlugin",
]
