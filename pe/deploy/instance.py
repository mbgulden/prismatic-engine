"""Per-instance auth configuration (Portal Phase 1, P0 #7).

Shared by the installer (``python -m pe.deploy.install --auth-provider ...``)
and the ``python -m pe.deploy.instance`` CLI below, which manages the auth
configuration of an *existing* instance without reinstalling::

    python -m pe.deploy.instance show
    python -m pe.deploy.instance set-provider tailnet-only
    python -m pe.deploy.instance grant michael@example.com admin
    python -m pe.deploy.instance revoke michael@example.com

The instance file is ``$PRISMATIC_STATE_DIR/instance.json`` (``~/.prismatic``
by default)::

    {"version": 1,
     "instance_id": "<uuid4 hex>",
     "auth_provider": "cloudflare-access",
     "identity_roles": {"michael@example.com": "admin"}}

``identity_roles`` is the portal's identity->role mapping (viewer / operator /
admin).  It lives only here -- the gateway never hard-codes a provider or a
mapping.

Stdlib only, no work at import time: safe to import from the bare-python
installer.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

STATE_DIR_ENV: Final = "PRISMATIC_STATE_DIR"
INSTANCE_FILE_NAME: Final = "instance.json"
INSTANCE_VERSION: Final = 1

DEFAULT_AUTH_PROVIDER: Final = "cloudflare-access"
PROVIDER_NAMES: Final = (
    "cloudflare-access",
    "basic-auth",
    "tailnet-only",
    "localhost-only",
)
PORTAL_ROLES: Final = frozenset({"viewer", "operator", "admin"})

#: Basic-auth users file, relative to the state dir.
BASIC_AUTH_USERS_RELATIVE: Final = Path("auth") / "basic-auth-users.json"

#: pbkdf2 iteration count for basic-auth password hashes.
_PBKDF2_ITERATIONS: Final = 260_000


class InstanceError(Exception):
    """Invalid instance configuration or unusable state dir."""


@dataclass
class InstanceConfig:
    instance_id: str
    auth_provider: str = DEFAULT_AUTH_PROVIDER
    identity_roles: dict[str, str] = field(default_factory=dict)


def state_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    raw = os.environ.get(STATE_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".prismatic"


def instance_file_path(state: Path) -> Path:
    return state / INSTANCE_FILE_NAME


def _validate(config: InstanceConfig) -> InstanceConfig:
    try:
        uuid.UUID(config.instance_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InstanceError("instance_id must be a uuid") from exc
    if config.auth_provider not in PROVIDER_NAMES:
        raise InstanceError(f"unknown auth provider: {config.auth_provider!r}")
    if not isinstance(config.identity_roles, dict):
        raise InstanceError("identity_roles must be an object")
    for subject, role in config.identity_roles.items():
        if not isinstance(subject, str) or not subject:
            raise InstanceError("identity_roles keys must be non-empty strings")
        if role not in PORTAL_ROLES:
            raise InstanceError(
                f"identity_roles[{subject!r}] has unknown role {role!r}"
            )
    return config


def load(state: Path) -> InstanceConfig | None:
    """Load instance.json; None when absent.  Invalid files raise."""
    path = instance_file_path(state)
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstanceError(f"cannot parse {path}") from exc
    if not isinstance(document, dict) or document.get("version") != INSTANCE_VERSION:
        raise InstanceError(f"unsupported instance file version in {path}")
    return _validate(
        InstanceConfig(
            instance_id=str(document.get("instance_id") or ""),
            auth_provider=str(document.get("auth_provider") or DEFAULT_AUTH_PROVIDER),
            identity_roles=dict(document.get("identity_roles") or {}),
        )
    )


def save(config: InstanceConfig, state: Path) -> Path:
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


def new_config(
    auth_provider: str = DEFAULT_AUTH_PROVIDER,
    identity_roles: dict[str, str] | None = None,
) -> InstanceConfig:
    """Build a fresh validated config (new instance_id)."""
    return _validate(
        InstanceConfig(
            instance_id=uuid.uuid4().hex,
            auth_provider=auth_provider,
            identity_roles=dict(identity_roles or {}),
        )
    )


def ensure(state: Path) -> InstanceConfig:
    """Load the config, creating one (fresh instance_id) when absent."""
    config = load(state)
    if config is not None:
        return config
    config = InstanceConfig(instance_id=uuid.uuid4().hex)
    save(config, state)
    return config


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """pbkdf2_hmac/sha256 password hash.  Returns (salt_hex, hash_hex)."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return salt.hex(), digest.hex()


