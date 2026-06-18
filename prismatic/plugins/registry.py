"""
PluginMarketplaceRegistry — SQLite-backed index of installed Prismatic plugins.

Scans the ``plugins/`` directory for ``plugin-manifest.yaml`` files, extracts
metadata (name, version, author, description, tags, hooks), and stores them in
a local SQLite database.  Exposes list, get, and search operations with
pagination and multi-field filtering.

Usage
-----
.. code-block:: python

    from prismatic.plugins.registry import PluginMarketplaceRegistry

    registry = PluginMarketplaceRegistry(db_path="./plugin_registry.db")
    registry.index_plugins(plugins_dir="./plugins")
    plugins = registry.list_plugins(offset=0, limit=20)
    results = registry.search_plugins(query="observability", tag="gpu")
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from prismatic.interface.plugin import PrismaticPlugin, PluginValidationError

logger = logging.getLogger("prismatic.plugins.registry")


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class PluginInfo:
    """Full metadata record for an installed plugin."""

    name: str
    version: str
    description: str
    author: str
    entry_point: str
    core_version_constraint: str
    dependencies: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    hooks: List[str] = field(default_factory=list)
    personas: List[Dict[str, Any]] = field(default_factory=list)
    manifest_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        return asdict(self)


@dataclass
class SearchResult:
    """Paginated result set from a registry search."""

    items: List[PluginInfo]
    total: int
    offset: int
    limit: int

    @property
    def has_next(self) -> bool:
        return (self.offset + self.limit) < self.total

    @property
    def has_previous(self) -> bool:
        return self.offset > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [p.to_dict() for p in self.items],
            "total": self.total,
            "offset": self.offset,
            "limit": self.limit,
            "has_next": self.has_next,
            "has_previous": self.has_previous,
        }


# ── Registry Implementation ─────────────────────────────────────────────────


class PluginMarketplaceRegistry:
    """SQLite-backed registry of installed Prismatic plugins.

    The registry maintains a local index of plugin metadata extracted from
    ``plugin-manifest.yaml`` files.  It supports full-text search over
    name, author, and description fields, plus filtered queries by tag
    and hook presence.
    """

    # ── Schema ────────────────────────────────────────────────────────────

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS plugins (
        name            TEXT PRIMARY KEY NOT NULL,
        version         TEXT NOT NULL,
        description     TEXT NOT NULL DEFAULT '',
        author          TEXT NOT NULL DEFAULT '',
        entry_point     TEXT NOT NULL DEFAULT '',
        core_version    TEXT NOT NULL DEFAULT '',
        dependencies    TEXT NOT NULL DEFAULT '[]',
        hooks           TEXT NOT NULL DEFAULT '[]',
        personas        TEXT NOT NULL DEFAULT '[]',
        tags            TEXT NOT NULL DEFAULT '',
        manifest_path   TEXT NOT NULL DEFAULT '',
        indexed_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS plugins_fts USING fts5(
        name, author, description,
        content='plugins',
        content_rowid='rowid',
        tokenize='porter unicode61'
    );

    CREATE TRIGGER IF NOT EXISTS plugins_ai AFTER INSERT ON plugins BEGIN
        INSERT INTO plugins_fts(rowid, name, author, description)
        VALUES (new.rowid, new.name, new.author, new.description);
    END;

    CREATE TRIGGER IF NOT EXISTS plugins_ad AFTER DELETE ON plugins BEGIN
        INSERT INTO plugins_fts(plugins_fts, rowid, name, author, description)
        VALUES ('delete', old.rowid, old.name, old.author, old.description);
    END;

    CREATE TRIGGER IF NOT EXISTS plugins_au AFTER UPDATE ON plugins BEGIN
        INSERT INTO plugins_fts(plugins_fts, rowid, name, author, description)
        VALUES ('delete', old.rowid, old.name, old.author, old.description);
        INSERT INTO plugins_fts(rowid, name, author, description)
        VALUES (new.rowid, new.name, new.author, new.description);
    END;
    """

    def __init__(self, db_path: str = "./plugin_registry.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── Connection Management ─────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-safe connection, creating it on first access."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        """Create tables and FTS triggers if they do not exist."""
        self._conn.executescript(self._SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Indexing ──────────────────────────────────────────────────────────

    def index_plugins(self, plugins_dir: str) -> int:
        """Scan *plugins_dir* for ``plugin-manifest.yaml`` files and index.

        Returns the number of plugins indexed (newly inserted or updated).
        """
        conn = self._get_conn()
        indexed_count = 0

        if not os.path.isdir(plugins_dir):
            logger.warning("Plugin directory does not exist: %s", plugins_dir)
            return 0

        for entry in os.scandir(plugins_dir):
            if not entry.is_dir():
                continue

            manifest_path = Path(entry.path) / "plugin-manifest.yaml"
            if not manifest_path.exists():
                continue

            try:
                info = self._parse_manifest(manifest_path)
                self._upsert_plugin(conn, info)
                indexed_count += 1
                logger.info("Indexed plugin '%s' (v%s)", info.name, info.version)
            except (yaml.YAMLError, PluginValidationError, KeyError) as exc:
                logger.warning(
                    "Skipping invalid manifest %s: %s", manifest_path, exc
                )

        conn.commit()
        return indexed_count

    def _parse_manifest(self, manifest_path: Path) -> PluginInfo:
        """Load and validate a ``plugin-manifest.yaml`` file."""
        with open(manifest_path, "r") as fh:
            manifest = yaml.safe_load(fh)

        if not isinstance(manifest, dict):
            raise PluginValidationError(
                f"Manifest is not a mapping: {manifest_path}"
            )

        name = manifest.get("name", "")
        version = manifest.get("version", "")
        description = manifest.get("description", "")
        author = manifest.get("author", "")
        entry_point = manifest.get("entry_point", "")
        core_version = manifest.get("core_version_constraint", "")
        dependencies = manifest.get("dependencies", {}).get("pip", [])
        hooks = manifest.get("hooks", [])
        personas = manifest.get("personas", [])

        if not name:
            raise PluginValidationError(
                f"Manifest missing required 'name' field: {manifest_path}"
            )

        # Extract tags from description keywords or from an explicit field
        tags = manifest.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        return PluginInfo(
            name=name,
            version=version,
            description=description,
            author=author,
            entry_point=entry_point,
            core_version_constraint=core_version,
            dependencies=dependencies if isinstance(dependencies, list) else [],
            tags=tags,
            hooks=hooks if isinstance(hooks, list) else [],
            personas=personas if isinstance(personas, list) else [],
            manifest_path=str(manifest_path),
        )

    def _upsert_plugin(self, conn: sqlite3.Connection, info: PluginInfo) -> None:
        """Insert or update a plugin record in the database."""
        conn.execute(
            """
            INSERT INTO plugins (name, version, description, author, entry_point,
                                 core_version, dependencies, hooks, personas,
                                 tags, manifest_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                version          = excluded.version,
                description      = excluded.description,
                author           = excluded.author,
                entry_point      = excluded.entry_point,
                core_version     = excluded.core_version,
                dependencies     = excluded.dependencies,
                hooks            = excluded.hooks,
                personas         = excluded.personas,
                tags             = excluded.tags,
                manifest_path    = excluded.manifest_path,
                indexed_at       = datetime('now')
            """,
            (
                info.name,
                info.version,
                info.description,
                info.author,
                info.entry_point,
                info.core_version_constraint,
                json_dumps(info.dependencies),
                json_dumps(info.hooks),
                json_dumps(info.personas),
                _tags_to_str(info.tags),
                info.manifest_path,
            ),
        )

    # ── Query Operations ──────────────────────────────────────────────────

    def list_plugins(
        self, offset: int = 0, limit: int = 20
    ) -> SearchResult:
        """Return a paginated list of all indexed plugins."""
        conn = self._get_conn()

        total_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM plugins"
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        rows = conn.execute(
            "SELECT * FROM plugins ORDER BY name ASC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

        items = [self._row_to_plugin(r) for r in rows]
        return SearchResult(items=items, total=total, offset=offset, limit=limit)

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        """Return metadata for a single plugin by *name*, or ``None``."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM plugins WHERE name = ?", (name,)
        ).fetchone()
        return self._row_to_plugin(row) if row else None

    def search_plugins(
        self,
        query: Optional[str] = None,
        tag: Optional[str] = None,
        hook: Optional[str] = None,
        author: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> SearchResult:
        """Search indexed plugins with optional filters.

        Parameters
        ----------
        query : str, optional
            Full-text search over name, author, and description fields.
        tag : str, optional
            Filter by tag (exact match on the tag string column).
        hook : str, optional
            Filter by hook name (plugins whose hooks list contains this value).
        author : str, optional
            Exact-match filter on the author field.
        offset, limit : int
            Pagination parameters.
        """
        conn = self._get_conn()
        conditions: List[str] = []
        params: List[Any] = []

        if query:
            conditions.append(
                "rowid IN (SELECT rowid FROM plugins_fts WHERE plugins_fts MATCH ?)"
            )
            params.append(_sanitise_fts_query(query))

        if tag:
            conditions.append("tags LIKE ?")
            params.append(f"%{tag}%")

        if hook:
            conditions.append("hooks LIKE ?")
            params.append(f"%{hook}%")

        if author:
            conditions.append("author = ?")
            params.append(author)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Count
        count_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM plugins {where_clause}", params
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        # Fetch
        query_params = params + [limit, offset]
        rows = conn.execute(
            f"SELECT * FROM plugins {where_clause} ORDER BY name ASC LIMIT ? OFFSET ?",
            query_params,
        ).fetchall()

        items = [self._row_to_plugin(r) for r in rows]
        return SearchResult(items=items, total=total, offset=offset, limit=limit)

    def reindex(self, plugins_dir: str) -> int:
        """Drop and re-index all plugins from *plugins_dir*.

        Useful when the registry state is stale or plugins have been
        added/removed in bulk.
        """
        conn = self._get_conn()
        conn.execute("DELETE FROM plugins")
        conn.commit()
        return self.index_plugins(plugins_dir)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_plugin(row: sqlite3.Row) -> PluginInfo:
        """Convert a SQLite row to a PluginInfo dataclass."""
        return PluginInfo(
            name=row["name"],
            version=row["version"],
            description=row["description"],
            author=row["author"],
            entry_point=row["entry_point"],
            core_version_constraint=row["core_version"],
            dependencies=json_loads(row["dependencies"], []),
            hooks=json_loads(row["hooks"], []),
            personas=json_loads(row["personas"], []),
            tags=_str_to_tags(row["tags"]),
            manifest_path=row["manifest_path"],
        )


# ── Serialisation Helpers ────────────────────────────────────────────────────


def json_dumps(data: Any) -> str:
    """JSON-serialise *data*, falling back to '[]' on error."""
    import json
    try:
        return json.dumps(data, default=str)
    except (TypeError, ValueError):
        return "[]"


def json_loads(raw: str, default: Any = None) -> Any:
    """JSON-deserialise *raw*, returning *default* on error."""
    import json
    if not raw:
        return default if default is not None else []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def _tags_to_str(tags: List[str]) -> str:
    """Join tag list into a space-separated string for SQLite LIKE matching."""
    return " ".join(tags) if tags else ""


def _str_to_tags(raw: str) -> List[str]:
    """Split a space-separated tag string back into a list."""
    return raw.split() if raw else []


def _sanitise_fts_query(query: str) -> str:
    """Escape FTS5 special characters and append wildcard for prefix search.

    FTS5 special characters: ^, *, ", (, ), +, -, ~, :, AND, OR, NOT, NEAR
    """
    # Strip trailing wildcards (user may have typed them)
    query = query.rstrip("*")
    # Escape common special chars by wrapping in double quotes
    for ch in ('"', '(', ')', '+', '-', '~', ':'):
        query = query.replace(ch, "")
    # Remove FTS5 keywords (case-insensitive)
    tokens = query.split()
    keywords = {"AND", "OR", "NOT", "NEAR"}
    tokens = [t for t in tokens if t.upper() not in keywords]
    if not tokens:
        return ""
    # Join with implicit AND and append * for prefix matching
    return " AND ".join(t + "*" for t in tokens)
