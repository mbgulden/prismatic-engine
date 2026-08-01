"""WorkspaceTreeWalker for the Curated Workspace Plugin (WA-2).

Walks deployed documentation directories and builds WorkspaceManifestEntry records.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from prismatic.workspace.acceptance import AcceptanceProtocol
from prismatic.workspace.categorize import WorkspaceCategorizer
from prismatic.workspace.manifest import WorkspaceManifestEntry


def default_deployed_docs_root() -> Path:
    """Resolve default deployed releases docs path.

    Priority:
    1. PRISMATIC_DEPLOYED_DOCS_DIR env var
    2. ~/.prismatic/releases/prismatic-engine/docs (resolved via symlink)
    3. ./docs (fallback)
    """
    env_dir = os.environ.get("PRISMATIC_DEPLOYED_DOCS_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    release_dir = Path("~/.prismatic/releases/prismatic-engine/docs").expanduser()
    if release_dir.exists() or release_dir.parent.exists():
        try:
            return release_dir.resolve()
        except Exception:
            return release_dir

    return Path("./docs").resolve()


class WorkspaceTreeWalker:
    """Walks documentation directories to collect curated entries."""

    def __init__(
        self,
        docs_root: Optional[Path] = None,
        acceptance: Optional[AcceptanceProtocol] = None,
        max_depth: int = 10,
    ):
        raw_root = docs_root or default_deployed_docs_root()
        try:
            self.docs_root = raw_root.resolve()
        except Exception:
            self.docs_root = raw_root
        self.acceptance = acceptance or AcceptanceProtocol(docs_root=self.docs_root)
        self.max_depth = max_depth

    def walk(self) -> list[WorkspaceManifestEntry]:
        """Walk docs_root and return list of WorkspaceManifestEntry records (G6: max_depth guarded)."""
        entries: list[WorkspaceManifestEntry] = []
        if not self.docs_root.exists():
            return entries

        for root, dirs, files in os.walk(self.docs_root):
            # Calculate current depth relative to docs_root
            try:
                rel_root = Path(root).relative_to(self.docs_root)
                depth = len(rel_root.parts)
            except ValueError:
                depth = 0

            if depth >= self.max_depth:
                dirs.clear()  # Do not recurse deeper
                continue

            # Exclude node_modules, assets, hidden dirs
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d not in ("node_modules", "assets", "__pycache__", "build", "dist")
            ]

            for file in files:
                if not file.endswith((".md", ".markdown")):
                    continue

                full_path = Path(root) / file
                entry = self._process_file(full_path)
                if entry:
                    entries.append(entry)

        return sorted(entries, key=lambda e: e.path)

    def _process_file(self, full_path: Path) -> Optional[WorkspaceManifestEntry]:
        """Process a single markdown file into a WorkspaceManifestEntry."""
        try:
            rel_path = str(full_path.relative_to(self.docs_root)).replace("\\", "/")
        except ValueError:
            rel_path = full_path.name

        try:
            content = full_path.read_text(encoding="utf-8")
            stat = full_path.stat()
            size_bytes = stat.st_size
            last_modified = Path(full_path).stat().st_mtime
            import datetime

            mtime_iso = datetime.datetime.fromtimestamp(
                last_modified, datetime.timezone.utc
            ).isoformat()
        except Exception:
            return None

        frontmatter, body = AcceptanceProtocol.parse_frontmatter(content)

        # Title: frontmatter.title or H1 heading or filename
        title = frontmatter.get("title")
        if not title:
            title = AcceptanceProtocol._find_h1(body)
        if not title:
            title = full_path.stem.replace("-", " ").replace("_", " ").title()

        # ID: slug from path
        doc_id = frontmatter.get("id") or full_path.stem.lower()

        # Category
        category = WorkspaceCategorizer.categorize(rel_path, frontmatter)

        # Acceptance validation
        acc_dict = self.acceptance.validate(full_path, content)

        # Status
        status = frontmatter.get("status", "accepted")

        # Tags & Linear issue
        tags = frontmatter.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        linear_issue = frontmatter.get("linear_issue")

        return WorkspaceManifestEntry(
            id=doc_id,
            title=title,
            path=rel_path,
            category=category,
            tags=tags,
            last_modified=mtime_iso,
            size_bytes=size_bytes,
            status=status,
            superseded_by=frontmatter.get("superseded_by"),
            acceptance=acc_dict,
            linear_issue=linear_issue,
        )
