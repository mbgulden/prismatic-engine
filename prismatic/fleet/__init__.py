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
from prismatic.fleet.telegram import (
    DynamicTelegramThrottler,
    TelegramStreamer,
    check_hermes_daemon_collision,
    daemon_collision_guard,
    run_hermes_chat_with_guard,
)

__all__ = [
    "PrismaticFleetManager",
    "SessionHealth",
    "SessionInfo",
    "ProfileStatus",
    "init_sqlite_connection",
    "execute_with_retry",
    "checkpoint_sqlite_database",
    "DynamicTelegramThrottler",
    "TelegramStreamer",
    "check_hermes_daemon_collision",
    "daemon_collision_guard",
    "run_hermes_chat_with_guard",
]
