"""WorkspaceCategorizer for the Curated Workspace Plugin (WA-3).

Rule-based categorizer mapping doc path and frontmatter to canonical categories:
- architecture, decisions, prompts, runbooks, okfs, plugins, governance, research, other
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

ALLOWED_CATEGORIES = {
    "architecture",
    "decisions",
    "prompts",
    "runbooks",
    "okfs",
    "plugins",
    "governance",
    "research",
    "other",
}


class WorkspaceCategorizer:
    """Categorizes documentation entries based on path patterns and frontmatter."""

    @classmethod
    def categorize(
        self,
        rel_path: str,
        frontmatter: Optional[dict[str, Any]] = None,
    ) -> str:
        """Determine category for a document path and optional frontmatter."""
        fm = frontmatter or {}

        # 1. Frontmatter type override if valid
        fm_type = str(fm.get("type", "")).lower()
        if fm_type in ALLOWED_CATEGORIES:
            return fm_type
        if fm_type == "decision" or fm_type == "adr":
            return "decisions"

        p_str = rel_path.replace("\\", "/").lower()
        p_name = Path(rel_path).name.lower()

        # 2. Path pattern rules
        if p_name.startswith("okf-") or "/okfs/" in p_str or "/okf/" in p_str:
            return "okfs"

        if p_name.startswith("adr-") or "/decisions/" in p_str or "decision" in p_name:
            return "decisions"

        if "/architecture/" in p_str or "architecture" in p_name:
            return "architecture"

        if "/runbooks/" in p_str or "runbook" in p_name:
            return "runbooks"

        if "/prompts/" in p_str or "prompt" in p_name:
            return "prompts"

        if "/plugins/" in p_str or "plugin" in p_name:
            return "plugins"

        if "/governance/" in p_str or p_name == "agents.md" or "governance" in p_name:
            return "governance"

        if "/research/" in p_str or "research" in p_name:
            return "research"

        return "other"
