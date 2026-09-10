"""
Prismatic Fleet Management & Automated Session Hygiene Engine.

Provides zero-touch onboarding, health inspection, automated context compression,
and session reset/rotation across multi-profile Hermes agent fleets.
"""

from prismatic.fleet.db import (
    checkpoint_sqlite_database,
    execute_with_retry,
    init_sqlite_connection,
)
from prismatic.fleet.manager import (
    PrismaticFleetManager,
    ProfileStatus,
    SessionHealth,
    SessionInfo,
)

__all__ = [
    "PrismaticFleetManager",
    "SessionHealth",
    "SessionInfo",
    "ProfileStatus",
    "init_sqlite_connection",
    "execute_with_retry",
    "checkpoint_sqlite_database",
]
