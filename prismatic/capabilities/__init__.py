"""
Prismatic Engine — Capabilities Package
=======================================

Provides capability registration and contract status checking for the golden flow.
"""
from __future__ import annotations

from .registry import Capability, CapabilityRegistry, registry
from .vcs_github import GitHubCapability
from .chat_agy import ChatAGYCapability

__all__ = [
    "Capability",
    "CapabilityRegistry",
    "registry",
    "GitHubCapability",
    "ChatAGYCapability",
]
