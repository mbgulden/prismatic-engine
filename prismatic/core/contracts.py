"""
Contract enforcement — path-boundary validation.

Every tool exposed by a plugin that interacts with the file system must
invoke ``validate_path`` before performing any file I/O.  This function
ensures the target path lies within the ``AgentContract``'s allowed (or
read-only) directory bounds, preventing directory-traversal attacks and
accidental writes outside the task's sandbox.
"""

from __future__ import annotations

import os
from prismatic.interface.plugin import AgentContract


class SecurityException(Exception):
    """Raised when file I/O operations violate contract boundaries."""


def validate_path(
    target_path: str,
    contract: AgentContract,
    read_only: bool = False,
) -> str:
    """
    Validate that *target_path* is within the contract's allowed
    directories.

    Parameters
    ----------
    target_path:
        The file path to validate (relative or absolute).
    contract:
        The ``AgentContract`` carrying allowed and read-only directory
        bounds for the current task.
    read_only:
        If ``True``, the check includes *read_only_dirs* as acceptable
        locations.  If ``False`` (default, write access), only
        *allowed_dirs* are permitted.

    Returns
    -------
    str
        The fully-resolved, absolute, canonical path.

    Raises
    ------
    SecurityException
        If *target_path* resolves outside the permitted boundaries.
    """
    # 1. Resolve → absolute + canonical (symlinks collapsed, ../ handled)
    abs_target = os.path.abspath(os.path.realpath(target_path))

    # 2. Build the base list of permitted folders
    allowed_bases = [
        os.path.abspath(os.path.realpath(d)) for d in contract.allowed_dirs
    ]
    if read_only:
        allowed_bases.extend(
            os.path.abspath(os.path.realpath(d)) for d in contract.read_only_dirs
        )

    # 3. Match — use trailing-sep guard to prevent prefix-bypass attacks
    #    (e.g. /app/data must NOT match /app/database)
    for base in allowed_bases:
        prefix = base if base.endswith(os.path.sep) else base + os.path.sep
        if abs_target == base or abs_target.startswith(prefix):
            return abs_target

    # 4. Raise safety exception
    raise SecurityException(
        f"Security Violation: Access to path '{target_path}' "
        f"(resolved to '{abs_target}') is blocked. "
        f"Task is restricted to allowed directories: {contract.allowed_dirs}"
        + (
            f" and read-only directories: {contract.read_only_dirs}"
            if read_only
            else ""
        )
    )
