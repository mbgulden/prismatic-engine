"""Central deploy configuration for the post-merge deploy pipeline (WS1).

Single home for the ``PRISMATIC_*`` env-var convention (previously scattered
across ``pe/deploy/``) plus the multi-repo / multi-account registry that lets
one deploy receiver serve every repo it is configured for.

Multi-repo model
----------------
* The deploy trigger (``.github/workflows/post-merge-deploy.yml``) POSTs a
  ``repository`` field (``${{ github.repository }}``, i.e. ``owner/repo``).
* The receiver routes the trigger to the matching :class:`DeployRepoConfig`.
* ``repository.full_name`` is globally unique across GitHub accounts, so
  routing by ``full_name`` covers repos in N accounts with no per-account
  plumbing. Sender-side secrets are already per-repo via GitHub Actions
  repository secrets (REUSE); the receiver resolves the matching secret via
  :attr:`DeployRepoConfig.hmac_secret_env`, falling back to the shared
  ``DEPLOY_HMAC_SECRET``.

Registry sources (first match wins):

1. ``PRISMATIC_DEPLOY_REPOS_FILE`` -- JSON file mapping ``full_name`` to
   per-repo overrides.
2. ``PRISMATIC_DEPLOY_REPOS`` -- comma-separated ``owner/repo`` list, every
   repo getting defaults.
3. Unset -- the single default repo ``mbgulden/prismatic-engine`` resolving
   to today's exact paths. Byte-identical behavior to before WS1.

Stdlib only -- no ``prismatic`` imports -- so this module stays importable
in the same minimal environments as ``pe.deploy.gateway_redeploy``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (identical to the pre-WS1 hardcoded values)
# ---------------------------------------------------------------------------

#: The single repo the deploy loop served before WS1. Registry default.
DEFAULT_REPO_FULL_NAME = "mbgulden/prismatic-engine"

#: Release/venv directory name prefix for the default repo.
DEFAULT_RELEASE_PREFIX = "prismatic-engine"

#: systemd unit restarted by the gateway redeploy.
DEFAULT_GATEWAY_SERVICE = "prismatic-gateway.service"

#: Gateway HTTP port used by health checks.
DEFAULT_GATEWAY_PORT = 9000

#: pip extras installed into the gateway venv.
DEFAULT_GATEWAY_EXTRAS = "gateway,primitives,verification"

#: Overall timeout for one deploy request (seconds).
DEFAULT_DEPLOY_TIMEOUT_S = 1800

#: Deploy disk-space preflight (GB).
DEFAULT_MIN_FREE_GB = 5.0

#: Strict health endpoints polled after a gateway restart (path, check name).
DEFAULT_HEALTH_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("/health", "gateway_health"),
    ("/api/review-factory/jobs", "review_jobs"),
)

#: Registry env vars.
REPOS_ENV_VAR = "PRISMATIC_DEPLOY_REPOS"
REPOS_FILE_ENV_VAR = "PRISMATIC_DEPLOY_REPOS_FILE"

#: Per-repo override keys accepted in the JSON registry file.
_OVERRIDE_KEYS = frozenset(
    {
        "mirror_dir",
        "release_prefix",
        "target_service",
        "health_endpoints",
        "hmac_secret_env",
        "target_node",
    }
)

_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def sanitize_env_name(full_name: str) -> str:
    """Map ``owner/repo`` to a safe env-var suffix: ``ACME_WIDGETS``."""
    return re.sub(r"[^A-Z0-9]", "_", full_name.upper())


# ---------------------------------------------------------------------------
# Centralized PRISMATIC_* env vars (identical defaults to the pre-WS1 code)
# ---------------------------------------------------------------------------


def state_dir() -> Path:
    """Base state dir: ``$PRISMATIC_STATE_DIR`` or ``~/.prismatic``."""
    raw = os.environ.get("PRISMATIC_STATE_DIR")
    return Path(raw) if raw else Path.home() / ".prismatic"


def versions_dir() -> Path:
    """Base versioned-releases dir: ``$PRISMATIC_VERSIONS_DIR`` or ``~/.prismatic/versions``."""
    env_dir = os.environ.get("PRISMATIC_VERSIONS_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    p = Path("~/.prismatic/versions").expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def release_symlink_path(
    release_prefix: str = DEFAULT_RELEASE_PREFIX,
) -> Path:
    """Release symlink: ``$PRISMATIC_RELEASE_SYMLINK`` or ``~/.prismatic/releases/<prefix>``."""
    env_path = os.environ.get("PRISMATIC_RELEASE_SYMLINK")
    if env_path:
        return Path(env_path).expanduser()
    p = Path(f"~/.prismatic/releases/{release_prefix}").expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def gateway_service() -> str:
    """systemd unit for the live gateway: ``$PRISMATIC_GATEWAY_SERVICE``."""
    return os.environ.get("PRISMATIC_GATEWAY_SERVICE", DEFAULT_GATEWAY_SERVICE)


def gateway_port() -> int:
    """Gateway HTTP port: ``$PRISMATIC_PORT`` (default 9000)."""
    return int(os.environ.get("PRISMATIC_PORT", str(DEFAULT_GATEWAY_PORT)))


def gateway_extras() -> str:
    """pip extras for the gateway venv: ``$PRISMATIC_GATEWAY_EXTRAS``."""
    return os.environ.get("PRISMATIC_GATEWAY_EXTRAS", DEFAULT_GATEWAY_EXTRAS)


def alert_log_path() -> Path:
    """Alert-log path.

    Precedence (mirrors ``AlertRouter``):
      1. ``$PRISMATIC_ALERT_LOG`` when set.
      2. ``$PRISMATIC_STATE_DIR/alerts.log`` when set.
      3. ``~/.prismatic/alerts.log``.
    """
    override = os.environ.get("PRISMATIC_ALERT_LOG")
    if override:
        return Path(override)
    state = os.environ.get("PRISMATIC_STATE_DIR")
    if state:
        return Path(state) / "alerts.log"
    return Path.home() / ".prismatic" / "alerts.log"


def deploy_db_path() -> Path:
    """Deploy-records JSON path.

    Priority:
    1. ``$PRISMATIC_DEPLOY_DB`` env var.
    2. ``~/.prismatic/db/deploy_records.json``.
    3. ``./prismatic_state/deploy_records.json`` (fallback).
    """
    env_path = os.environ.get("PRISMATIC_DEPLOY_DB")
    if env_path:
        return Path(env_path).expanduser()

    db_dir = Path("~/.prismatic/db").expanduser()
    if db_dir.exists() or db_dir.parent.exists():
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / "deploy_records.json"

    fallback = Path("./prismatic_state")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback / "deploy_records.json"


def linear_transitions_db_path() -> Path:
    """Linear-transitions JSON store: ``$PRISMATIC_LINEAR_TRANSITIONS_DB`` or default."""
    env_path = os.environ.get("PRISMATIC_LINEAR_TRANSITIONS_DB")
    if env_path:
        return Path(env_path).expanduser()
    p = Path("~/.prismatic/db/linear_transitions.json").expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def deploy_timeout_s() -> int:
    """Overall per-deploy-request timeout: ``$PRISMATIC_DEPLOY_TIMEOUT_S`` (default 1800)."""
    return int(os.environ.get("PRISMATIC_DEPLOY_TIMEOUT_S", str(DEFAULT_DEPLOY_TIMEOUT_S)))


def min_free_gb() -> float:
    """Deploy disk-space preflight: ``$PRISMATIC_DEPLOY_MIN_FREE_GB`` (default 5.0)."""
    raw = os.environ.get("PRISMATIC_DEPLOY_MIN_FREE_GB", str(DEFAULT_MIN_FREE_GB))
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MIN_FREE_GB


def strict_secrets() -> bool:
    """Whether ``$PRISMATIC_STRICT_SECRETS`` is set (no dev HMAC fallback)."""
    return bool(os.environ.get("PRISMATIC_STRICT_SECRETS"))


def allow_default_hmac() -> bool:
    """Whether ``$PRISMATIC_ALLOW_DEFAULT_HMAC=1`` permits the dev HMAC secret."""
    return os.environ.get("PRISMATIC_ALLOW_DEFAULT_HMAC") == "1"


def deploy_source_repo() -> Path:
    """Resolve the deploy source from ``$PRISMATIC_DEPLOY_SOURCE_REPO``.

    Fail-fast: the receiver never guesses a deploy source from its working
    directory. ``PRISMATIC_DEPLOY_SOURCE_REPO`` remains the explicit source
    for the default repo (per-repo source checkouts are a later workstream).
    """
    raw = os.environ.get("PRISMATIC_DEPLOY_SOURCE_REPO", "").strip()
    if not raw:
        raise RuntimeError(
            "PRISMATIC_DEPLOY_SOURCE_REPO is not set: the deploy receiver "
            "refuses to start without an explicit deploy source repo. "
            "Set it to a git checkout of prismatic-engine."
        )
    return Path(raw).expanduser()


# ---------------------------------------------------------------------------
# Multi-repo registry
# ---------------------------------------------------------------------------


class UnknownRepoError(KeyError):
    """A deploy trigger named a repository that is not in the registry."""


@dataclass(frozen=True)
class DeployRepoConfig:
    """Deploy configuration for one ``owner/repo``.

    ``full_name`` (``owner/repo``) is the routing key: it is globally unique
    across GitHub accounts, so one registry covers repos in N accounts with
    no per-account plumbing.
    """

    full_name: str
    mirror_dir: Path
    release_prefix: str = DEFAULT_RELEASE_PREFIX
    target_service: str = DEFAULT_GATEWAY_SERVICE
    health_endpoints: tuple[tuple[str, str], ...] = DEFAULT_HEALTH_ENDPOINTS
    hmac_secret_env: str = field(default="")
    target_node: str = "local"

    def __post_init__(self) -> None:
        if not self.hmac_secret_env:
            # Per-repo HMAC secret env var, e.g.
            # DEPLOY_HMAC_SECRET_MBGULDEN_PRISMATIC_ENGINE. Falls back to the
            # shared DEPLOY_HMAC_SECRET at verification time.
            object.__setattr__(
                self,
                "hmac_secret_env",
                f"DEPLOY_HMAC_SECRET_{sanitize_env_name(self.full_name)}",
            )
        # Normalize health_endpoints to tuples of (path, name).
        object.__setattr__(
            self,
            "health_endpoints",
            tuple((str(p), str(n)) for p, n in self.health_endpoints),
        )
        object.__setattr__(self, "mirror_dir", Path(self.mirror_dir).expanduser())


def _validate_full_name(full_name: str) -> None:
    if not isinstance(full_name, str) or not _FULL_NAME_RE.match(full_name):
        raise ValueError(
            f"invalid repository full_name {full_name!r}: expected 'owner/repo'"
        )


def _config_for_name(
    full_name: str, overrides: dict[str, Any] | None = None
) -> DeployRepoConfig:
    """Build a repo config from defaults plus optional overrides."""
    _validate_full_name(full_name)
    owner, repo = full_name.split("/")
    ov = dict(overrides or {})
    unknown = set(ov) - _OVERRIDE_KEYS
    if unknown:
        raise ValueError(
            f"unknown per-repo override key(s) for {full_name!r}: "
            f"{sorted(unknown)} (expected one of {sorted(_OVERRIDE_KEYS)})"
        )

    if "mirror_dir" in ov:
        mirror_dir = Path(str(ov["mirror_dir"])).expanduser()
    else:
        mirror_dir = Path.home() / ".prismatic" / "repos" / owner / repo

    health_endpoints: tuple[tuple[str, str], ...] = DEFAULT_HEALTH_ENDPOINTS
    if "health_endpoints" in ov:
        raw_eps = ov["health_endpoints"]
        try:
            health_endpoints = tuple(
                (str(path), str(name)) for path, name in raw_eps
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid health_endpoints for {full_name!r}: expected a list "
                f"of [path, name] pairs: {exc}"
            ) from exc
        if not health_endpoints or any(
            len(ep) != 2 for ep in health_endpoints
        ):
            raise ValueError(
                f"invalid health_endpoints for {full_name!r}: expected a "
                "non-empty list of [path, name] pairs"
            )

    return DeployRepoConfig(
        full_name=full_name,
        mirror_dir=mirror_dir,
        release_prefix=str(ov.get("release_prefix", DEFAULT_RELEASE_PREFIX)),
        # Default target service honors $PRISMATIC_GATEWAY_SERVICE so the
        # registry default matches the pre-WS1 env behavior exactly.
        target_service=str(ov.get("target_service", gateway_service())),
        health_endpoints=health_endpoints,
        hmac_secret_env=str(ov.get("hmac_secret_env", "")),
        target_node=str(ov.get("target_node", "local")),
    )


@dataclass(frozen=True)
class DeployRepoRegistry:
    """Ordered set of repo configs; the first entry is the default repo."""

    repos: tuple[DeployRepoConfig, ...]

    def __post_init__(self) -> None:
        if not self.repos:
            raise ValueError("deploy repo registry must contain at least one repo")
        seen: set[str] = set()
        for repo in self.repos:
            if repo.full_name in seen:
                raise ValueError(
                    f"duplicate repository {repo.full_name!r} in deploy registry"
                )
            seen.add(repo.full_name)

    def get(self, full_name: str) -> DeployRepoConfig:
        """Return the config for ``full_name``; raise :class:`UnknownRepoError`."""
        for repo in self.repos:
            if repo.full_name == full_name:
                return repo
        known = ", ".join(r.full_name for r in self.repos)
        raise UnknownRepoError(
            f"unknown repository {full_name!r}: not in the deploy registry "
            f"(configured: {known})"
        )

    def default(self) -> DeployRepoConfig:
        """The default repo (first registry entry)."""
        return self.repos[0]

    def __contains__(self, full_name: object) -> bool:
        return any(r.full_name == full_name for r in self.repos)

    def __len__(self) -> int:
        return len(self.repos)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.repos)


def _registry_from_file(path: Path) -> DeployRepoRegistry:
    """Load the registry from a JSON file mapping full_name -> overrides."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"{REPOS_FILE_ENV_VAR} points at {path}, which does not exist"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{REPOS_FILE_ENV_VAR} file {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict) or not raw:
        raise ValueError(
            f"{REPOS_FILE_ENV_VAR} file {path} must be a non-empty JSON "
            "object mapping 'owner/repo' to per-repo overrides"
        )
    configs = []
    for full_name, overrides in raw.items():
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict):
            raise ValueError(
                f"overrides for {full_name!r} in {path} must be an object"
            )
        configs.append(_config_for_name(full_name, overrides))
    return DeployRepoRegistry(tuple(configs))


