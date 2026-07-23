"""PWP domain API.

The Prismatic Engine adapter is intentionally lazy so importing portable PWP
utilities does not require the PE runtime protocol package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .compiler import (
    compile_tokens_to_css,
    get_tokens_for_tenant,
    render_template,
    set_tenant_tokens,
    validate_tokens,
)
from .domain import PWPDomainService

if TYPE_CHECKING:
    from .prismatic_adapter import PWPDesignTokenPlugin


__all__ = [
    "PWPDomainService",
    "PWPDesignTokenPlugin",
    "compile_tokens_to_css",
    "get_tokens_for_tenant",
    "render_template",
    "set_tenant_tokens",
    "validate_tokens",
]


def __getattr__(name: str):
    if name == "PWPDesignTokenPlugin":
        from .prismatic_adapter import PWPDesignTokenPlugin

        return PWPDesignTokenPlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
