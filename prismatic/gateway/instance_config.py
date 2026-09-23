"""Per-instance configuration for the Prismatic gateway (Portal Phase 1, P0 #7).

The instance file lives at ``$PRISMATIC_STATE_DIR/instance.json`` (falling
back to ``~/.prismatic/instance.json``, mirroring ``pe.deploy.config``)::

    {"version": 1,
     "instance_id": "<uuid4 hex>",
     "auth_provider": "cloudflare-access",
     "identity_roles": {"michael@example.com": "admin"}}

``identity_roles`` maps provider-established identity subjects to portal
roles (``viewer`` / ``operator`` / ``admin``).  It is the only place the
portal's identity->role mapping lives: never hard-coded in the gateway.

Stdlib only, no work at import time -- importable from the installer, the
``pe.deploy.instance`` CLI, and the gateway alike.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

STATE_DIR_ENV: Final = "PRISMATIC_STATE_DIR"
INSTANCE_FILE_NAME: Final = "instance.json"
INSTANCE_VERSION: Final = 1

#: Provider used when no instance file exists: the historical posture
#: (Cloudflare Access at the edge, credential-file enforcement).
DEFAULT_AUTH_PROVIDER: Final = "cloudflare-access"

#: Provider names the installer accepts (mirrors auth_providers.PROVIDER_NAMES
#: without importing fastapi-dependent code from the stdlib-only installer).
PROVIDER_NAMES: Final = (
    "cloudflare-access",
    "basic-auth",
    "tailnet-only",
    "localhost-only",
)

#: Portal roles assignable to identities.
PORTAL_ROLES: Final = frozenset({"viewer", "operator", "admin"})


class InstanceConfigError(Exception):
    """The instance file is missing required data or fails validation."""


def state_dir() -> Path:
    """Base state dir: ``$PRISMATIC_STATE_DIR`` or ``~/.prismatic``."""
    raw = os.environ.get(STATE_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".prismatic"


def instance_file_path(state: Path | None = None) -> Path:
    return (state or state_dir()) / INSTANCE_FILE_NAME


@dataclass
class InstanceConfig:
    """Parsed instance.json."""

    instance_id: str
    auth_provider: str = DEFAULT_AUTH_PROVIDER
    identity_roles: dict[str, str] = field(default_factory=dict)

    def role_for(self, subject: str) -> str | None:
        """Portal role mapped to an identity subject, if any."""
        return self.identity_roles.get(subject)


def _validate(config: InstanceConfig) -> InstanceConfig:
    try:
        uuid.UUID(config.instance_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InstanceConfigError("instance_id must be a uuid") from exc
    if config.auth_provider not in PROVIDER_NAMES:
        raise InstanceConfigError(
            f"unknown auth_provider: {config.auth_provider!r}"
        )
    if not isinstance(config.identity_roles, dict):
        raise InstanceConfigError("identity_roles must be an object")
    for subject, role in config.identity_roles.items():
        if not isinstance(subject, str) or not subject:
            raise InstanceConfigError("identity_roles keys must be non-empty strings")
        if role not in PORTAL_ROLES:
            raise InstanceConfigError(
                f"identity_roles[{subject!r}] has unknown role {role!r}"
            )
    return config


def load_instance_config(state: Path | None = None) -> InstanceConfig:
    """Load instance.json, or return defaults when it does not exist.

    A present-but-invalid file raises :class:`InstanceConfigError` (fail
    closed); a missing file means "historical posture".
    """
    path = instance_file_path(state)
    if not path.exists():
        return InstanceConfig(instance_id="", auth_provider=DEFAULT_AUTH_PROVIDER)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstanceConfigError(f"cannot parse {path}") from exc
    if not isinstance(document, dict) or document.get("version") != INSTANCE_VERSION:
        raise InstanceConfigError(f"unsupported instance file version in {path}")
    return _validate(
        InstanceConfig(
            instance_id=str(document.get("instance_id") or ""),
            auth_provider=str(
                document.get("auth_provider") or DEFAULT_AUTH_PROVIDER
            ),
            identity_roles=dict(document.get("identity_roles") or {}),
        )
    )


def save_instance_config(config: InstanceConfig, state: Path | None = None) -> Path:
    """Validate and atomically write instance.json (mode 0600)."""
    _validate(config)
    path = instance_file_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "version": INSTANCE_VERSION,
        "instance_id": config.instance_id,
        "auth_provider": config.auth_provider,
        "identity_roles": dict(config.identity_roles),
    }
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def ensure_instance_id(state: Path | None = None) -> InstanceConfig:
    """Return the instance config, minting and persisting one when absent.

    Used by the token API so a fresh instance binds its first tokens without a
    reinstall.  Existing instance files are never rewritten by this call.
    """
    path = instance_file_path(state)
    if path.exists():
        return load_instance_config(state)
    config = InstanceConfig(instance_id=uuid.uuid4().hex)
    save_instance_config(config, state)
    return config
