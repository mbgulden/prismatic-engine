"""Import shim for the v0.2 closeout skill.

The real skill directory is hyphenated (``prismatic-agent-closeout-contract``)
following the Prismatic skill discovery convention. Python module names must
be underscored; this package re-exports the validator module by running its
file path. The discovery tools see the hyphenated directory; runtime
imports see this underscored package.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import sys as _sys
from pathlib import Path as _Path

_PKG_DIR = _Path(__file__).resolve().parent
_VALIDATOR_PATH = (
    _PKG_DIR.parent
    / "prismatic-agent-closeout-contract"
    / "scripts"
    / "validate_closeout_packet.py"
)

_spec = _importlib_util.spec_from_file_location(
    "prismatic.skills.prismatic_agent_closeout_contract.scripts.validate_closeout_packet",
    _VALIDATOR_PATH,
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load validator from {_VALIDATOR_PATH}")
_module = _importlib_util.module_from_spec(_spec)
_sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)

# Re-export commonly used symbols at the top level.
validate_closeout_packet = _module.validate_closeout_packet
validate_packet_directory = _module.validate_packet_directory
load_schema = _module.load_schema
ValidationOutcome = _module.ValidationOutcome
CloseoutValidationError = _module.CloseoutValidationError
LaunchContext = _module.LaunchContext

# Expose the validator module under the underscored dotted paths so callers
# can use any of:
#   from prismatic.skills.prismatic_agent_closeout_contract import (
#       validate_closeout_packet,
#   )
#   from prismatic.skills.prismatic_agent_closeout_contract.scripts
#       import validate_closeout_packet
#   from prismatic.skills.prismatic_agent_closeout_contract.scripts.validate_closeout_packet
#       import validate_closeout_packet
_alias_pkg = "prismatic.skills.prismatic_agent_closeout_contract"
_alias_scripts = f"{_alias_pkg}.scripts"
_alias_mod = f"{_alias_scripts}.validate_closeout_packet"
_sys.modules.setdefault(_alias_pkg, _module)
_sys.modules.setdefault(_alias_scripts, _module)
_sys.modules.setdefault(_alias_mod, _module)
