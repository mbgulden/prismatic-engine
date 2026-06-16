"""
prismatic/telegram_controls.py — Telegram Inline Controls for Mode Switch
==========================================================================

Generates Telegram inline keyboard markup for mode switching, configuration
panels, and status display. Designed to be imported by the Telegram bot
handler (Jamie or similar) to present rich mode-control interfaces.

Usage
-----
    from prismatic.telegram_controls import (
        build_mode_switch_keyboard,
        build_config_panel_keyboard,
        build_mode_info_markup,
    )

    # Inline keyboard for switching to a specific mode
    kb = build_mode_switch_keyboard(current_mode="collaborative")

    # Configuration panel keyboard
    kb = build_config_panel_keyboard()

    # Rich markup with inline buttons
    text, kb = build_mode_info_markup(current_mode="autonomous")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .agent_cards import AgentCard, get_agent_card, get_all_agent_cards, StatusIndicator
from .state_machine import OrchestrationMode


# ═══════════════════════════════════════════════════════════════
# Telegram API types (inline keyboard structures)
# ═══════════════════════════════════════════════════════════════

@dataclass
class InlineButton:
    """Single Telegram inline keyboard button."""
    text: str
    callback_data: str


@dataclass
class InlineRow:
    """A row of inline buttons."""
    buttons: list[InlineButton] = field(default_factory=list)


@dataclass
class InlineKeyboard:
    """A complete inline keyboard."""
    rows: list[InlineRow] = field(default_factory=list)

    def to_tg_api(self) -> dict[str, Any]:
        """Convert to Telegram Bot API ``reply_markup`` format.

        Returns:
            A dict suitable for the ``reply_markup`` field:
            ``{"inline_keyboard": [[{...}, ...], ...]}``
        """
        keyboard = []
        for row in self.rows:
            kb_row = []
            for btn in row.buttons:
                kb_row.append({
                    "text": btn.text,
                    "callback_data": btn.callback_data,
                })
            keyboard.append(kb_row)
        return {"inline_keyboard": keyboard}

    def to_json(self) -> str:
        """Serialize the keyboard to JSON string."""
        return json.dumps(self.to_tg_api(), ensure_ascii=False)

    def add_row(self, *buttons: InlineButton) -> "InlineKeyboard":
        """Add a row of buttons (fluent API)."""
        self.rows.append(InlineRow(buttons=list(buttons)))
        return self


# ═══════════════════════════════════════════════════════════════
# Callback data constants
# ═══════════════════════════════════════════════════════════════

CALLBACK_MODE_SET = "mode:set"
CALLBACK_MODE_INFO = "mode:info"
CALLBACK_MODE_CONFIG = "mode:config"
CALLBACK_MODE_CONFIRM = "mode:confirm"
CALLBACK_MODE_CANCEL = "mode:cancel"


def _mode_callback(action: str, mode: str) -> str:
    """Build a structured callback_data string."""
    return f"{action}:{mode}"


# ═══════════════════════════════════════════════════════════════
# Mode Switch Keyboard
# ═══════════════════════════════════════════════════════════════

def build_mode_switch_keyboard(
    current_mode: OrchestrationMode | str | None = None,
    *,
    show_info_button: bool = True,
    show_config_button: bool = True,
) -> InlineKeyboard:
    """Build a Telegram inline keyboard for mode switching.

    Each mode gets its own button row with emoji + label. The active
    mode is marked with a checkmark. Extra rows provide info/config
    access.

    Args:
        current_mode: The currently active mode (gets a ✓ marker).
        show_info_button: Include an info row.
        show_config_button: Include a config panel link.

    Returns:
        An InlineKeyboard ready for ``reply_markup``.
    """
    if isinstance(current_mode, str):
        current_mode = OrchestrationMode.from_string(current_mode)

    kb = InlineKeyboard()

    # ── Mode buttons ────────────────────────────────────────
    for card in get_all_agent_cards():
        is_active = current_mode and card.mode == current_mode
        label = f"{'✅ ' if is_active else ''}{card.emoji} {card.label}"
        kb.add_row(
            InlineButton(
                text=label,
                callback_data=_mode_callback(CALLBACK_MODE_SET, card.mode.value),
            )
        )

    # ── Navigation row ───────────────────────────────────────
    nav_buttons = []
    if show_info_button:
        current_val = current_mode.value if current_mode else "collaborative"
        nav_buttons.append(
            InlineButton(
                text="ℹ️ Info",
                callback_data=_mode_callback(CALLBACK_MODE_INFO, current_val),
            )
        )
    if show_config_button:
        nav_buttons.append(
            InlineButton(
                text="⚙️ Config",
                callback_data=CALLBACK_MODE_CONFIG,
            )
        )
    if nav_buttons:
        kb.add_row(*nav_buttons)

    return kb


def build_mode_confirm_keyboard(target_mode: str) -> InlineKeyboard:
    """Build a confirmation keyboard for mode switching.

    Args:
        target_mode: The mode the user is switching to.

    Returns:
        InlineKeyboard with Confirm + Cancel buttons.
    """
    kb = InlineKeyboard()
    kb.add_row(
        InlineButton(
            text=f"✅ Confirm switch to {target_mode.title()}",
            callback_data=_mode_callback(CALLBACK_MODE_CONFIRM, target_mode),
        ),
    )
    kb.add_row(
        InlineButton(
            text="❌ Cancel",
            callback_data=CALLBACK_MODE_CANCEL,
        ),
    )
    return kb


def build_config_panel_keyboard() -> InlineKeyboard:
    """Build a configuration panel keyboard for mode settings.

    Returns:
        InlineKeyboard with config options for all modes.
    """
    kb = InlineKeyboard()
    kb.add_row(
        InlineButton(
            text="📋 View All Modes",
            callback_data=CALLBACK_MODE_INFO + ":all",
        ),
    )
    kb.add_row(
        InlineButton(
            text="🔙 Back to Mode Picker",
            callback_data=CALLBACK_MODE_INFO + ":picker",
        ),
    )
    return kb


# ═══════════════════════════════════════════════════════════════
# Mode Info Markup
# ═══════════════════════════════════════════════════════════════

def build_mode_info_markup(
    current_mode: OrchestrationMode | str,
) -> tuple[str, InlineKeyboard]:
    """Build a rich Telegram message with mode info and inline controls.

    Returns a (text, keyboard) tuple ready for Telegram sendMessage.

    Args:
        current_mode: The currently active mode.

    Returns:
        (markdown_text, InlineKeyboard) tuple.
    """
    if isinstance(current_mode, str):
        current_mode = OrchestrationMode.from_string(current_mode)

    card = get_agent_card(current_mode)
    text = card.to_telegram_text()
    kb = build_mode_switch_keyboard(current_mode)

    return text, kb


def build_mode_picker_markup(
    current_mode: OrchestrationMode | str,
) -> tuple[str, InlineKeyboard]:
    """Build a compact mode picker with visual bars and controls.

    Args:
        current_mode: The currently active mode.

    Returns:
        (markdown_text, InlineKeyboard) tuple.
    """
    from .agent_cards import render_mode_picker_text

    if isinstance(current_mode, str):
        current_mode = OrchestrationMode.from_string(current_mode)

    text = render_mode_picker_text(current_mode)
    kb = build_mode_switch_keyboard(current_mode)
    return text, kb


# ═══════════════════════════════════════════════════════════════
# Callback Data Parser
# ═══════════════════════════════════════════════════════════════

@dataclass
class ModeCallback:
    """Parsed mode-related callback from a Telegram inline button."""
    action: str                 # e.g. "mode:set", "mode:confirm"
    mode: str | None = None     # e.g. "interactive", "autonomous"

    @classmethod
    def parse(cls, callback_data: str) -> "ModeCallback | None":
        """Parse a callback_data string into a ModeCallback.

        Returns:
            ModeCallback if this is a mode-related callback, None otherwise.
        """
        if not callback_data.startswith("mode:"):
            return None

        parts = callback_data.split(":", 2)
        action = f"{parts[0]}:{parts[1]}" if len(parts) >= 2 else callback_data
        mode = parts[2] if len(parts) >= 3 else None
        return cls(action=action, mode=mode)

    @property
    def is_switch(self) -> bool:
        """True if this callback is requesting a mode switch."""
        return self.action == CALLBACK_MODE_SET

    @property
    def is_confirm(self) -> bool:
        """True if this is a confirmation callback."""
        return self.action == CALLBACK_MODE_CONFIRM

    @property
    def is_cancel(self) -> bool:
        """True if this is a cancellation."""
        return self.action == CALLBACK_MODE_CANCEL


# ═══════════════════════════════════════════════════════════════
# Compact Status Bar Keyboard (for ephemeral status messages)
# ═══════════════════════════════════════════════════════════════

def build_compact_status_keyboard(
    current_mode: OrchestrationMode | str,
) -> InlineKeyboard:
    """Build a single-row compact keyboard showing only mode toggle.

    Designed for inline status messages where screen real estate is
    limited (e.g., pinned channel messages, bot status headers).

    Args:
        current_mode: The currently active mode.

    Returns:
        Single-row InlineKeyboard.
    """
    if isinstance(current_mode, str):
        current_mode = OrchestrationMode.from_string(current_mode)

    card = get_agent_card(current_mode)
    kb = InlineKeyboard()

    # Single row: current mode indicator + quick toggle buttons
    buttons = [
        InlineButton(
            text=f"{card.emoji} {card.short_label}",
            callback_data=_mode_callback(CALLBACK_MODE_INFO, current_mode.value),
        ),
        InlineButton(
            text="⚙️",
            callback_data=CALLBACK_MODE_CONFIG,
        ),
    ]
    kb.add_row(*buttons)
    return kb
