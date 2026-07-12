"""Agent-neutral Prismatic capability context discovery.

This module exposes a tiny packaged discovery layer that lets Hermes, AGY,
OpenClaw, or future runtimes install a managed capability pointer into their
AGENTS.md/soul.md-style context files without hand-copying profile notes.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

MANAGED_BLOCK_START = "<!-- PRISMATIC_AGENT_CONTEXT:START -->"
MANAGED_BLOCK_END = "<!-- PRISMATIC_AGENT_CONTEXT:END -->"


@dataclass(frozen=True)
class AgentContextCard:
    id: str
    title: str
    description: str
    agents: tuple[str, ...]
    command: str
    api: str


_DEFAULT_CARDS: tuple[AgentContextCard, ...] = (
    AgentContextCard(
        id="skills",
        title="Prismatic Skills Registry",
        description="Discover, inspect, install, and uninstall packaged Prismatic Core skills.",
        agents=("hermes", "agy", "openclaw"),
        command="prismatic-engine-skills list",
        api="/api/skills",
    ),
    AgentContextCard(
        id="timeline",
        title="Operational Timeline",
        description="Record auditable governance events and inspect operator timeline history.",
        agents=("hermes", "agy", "openclaw"),
        command="prismatic-timeline list --limit 20",
        api="/api/timeline",
    ),
    AgentContextCard(
        id="agent-context",
        title="Agent Context Install Doc",
        description="Install this managed Prismatic capability block into AGENTS.md or soul.md.",
        agents=("hermes", "agy", "openclaw"),
        command="prismatic-agent-context install-doc AGENTS.md --agent hermes",
        api="/api/agent-context",
    ),
)


def list_context_cards(agent: str | None = None) -> list[dict[str, Any]]:
    """Return context cards visible to *agent*.

    Unknown agent values return the universal cards rather than failing; the
    layer is a discovery hint, not an authorization boundary.
    """
    normalized = (agent or "").strip().lower()
    cards = []
    for card in _DEFAULT_CARDS:
        if not normalized or normalized in card.agents:
            payload = asdict(card)
            payload["agents"] = list(card.agents)
            cards.append(payload)
    return cards


def render_context_lines(agent: str = "hermes") -> str:
    """Render a compact one-block markdown capability pointer."""
    cards = list_context_cards(agent)
    lines = [
        f"Prismatic Engine capabilities for `{agent}`:",
        "",
    ]
    for card in cards:
        lines.append(f"- **{card['title']}** — {card['description']} (`{card['command']}`, `{card['api']}`)")
    return "\n".join(lines).rstrip()


def render_managed_block(agent: str = "hermes") -> str:
    return f"{MANAGED_BLOCK_START}\n{render_context_lines(agent)}\n{MANAGED_BLOCK_END}\n"


def install_context_doc(path: str | Path, *, agent: str = "hermes") -> dict[str, Any]:
    """Install or replace the managed Prismatic context block in *path*.

    Human-authored content outside the managed block is preserved.
    """
    target = Path(path)
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    block = render_managed_block(agent)
    if MANAGED_BLOCK_START in before and MANAGED_BLOCK_END in before:
        prefix, rest = before.split(MANAGED_BLOCK_START, 1)
        _old, suffix = rest.split(MANAGED_BLOCK_END, 1)
        after = prefix.rstrip() + "\n\n" + block + suffix.lstrip("\n")
        action = "updated"
    else:
        separator = "\n\n" if before.strip() else ""
        after = before.rstrip() + separator + block
        action = "installed"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(after, encoding="utf-8")
    return {
        "ok": True,
        "action": action,
        "path": str(target),
        "agent": agent,
        "block_start": MANAGED_BLOCK_START,
        "block_end": MANAGED_BLOCK_END,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prismatic-agent-context")
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--agent", default="hermes")
    line_parser = sub.add_parser("line")
    line_parser.add_argument("--agent", default="hermes")
    install_parser = sub.add_parser("install-doc")
    install_parser.add_argument("path")
    install_parser.add_argument("--agent", default="hermes")
    args = parser.parse_args(argv)

    if args.command == "list":
        print(json.dumps({"source": "prismatic.agent_context", "cards": list_context_cards(args.agent)}, indent=2))
    elif args.command == "line":
        print(render_context_lines(args.agent))
    elif args.command == "install-doc":
        print(json.dumps(install_context_doc(args.path, agent=args.agent), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