def write_basic_auth_users(
    users: dict[str, str], state: Path, *, overwrite: bool = False
) -> Path:
    """Write ``auth/basic-auth-users.json`` from {username: password} (0600).

    Plaintext passwords never touch the file: only salt + pbkdf2 hash.
    """
    path = state / BASIC_AUTH_USERS_RELATIVE
    if path.exists() and not overwrite:
        raise InstanceError(f"{path} already exists: refusing to overwrite")
    if not users:
        raise InstanceError("basic-auth needs at least one user")
    records = []
    for username, password in users.items():
        if not username or ":" in username or len(username) > 200:
            raise InstanceError(f"invalid basic-auth username: {username!r}")
        if not password:
            raise InstanceError(f"empty password for basic-auth user {username!r}")
        salt_hex, hash_hex = hash_password(password)
        records.append(
            {"username": username, "salt": salt_hex, "password_hash": hash_hex}
        )
    document = {"version": 1, "users": records}
    path.parent.mkdir(parents=True, exist_ok=True)
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
    # Reject group/world-readable leftovers from an earlier umask race.
    metadata = os.lstat(path)
    if os.name != "nt" and metadata.st_mode & 0o077:
        raise InstanceError(f"{path} is group/world-readable: fix permissions")
    return path


def resolve_password(username: str, users: dict[str, str]) -> str:
    """Password for one basic-auth user: env var (single user) or prompt."""
    if len(users) == 1:
        env_password = os.environ.get("PRISMATIC_BASIC_AUTH_PASSWORD")
        if env_password:
            return env_password
    if not sys.stdin.isatty():
        raise InstanceError(
            f"no password for basic-auth user {username!r}: set "
            "PRISMATIC_BASIC_AUTH_PASSWORD or run interactively"
        )
    password = getpass.getpass(f"password for basic-auth user {username!r}: ")
    if not password:
        raise InstanceError(f"empty password for basic-auth user {username!r}")
    return password


# ---------------------------------------------------------------------------
# CLI: python -m pe.deploy.instance
# ---------------------------------------------------------------------------


def _add_state_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        default=None,
        help="instance state dir (default: $PRISMATIC_STATE_DIR or ~/.prismatic)",
    )


def _cmd_show(args: argparse.Namespace) -> int:
    state = state_dir(args.state_dir)
    config = load(state)
    if config is None:
        print(f"no instance.json in {state} (historical posture: "
              f"{DEFAULT_AUTH_PROVIDER}, no identity mapping)")
        return 0
    print(f"instance_id:   {config.instance_id}")
    print(f"auth_provider: {config.auth_provider}")
    if config.identity_roles:
        print("identity_roles:")
        for subject in sorted(config.identity_roles):
            print(f"  {subject}: {config.identity_roles[subject]}")
    else:
        print("identity_roles: (none)")
    return 0


def _cmd_set_provider(args: argparse.Namespace) -> int:
    if args.provider not in PROVIDER_NAMES:
        print(f"unknown auth provider: {args.provider!r}", file=sys.stderr)
        return 2
    state = state_dir(args.state_dir)
    config = ensure(state)
    config.auth_provider = args.provider
    save(config, state)
    print(f"auth_provider set to {args.provider} in {instance_file_path(state)}")
    return 0


def _cmd_grant(args: argparse.Namespace) -> int:
    role = args.role.strip().lower()
    if role not in PORTAL_ROLES:
        print(f"unknown role: {args.role!r} (viewer/operator/admin)", file=sys.stderr)
        return 2
    subject = args.subject.strip()
    if not subject:
        print("subject must be non-empty", file=sys.stderr)
        return 2
    state = state_dir(args.state_dir)
    config = ensure(state)
    config.identity_roles[subject] = role
    save(config, state)
    print(f"granted {role} to {subject}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    subject = args.subject.strip()
    state = state_dir(args.state_dir)
    config = load(state)
    if config is None or subject not in config.identity_roles:
        print(f"no mapping for {subject!r}", file=sys.stderr)
        return 1
    del config.identity_roles[subject]
    save(config, state)
    print(f"removed mapping for {subject}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pe.deploy.instance",
        description="Manage a Prismatic instance's auth configuration.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print the instance auth configuration")
    _add_state_dir(show)
    show.set_defaults(func=_cmd_show)

    set_provider = sub.add_parser(
        "set-provider", help="change the instance's auth provider"
    )
    _add_state_dir(set_provider)
    set_provider.add_argument("provider", help=f"one of: {', '.join(PROVIDER_NAMES)}")
    set_provider.set_defaults(func=_cmd_set_provider)

    grant = sub.add_parser(
        "grant", help="map a provider identity subject to a portal role"
    )
    _add_state_dir(grant)
    grant.add_argument("subject", help="identity subject (email, username, ...)")
    grant.add_argument("role", help="viewer, operator, or admin")
    grant.set_defaults(func=_cmd_grant)

    revoke = sub.add_parser("revoke", help="remove an identity->role mapping")
    _add_state_dir(revoke)
    revoke.add_argument("subject", help="identity subject")
    revoke.set_defaults(func=_cmd_revoke)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except InstanceError as exc:
        print(f"instance: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
