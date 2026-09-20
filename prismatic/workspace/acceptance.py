"""AcceptanceProtocol for the Curated Workspace Plugin (WA-5).

Corresponds to §6 of okf-docs-workspace-deploy-v1.md.
Validates frontmatter, H1 headings, relative Markdown links, and status.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

VALID_STATUSES = {"proposed", "accepted", "deprecated", "superseded"}
GRO_PATTERN = re.compile(r"^GRO-\d+$", re.IGNORECASE)
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


class AcceptanceProtocol:
    """Validates documentation entries against acceptance criteria."""

    def __init__(self, docs_root: Optional[Path] = None):
        self.docs_root = docs_root or Path(".")

    def validate(
        self,
        file_path: Path,
        content: Optional[str] = None,
    ) -> dict[str, Any]:
        """Validate a document file and return an acceptance dict.

        Dict fields:
            passed: bool
            status: str
            failed_reasons: list[str]
            skip_reasons: list[str]
            last_checked_at: str (ISO8601)
        """
        if content is None:
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception as exc:
                return {
                    "passed": False,
                    "status": "failed",
                    "failed_reasons": [f"Could not read file: {exc}"],
                    "skip_reasons": [],
                    "last_checked_at": datetime.now(timezone.utc).isoformat(),
                }

        frontmatter, body = self.parse_frontmatter(content)
        failed_reasons: list[str] = []

        # Check skip_reasons override
        skip_reasons = frontmatter.get("acceptance", {}).get("skip_reasons", [])
        if not isinstance(skip_reasons, list):
            skip_reasons = []

        # 1. Frontmatter present check
        if not frontmatter and "missing-frontmatter" not in skip_reasons:
            failed_reasons.append("Frontmatter missing or invalid YAML")

        # 2. Frontmatter valid check
        status = str(frontmatter.get("status", "accepted")).lower()
        if status not in VALID_STATUSES and "invalid-status" not in skip_reasons:
            failed_reasons.append(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}")

        # 3. H1 heading check
        title = frontmatter.get("title", "")
        h1 = self._find_h1(body)
        if not h1 and not title and "missing-title" not in skip_reasons:
            failed_reasons.append("No H1 heading or frontmatter title present")

        # 4. Broken relative links check
        if "skip-link-check" not in skip_reasons:
            broken_links = self._check_links(file_path, body)
            for link in broken_links:
                failed_reasons.append(f"Broken relative link: {link}")

        # 5. Linear issue linkage check
        linear_issue = frontmatter.get("linear_issue")
        if linear_issue and not GRO_PATTERN.match(str(linear_issue)):
            if "invalid-linear-issue" not in skip_reasons:
                failed_reasons.append(f"Invalid Linear issue format '{linear_issue}'. Expected GRO-XXXX")

        # 6. Deprecated check
        if status == "deprecated" and "deprecated-doc" not in skip_reasons:
            failed_reasons.append("Document is marked as deprecated")

        # 7. Superseded check
        if status == "superseded":
            superseded_by = frontmatter.get("superseded_by")
            if not superseded_by and "missing-replacement" not in skip_reasons:
                failed_reasons.append("Superseded document must specify 'superseded_by'")

        passed = len(failed_reasons) == 0

        return {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "failed_reasons": failed_reasons,
            "skip_reasons": skip_reasons,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        """Extract YAML frontmatter and body content."""
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        try:
            fm = yaml.safe_load(parts[1])
            return fm if isinstance(fm, dict) else {}, parts[2]
        except yaml.YAMLError:
            return {}, content

    @staticmethod
    def _find_h1(content: str) -> Optional[str]:
        """Find the first H1 heading in markdown body."""
        for line in content.splitlines():
            line_s = line.strip()
            if line_s.startswith("# "):
                return line_s[2:].strip()
        return None

    def _check_links(self, file_path: Path, body: str) -> list[str]:
        """Check relative markdown links against file system."""
        broken: list[str] = []
        base_dir = file_path.parent

        for match in LINK_PATTERN.finditer(body):
            url = match.group(2).strip()
            # Ignore absolute URLs, anchors, mailto
            if url.startswith(("http://", "https://", "mailto:", "#")):
                continue

            # Strip anchor from path
            target_path_str = url.split("#")[0]
            if not target_path_str:
                continue

            target_path = (base_dir / target_path_str).resolve()
            # Also check relative to docs_root
            root_target = (self.docs_root / target_path_str).resolve()

            if not (target_path.exists() or root_target.exists()):
                broken.append(url)

        return broken
