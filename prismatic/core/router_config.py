"""prismatic/core/router_config.py — Runtime-mutable router configuration.

GRO-555: Replace the hardcoded MODEL_PRIORITY_CHAIN with a file-backed,
runtime-mutable config store. The admin API and UI manipulate this
config without requiring code changes or redeploys.

Persistence model
-----------------
- One JSON file: ``prismatic_state/router_config.json``
- Atomic writes (write-temp + rename) to avoid corruption on crash
- File-level lock (``fcntl``) for concurrent writers
- In-memory cache invalidated on every write (TTL of 1s as backstop)

Schema
------
::

    {
        "version": 1,
        "updated_at_iso": "2026-06-26T12:34:56Z",
        "updated_by": "api-key-prefix",
        "routes": {
            "<agent_label>": {
                "priority_chain": ["claude-opus", "claude-sonnet", ...],
                "weight": 1.0,
                "enabled": true,
                "cooldown_seconds": 300
            }
        },
        "models": {
            "<canonical_name>": {
                "agy_model_flag": "Claude Opus 4.6 (Thinking)",
                "tier": "premium",
                "description": "...",
                "enabled": true
            }
        },
        "system": {
            "default_cooldown_seconds": 300,
            "quota_keywords": ["quota exceeded", ...]
        }
    }

The store is read by ``CircuitBreakerRouter`` when initialised; subsequent
mutations are observed on the next ``check_and_route`` call because the
router re-reads the chain on each evaluation.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("prismatic.core.router_config")

# ── Defaults (matches existing prismatic/core/router.py) ──────────────
DEFAULT_STATE_DIR: str = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
DEFAULT_CONFIG_FILENAME: str = "router_config.json"

DEFAULT_COOLDOWN_SECONDS: int = 300

DEFAULT_MODELS: dict[str, dict[str, Any]] = {
    "claude-opus": {
        "agy_model_flag": "Claude Opus 4.6 (Thinking)",
        "tier": "premium",
        "description": "Top-tier premium model — first choice for code generation",
        "enabled": True,
    },
    "claude-sonnet": {
        "agy_model_flag": "Claude Sonnet 4.6 (Thinking)",
        "tier": "premium",
        "description": "Secondary premium model — capable code generation",
        "enabled": True,
    },
    "gemini-3.5-flash": {
        "agy_model_flag": "Gemini 3.5 Flash (Medium)",
        "tier": "fallback",
        "description": "Primary fallback — fast, capable, cost-effective",
        "enabled": True,
    },
    "gemini-3.1-flash-lite": {
        "agy_model_flag": "Gemini 3.1 Pro (Low)",
        "tier": "fallback",
        "description": "Secondary fallback — lower capability but always available",
        "enabled": True,
    },
    "gpt-oss-120b": {
        "agy_model_flag": "GPT-OSS 120B (Medium)",
        "tier": "free",
        "description": "Last-resort — local/open model, no quota constraints",
        "enabled": True,
    },
}

DEFAULT_ROUTES: dict[str, dict[str, Any]] = {
    "agy": {
        "priority_chain": [
            "claude-opus",
            "claude-sonnet",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
            "gpt-oss-120b",
        ],
        "weight": 1.0,
        "enabled": True,
        "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
    },
}

DEFAULT_QUOTA_KEYWORDS: list[str] = [
    "quota exceeded",
    "rate limit",
    "429",
    "RESOURCE_EXHAUSTED",
    "quota_exhausted",
    "too many requests",
    "API rate limit",
    "quota limit reached",
]


# ═══════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════


@dataclass
class ModelEntry:
    canonical_name: str
    agy_model_flag: str
    tier: str = "premium"
    description: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "ModelEntry":
        return cls(
            canonical_name=name,
            agy_model_flag=data.get("agy_model_flag", name),
            tier=data.get("tier", "premium"),
            description=data.get("description", ""),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class RouteEntry:
    agent_label: str
    priority_chain: list[str]
    weight: float = 1.0
    enabled: bool = True
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, label: str, data: dict[str, Any]) -> "RouteEntry":
        chain = data.get("priority_chain") or []
        return cls(
            agent_label=label,
            priority_chain=list(chain),
            weight=float(data.get("weight", 1.0)),
            enabled=bool(data.get("enabled", True)),
            cooldown_seconds=int(data.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)),
        )


@dataclass
class RouterConfig:
    routes: dict[str, RouteEntry] = field(default_factory=dict)
    models: dict[str, ModelEntry] = field(default_factory=dict)
    default_cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
    quota_keywords: list[str] = field(default_factory=list)
    version: int = 1
    updated_at_iso: str = ""
    updated_by: str = ""

    # ── Derived helpers used by CircuitBreakerRouter ─────────────
    def priority_chain(self, agent_label: str) -> list[str]:
        """Return the live priority chain for *agent_label*, or default."""
        route = self.routes.get(agent_label)
        if route and route.enabled:
            # Filter chain through enabled models, preserving order
            return [m for m in route.priority_chain if self._is_model_enabled(m)]
        # Fallback to agy chain if available
        agy = self.routes.get("agy")
        if agy and agy.enabled:
            return [m for m in agy.priority_chain if self._is_model_enabled(m)]
        # Last resort: all enabled models
        return [m for m, e in self.models.items() if e.enabled]

    def _is_model_enabled(self, canonical_name: str) -> bool:
        entry = self.models.get(canonical_name)
        return entry is not None and entry.enabled

    def model_flag(self, canonical_name: str) -> str | None:
        """Return the AGY --model flag for a canonical name, or None."""
        entry = self.models.get(canonical_name)
        return entry.agy_model_flag if entry else None

    # ── Serialization ────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at_iso": self.updated_at_iso,
            "updated_by": self.updated_by,
            "routes": {label: r.to_dict() for label, r in self.routes.items()},
            "models": {name: m.to_dict() for name, m in self.models.items()},
            "system": {
                "default_cooldown_seconds": self.default_cooldown_seconds,
                "quota_keywords": list(self.quota_keywords),
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouterConfig":
        routes_raw = data.get("routes", {}) or {}
        models_raw = data.get("models", {}) or {}
        sys_raw = data.get("system", {}) or {}
        return cls(
            routes={label: RouteEntry.from_dict(label, r) for label, r in routes_raw.items()},
            models={name: ModelEntry.from_dict(name, m) for name, m in models_raw.items()},
            default_cooldown_seconds=int(sys_raw.get("default_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)),
            quota_keywords=list(sys_raw.get("quota_keywords", DEFAULT_QUOTA_KEYWORDS)),
            version=int(data.get("version", 1)),
            updated_at_iso=data.get("updated_at_iso", ""),
            updated_by=data.get("updated_by", ""),
        )

    @classmethod
    def defaults(cls) -> "RouterConfig":
        return cls(
            routes={label: RouteEntry.from_dict(label, r) for label, r in DEFAULT_ROUTES.items()},
            models={name: ModelEntry.from_dict(name, m) for name, m in DEFAULT_MODELS.items()},
            default_cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
            quota_keywords=list(DEFAULT_QUOTA_KEYWORDS),
            version=1,
            updated_at_iso=datetime.now(timezone.utc).isoformat(),
            updated_by="defaults",
        )


# ═══════════════════════════════════════════════════════════════
# File locking (cross-platform)
# ═══════════════════════════════════════════════════════════════


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    """Cross-platform advisory lock. Uses fcntl on POSIX, no-op elsewhere."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            import fcntl  # POSIX only
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            # Windows or other — best-effort, accept race risk
            pass
        yield
    finally:
        try:
            import fcntl  # POSIX only
            fcntl.flock(fd, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        os.close(fd)


# ═══════════════════════════════════════════════════════════════
# Store
# ═══════════════════════════════════════════════════════════════


class RouterConfigStore:
    """File-backed router configuration store with in-memory cache.

    The store reads ``router_config.json`` from ``PRISMATIC_STATE_DIR``
    on first access and writes changes atomically (write-temp + rename).
    Concurrent writers are serialised through an fcntl lock.
    """

    CACHE_TTL_SECONDS: float = 1.0

    def __init__(self, config_path: str | os.PathLike[str] | None = None):
        env_dir = Path(DEFAULT_STATE_DIR)
        env_dir.mkdir(parents=True, exist_ok=True)
        if config_path is None:
            config_path = env_dir / DEFAULT_CONFIG_FILENAME
        self._path = Path(config_path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._cache: RouterConfig | None = None
        self._cache_loaded_at: float = 0.0

    # ── Public API ──────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return self._path

    def load(self, *, force_reload: bool = False) -> RouterConfig:
        """Return the current config, refreshing the cache if stale."""
        now = time.monotonic()
        if (
            force_reload
            or self._cache is None
            or (now - self._cache_loaded_at) > self.CACHE_TTL_SECONDS
        ):
            self._cache = self._read_from_disk()
            self._cache_loaded_at = now
        return self._cache

    def save(self, config: RouterConfig, *, updated_by: str = "") -> RouterConfig:
        """Persist *config* to disk atomically. Returns the saved config."""
        config.version += 1
        config.updated_at_iso = datetime.now(timezone.utc).isoformat()
        if updated_by:
            config.updated_by = updated_by
        with _file_lock(self._lock_path):
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=self._path.name + ".",
                suffix=".tmp",
                dir=str(self._path.parent),
            )
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    json.dump(config.to_dict(), f, indent=2, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._path)
            except Exception:
                # Clean up the temp file on failure
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        # Invalidate cache so next read sees fresh data
        self._cache = config
        self._cache_loaded_at = time.monotonic()
        logger.info(
            "router_config saved: version=%d updated_by=%s",
            config.version,
            config.updated_by,
        )
        return config

    def reset_to_defaults(self, *, updated_by: str = "system") -> RouterConfig:
        """Reset the config to factory defaults and persist."""
        cfg = RouterConfig.defaults()
        cfg.updated_by = updated_by
        return self.save(cfg, updated_by=updated_by)

    # ── Helpers used by the API layer ───────────────────────────
    def upsert_route(self, label: str, route: RouteEntry, *, updated_by: str = "") -> RouterConfig:
        cfg = self.load()
        cfg.routes[label] = route
        return self.save(cfg, updated_by=updated_by)

    def delete_route(self, label: str, *, updated_by: str = "") -> RouterConfig | None:
        cfg = self.load()
        if label not in cfg.routes:
            return None
        del cfg.routes[label]
        return self.save(cfg, updated_by=updated_by)

    def upsert_model(self, name: str, model: ModelEntry, *, updated_by: str = "") -> RouterConfig:
        cfg = self.load()
        cfg.models[name] = model
        return self.save(cfg, updated_by=updated_by)

    def delete_model(self, name: str, *, updated_by: str = "") -> RouterConfig | None:
        cfg = self.load()
        if name not in cfg.models:
            return None
        # Refuse to delete a model that's still referenced by a route
        for route in cfg.routes.values():
            if name in route.priority_chain:
                raise ValueError(
                    f"model {name!r} is referenced by route {route.agent_label!r}; "
                    "remove it from the chain first"
                )
        del cfg.models[name]
        return self.save(cfg, updated_by=updated_by)

    # ── Disk I/O ─────────────────────────────────────────────────
    def _read_from_disk(self) -> RouterConfig:
        if not self._path.exists():
            logger.info("router_config not found at %s; using defaults", self._path)
            return RouterConfig.defaults()
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return RouterConfig.from_dict(data)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "router_config at %s is unreadable (%s); falling back to defaults",
                self._path,
                exc,
            )
            return RouterConfig.defaults()


# ═══════════════════════════════════════════════════════════════
# Module-level singleton
# ═══════════════════════════════════════════════════════════════

_singleton: RouterConfigStore | None = None


def get_router_config_store() -> RouterConfigStore:
    """Return the process-wide RouterConfigStore singleton."""
    global _singleton
    if _singleton is None:
        _singleton = RouterConfigStore()
    return _singleton


def reset_router_config_store() -> None:
    """Drop the singleton (test helper)."""
    global _singleton
    _singleton = None
