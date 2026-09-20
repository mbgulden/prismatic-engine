"""Prismatic skills package.

New structured skills (e.g. ``prismatic-agent-closeout-contract``) ship Python
modules under their subtree and become importable. Legacy skills (``code-review``,
docs-generator``, ``research-synthesizer``) are pure manifest directories and
remain usable via filesystem discovery.

This package directory coexists with the legacy ``prismatic/skills.py`` module
file that defines ``cli_skills``, ``list_skills`` and friends. The package
takes precedence in Python's resolution order, so the names must be
re-exported here for entrypoints (``prismatic-engine-skills``) and direct
imports (``from prismatic.skills import cli_skills``) to keep working.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import sys as _sys
from pathlib import Path as _Path

_PKG_DIR = _Path(__file__).resolve().parent
_PARENT = _PKG_DIR.parent
_MODULE_PATH = _PARENT / "skills.py"

# When both ``prismatic/skills.py`` and ``prismatic/skills/`` exist, the
# package wins. Load the module file under a private alias and re-export
# the public names so legacy entrypoints and import sites still work.
_spec = _importlib_util.spec_from_file_location(
    "prismatic._skills_module_legacy", _MODULE_PATH
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load legacy skills module from {_MODULE_PATH}")
_mod = _importlib_util.module_from_spec(_spec)
_sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

# Public re-exports expected by entrypoints and downstream callers.
cli_skills = _mod.cli_skills
list_skills = _mod.list_skills
skill_info = _mod.skill_info
install_skill = _mod.install_skill
uninstall_skill = _mod.uninstall_skill
create_skill = _mod.create_skill
get_universal_skills_dirs = _mod.get_universal_skills_dirs
upload_skill = _mod.upload_skill
toggle_skill_enabled = _mod.toggle_skill_enabled
delete_skill = _mod.delete_skill
get_disabled_skills = _mod.get_disabled_skills

# Subpackages remain first-class attributes of this package (they live on
# disk under prismatic/skills/<name>/). No extra wiring needed.
