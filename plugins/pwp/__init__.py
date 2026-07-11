"""PWP theme-system helpers and module contract utilities."""

from .content_guards import (
    DEFAULT_LOCKED_FIELDS,
    DEFAULT_SYSTEM_ROUTING_FIELDS,
    GuardResult,
    GuardViolation,
    LockedFieldViolation,
    assert_safe_emdash_edits,
    guard_emdash_edits,
)
from .module_contracts import (
    ModuleContractError,
    load_module_contracts,
    validate_module_contract,
)
from .module_fixture_renderer import (
    RenderedFixture,
    render_all_fixtures,
    render_fixture,
)

__all__ = [
    "DEFAULT_LOCKED_FIELDS",
    "DEFAULT_SYSTEM_ROUTING_FIELDS",
    "GuardResult",
    "GuardViolation",
    "LockedFieldViolation",
    "ModuleContractError",
    "RenderedFixture",
    "assert_safe_emdash_edits",
    "guard_emdash_edits",
    "load_module_contracts",
    "render_all_fixtures",
    "render_fixture",
    "validate_module_contract",
]
