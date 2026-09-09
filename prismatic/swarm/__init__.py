"""
prismatic/swarm — Sovereign Multi-Agent Swarm & 7-Step Iterative Loop Engine
=============================================================================

Coordinates full-spectrum manifestation across the 4 archetypes:
1. Software
2. Web Properties (PWP)
3. Commercial Operations
4. Publications & Content
"""

from .archetypes import (
    Archetype,
    ArchetypeContract,
    ArchetypeRegistry,
    ProjectDeliverable,
)
from .loop_runner import (
    SwarmLoopResult,
    SwarmLoopRunner,
    SwarmStepEvent,
    get_deliverable_by_slug,
    load_all_deliverables,
    save_all_deliverables,
)
from .manifest_oahu import generate_active_oahu_deliverable

__all__ = [
    "Archetype",
    "ArchetypeContract",
    "ArchetypeRegistry",
    "ProjectDeliverable",
    "SwarmLoopRunner",
    "SwarmLoopResult",
    "SwarmStepEvent",
    "load_all_deliverables",
    "get_deliverable_by_slug",
    "save_all_deliverables",
    "generate_active_oahu_deliverable",
]
