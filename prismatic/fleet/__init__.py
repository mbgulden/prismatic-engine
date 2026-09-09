"""
Prismatic Fleet Management & Automated Session Hygiene Engine.

Provides zero-touch onboarding, health inspection, automated context compression,
and session reset/rotation across multi-profile Hermes agent fleets.
"""

from prismatic.fleet.manager import PrismaticFleetManager, SessionHealth, SessionInfo, ProfileStatus

__all__ = [
    "PrismaticFleetManager",
    "SessionHealth",
    "SessionInfo",
    "ProfileStatus",
]