def load_repo_registry() -> DeployRepoRegistry:
    """Load the deploy repo registry.

    Precedence:

    1. ``PRISMATIC_DEPLOY_REPOS_FILE`` -- JSON file: ``full_name`` ->
       per-repo overrides (``mirror_dir``, ``release_prefix``,
       ``target_service``, ``health_endpoints``, ``hmac_secret_env``,
       ``target_node``).
    2. ``PRISMATIC_DEPLOY_REPOS`` -- comma-separated ``owner/repo`` list,
       every repo getting defaults.
    3. Neither set -- the single default repo
       ``mbgulden/prismatic-engine`` resolving to today's exact paths
       (byte-identical behavior to before WS1).

    ``full_name`` (``owner/repo``) is globally unique across GitHub accounts,
    so routing by ``full_name`` already covers N accounts -- no per-account
    plumbing is needed at the receiver level. Per-repo HMAC secrets cover the
    case where different accounts need different signing secrets.
    """
    file_var = os.environ.get(REPOS_FILE_ENV_VAR, "").strip()
    if file_var:
        return _registry_from_file(Path(file_var).expanduser())
    raw = os.environ.get(REPOS_ENV_VAR, "").strip()
    if raw:
        names = [n.strip() for n in raw.split(",") if n.strip()]
        if not names:
            raise ValueError(
                f"{REPOS_ENV_VAR} is set but contains no repository names"
            )
        return DeployRepoRegistry(tuple(_config_for_name(n) for n in names))
    return DeployRepoRegistry((_config_for_name(DEFAULT_REPO_FULL_NAME),))


def default_repo_config() -> DeployRepoConfig:
    """The default repo's config (registry default; today's behavior when unset)."""
    return load_repo_registry().default()


def default_repo_full_name() -> str:
    """The default repo's ``owner/repo`` full name."""
    return default_repo_config().full_name
