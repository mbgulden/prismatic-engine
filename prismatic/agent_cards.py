"""
prismatic/agent_cards.py — Agent Cards and Mode Configuration UI
==================================================================

Agent Cards present each orchestration mode (Interactive, Collaborative,
Autonomous) as a rich visual card with emoji, color, status indicators,
capabilities, and warnings. These cards feed into the configuration panel
and Telegram inline controls for mode switching.

Usage
-----
    from prismatic.agent_cards import AgentCard, get_agent_card, render_status_bar

    card = get_agent_card(OrchestrationMode.COLLABORATIVE)
    print(card.to_telegram_text())       # Telegram-formatted summary
    print(card.status_bar())             # Visual progress/status bar
    print(card.to_json())                # JSON for API/config panel

    # Render a status bar for the current mode
    bar = render_status_bar(mode=OrchestrationMode.AUTONOMOUS, width=10)

    # Get all cards (for configuration panel UI)
    all_cards = get_all_agent_cards()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .state_machine import OrchestrationMode


# ═══════════════════════════════════════════════════════════════
# Status Indicator
# ═══════════════════════════════════════════════════════════════

class StatusIndicator(Enum):
    """Visual status indicators for agent card states."""
    ACTIVE = "active"        # Green, currently selected
    AVAILABLE = "available"  # Blue, selectable
    LOCKED = "locked"        # Gray, unavailable
    WARNING = "warning"      # Yellow, caution


# ═══════════════════════════════════════════════════════════════
# Agent Card
# ═══════════════════════════════════════════════════════════════

@dataclass
class AgentCard:
    """Rich visual card representing an orchestration mode.

    Each card includes:
    - Visual identity (emoji, color, label)
    - Status indicators (active/available/locked/warning)
    - Feature capabilities
    - Risk warnings
    - Metadata for UI rendering
    """

    mode: OrchestrationMode

    # Visual identity
    emoji: str
    color_hex: str
    label: str
    short_label: str  # 3-letter abbreviation for compact views

    # Content
    tagline: str
    description: str
    capabilities: list[str]
    warnings: list[str]

    # Status
    indicator: StatusIndicator = StatusIndicator.AVAILABLE

    # Metadata
    order: int = 0  # Display order (0=first)

    def to_json(self) -> dict[str, Any]:
        """Serialize the card to a JSON-compatible dict."""
        return {
            "mode": self.mode.value,
            "emoji": self.emoji,
            "color_hex": self.color_hex,
            "label": self.label,
            "short_label": self.short_label,
            "tagline": self.tagline,
            "description": self.description,
            "capabilities": self.capabilities,
            "warnings": self.warnings,
            "indicator": self.indicator.value,
            "order": self.order,
            "status_bar": self.status_bar(),
        }

    def to_telegram_text(self) -> str:
        """Format the card as Telegram markdown text."""
        lines = [
            f"{self.emoji} *{self.label}*  `{self.short_label}`",
            f"_{self.tagline}_",
            "",
            f"{self.description}",
            "",
            "*Capabilities:*",
        ]
        for cap in self.capabilities:
            lines.append(f"  ✅ {cap}")

        if self.warnings:
            lines.append("")
            lines.append("*Warnings:*")
            for w in self.warnings:
                lines.append(f"  ⚠️ {w}")

        lines.append("")
        lines.append(f"Status: `{self.status_bar()}`")
        return "\n".join(lines)

    def status_bar(self, active: bool = False, width: int = 10) -> str:
        """Render a visual status bar.

        Active modes get a filled bar; inactive get an empty one.

        Args:
            active: If True, the bar is rendered as filled (current mode).
            width: Number of segments in the bar.

        Returns:
            A string like ``████████░░`` (filled) or ``░░░░░░░░░░`` (empty).
        """
        filled = width if active else 0
        return "█" * filled + "░" * (width - filled)

    def capability_summary(self) -> str:
        """One-line capability summary for compact views."""
        count = len(self.capabilities)
        auto_advance = "auto-advance" in " ".join(self.capabilities).lower()
        human_gates = "human gates" if not auto_advance else ""
        parts = [f"{count} capabilities"]
        if human_gates:
            parts.append(human_gates)
        return ", ".join(parts)


# ═══════════════════════════════════════════════════════════════
# Mode → Card definitions
# ═══════════════════════════════════════════════════════════════

_INTERACTIVE_CARD = AgentCard(
    mode=OrchestrationMode.INTERACTIVE,
    emoji="🛡️",
    color_hex="#FF6B6B",
    label="Interactive",
    short_label="INT",
    tagline="Full human control — every step requires approval",
    description=(
        "Every pipeline transition requires explicit human sign-off. "
        "Agents propose but never execute without approval. Best for "
        "production deploys, sensitive operations, and high-risk tasks."
    ),
    capabilities=[
        "Full human approval at every gate",
        "Explicit transition confirmations",
        "Verbose Linear comments",
        "Maximum safety — nothing ships without sign-off",
    ],
    warnings=[
        "Slowest mode — human must be available",
        "Agents cannot auto-advance",
        "Requires active monitoring",
    ],
    order=0,
)

_COLLABORATIVE_CARD = AgentCard(
    mode=OrchestrationMode.COLLABORATIVE,
    emoji="🤝",
    color_hex="#4ECDC4",
    label="Collaborative",
    short_label="COL",
    tagline="Agents execute, humans review at key gates",
    description=(
        "Agents execute autonomously through Decompose, Dispatch, Execute, "
        "Feedback, and Refine. Humans review at Review and Integrate. "
        "The default mode — balances speed with oversight."
    ),
    capabilities=[
        "Autonomous decomposing and dispatching",
        "Agent-driven execution and refinement",
        "Human sign-off at Review and Integrate gates",
        "Up to 3 retries before escalation",
        "Verbose Linear comments for visibility",
    ],
    warnings=[
        "Key decisions require human availability",
        "Agents may proceed while human is away",
        "3-retry limit may escalate quickly on repeated failures",
    ],
    order=1,
)

_AUTONOMOUS_CARD = AgentCard(
    mode=OrchestrationMode.AUTONOMOUS,
    emoji="🚀",
    color_hex="#45B7D1",
    label="Autonomous",
    short_label="AUT",
    tagline="Full auto-pilot — escalate only on persistent failure",
    description=(
        "The entire 7-step pipeline executes without human intervention. "
        "Agents decompose, dispatch, execute, review, and integrate on their "
        "own. Escalation only occurs after 5 consecutive failures or credit "
        "exhaustion. Best for routine, low-risk tasks."
    ),
    capabilities=[
        "End-to-end autonomous pipeline execution",
        "No human approval gates — fully automated",
        "Up to 5 retries before escalation",
        "Silent mode — minimal comments",
        "Maximum throughput for routine tasks",
    ],
    warnings=[
        "No human oversight — errors may propagate",
        "5 retries may delay problem detection",
        "Not suitable for production deploys",
        "Minimal visibility — check logs for issues",
    ],
    order=2,
)

# ── Card lookup ────────────────────────────────────────────────

_AGENT_CARDS: dict[OrchestrationMode, AgentCard] = {
    OrchestrationMode.INTERACTIVE: _INTERACTIVE_CARD,
    OrchestrationMode.COLLABORATIVE: _COLLABORATIVE_CARD,
    OrchestrationMode.AUTONOMOUS: _AUTONOMOUS_CARD,
}


def get_agent_card(mode: OrchestrationMode | str) -> AgentCard:
    """Get the AgentCard for a specific orchestration mode.

    Args:
        mode: OrchestrationMode enum or string name.

    Returns:
        The AgentCard for that mode.

    Raises:
        ValueError: If the mode is unknown.
    """
    if isinstance(mode, str):
        mode = OrchestrationMode.from_string(mode)
    card = _AGENT_CARDS.get(mode)
    if card is None:
        raise ValueError(f"Unknown mode: {mode}")
    return card


def get_all_agent_cards() -> list[AgentCard]:
    """Return all agent cards, ordered by display priority."""
    return sorted(_AGENT_CARDS.values(), key=lambda c: c.order)


def get_active_card(
    active_mode: OrchestrationMode | str,
) -> list[AgentCard]:
    """Return all cards with the active one flagged.

    The active card gets ``indicator=ACTIVE`` and an active status bar.
    All others get ``indicator=AVAILABLE``.

    Args:
        active_mode: The currently selected mode.

    Returns:
        All agent cards ordered by priority, with the active one annotated.
    """
    if isinstance(active_mode, str):
        active_mode = OrchestrationMode.from_string(active_mode)

    cards = []
    for card in sorted(_AGENT_CARDS.values(), key=lambda c: c.order):
        c = AgentCard(
            mode=card.mode,
            emoji=card.emoji,
            color_hex=card.color_hex,
            label=card.label,
            short_label=card.short_label,
            tagline=card.tagline,
            description=card.description,
            capabilities=list(card.capabilities),
            warnings=list(card.warnings),
            indicator=(
                StatusIndicator.ACTIVE
                if card.mode == active_mode
                else StatusIndicator.AVAILABLE
            ),
            order=card.order,
        )
        cards.append(c)
    return cards


# ═══════════════════════════════════════════════════════════════
# Visual Status Bar Renderer
# ═══════════════════════════════════════════════════════════════

def render_status_bar(
    mode: OrchestrationMode | str,
    width: int = 10,
    *,
    filled_char: str = "█",
    empty_char: str = "░",
) -> str:
    """Render a visual status bar for the given mode.

    The bar is always filled — it represents the mode's "power level"
    based on autonomy. Autonomous = full bar, Interactive = 1/3 bar,
    Collaborative = 2/3 bar.

    Args:
        mode: The orchestration mode.
        width: Number of segments in the bar.
        filled_char: Character for filled segments.
        empty_char: Character for empty segments.

    Returns:
        A visual bar string like ``██████░░░░``.
    """
    if isinstance(mode, str):
        mode = OrchestrationMode.from_string(mode)

    # Autonomy level: Interactive=1, Collaborative=2, Autonomous=3
    _autonomy_level: dict[OrchestrationMode, int] = {
        OrchestrationMode.INTERACTIVE: 1,
        OrchestrationMode.COLLABORATIVE: 2,
        OrchestrationMode.AUTONOMOUS: 3,
    }
    level = _autonomy_level.get(mode, 1)
    filled = max(1, int((level / 3) * width))
    return filled_char * filled + empty_char * (width - filled)


def render_mode_picker_text(
    active_mode: OrchestrationMode | str,
    all_cards: list[AgentCard] | None = None,
) -> str:
    """Render a mode picker summary for display or Telegram message.

    Args:
        active_mode: The currently active mode.
        all_cards: Pre-fetched cards (optional, fetches if None).

    Returns:
        A formatted Telegram markdown text with all modes and their bars.
    """
    if isinstance(active_mode, str):
        active_mode = OrchestrationMode.from_string(active_mode)

    cards = all_cards or get_all_agent_cards()

    lines = ["*🎛️ Orchestration Mode Picker*", ""]
    for card in cards:
        is_active = card.mode == active_mode
        marker = "🔵" if is_active else "⚪"
        bar = card.status_bar(active=is_active, width=8)
        lines.append(
            f"{marker} {card.emoji} *{card.label}* `{bar}`"
        )
        lines.append(f"   _{card.tagline}_")
        lines.append("")

    lines.append(f"_Current: {get_agent_card(active_mode).emoji} "
                  f"{get_agent_card(active_mode).label}_")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Mode Comparison Table
# ═══════════════════════════════════════════════════════════════

def render_comparison_table(cards: list[AgentCard] | None = None) -> str:
    """Render a markdown comparison table of all modes.

    Args:
        cards: Pre-fetched cards (optional).

    Returns:
        A GitHub-flavored markdown table.
    """
    cards = cards or get_all_agent_cards()
    header = "| Mode | Emoji | Autonomy | Review Gates | Retries | Comments |"
    sep = "|------|-------|----------|--------------|---------|----------|"

    rows = []
    for card in cards:
        is_auto = card.mode == OrchestrationMode.AUTONOMOUS
        gate_count = len(
            {
                OrchestrationMode.INTERACTIVE: 7,
                OrchestrationMode.COLLABORATIVE: 2,
                OrchestrationMode.AUTONOMOUS: 0,
            }.get(card.mode, 0)
            * ["✓"]
        )
        gate_str = (
            "None" if gate_count == 0
            else f"{gate_count} gates"
        )
        retries = {
            OrchestrationMode.INTERACTIVE: 1,
            OrchestrationMode.COLLABORATIVE: 3,
            OrchestrationMode.AUTONOMOUS: 5,
        }.get(card.mode, 0)
        comments = (
            "Silent" if card.mode == OrchestrationMode.AUTONOMOUS
            else "Verbose"
        )
        rows.append(
            f"| {card.label} | {card.emoji} | "
            f"{'Full' if is_auto else 'Partial'} | {gate_str} | "
            f"{retries} | {comments} |"
        )

    return "\n".join([header, sep] + rows)
