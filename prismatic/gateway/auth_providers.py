"""Auth-provider abstraction for the Prismatic gateway (Portal Phase 1, P0 #7).

A provider establishes *identity* for an incoming HTTP request.  The portal
then maps identity -> role (viewer/operator/admin) from per-instance
configuration; this module never hard-codes that mapping and never grants
roles itself.

Providers
---------
- ``cloudflare-access``: identity from the Cloudflare Access edge headers
  (``Cf-Access-Authenticated-User-Email``).  The edge verifies the session;
  the portal trusts the headers the edge sets and strips upstream.  No
  Cloudflare API calls are made here by design.
- ``basic-auth``: HTTP Basic credentials verified against a per-instance
  ``auth/basic-auth-users.json`` file (pbkdf2_hmac, per-user salt, mode 0600,
  fail-closed).
- ``tailnet-only``: the peer must arrive on a Tailscale IP; identity is the
  tailnet login resolved via whois when available, otherwise the tailnet IP.
- ``localhost-only``: the peer must be a loopback address; identity is the
  fixed subject ``localhost``.

Only :func:`get_provider` and the :class:`AuthProvider` base class are the
public seam.  Portal API tokens are validated *through* this abstraction (see
``prismatic.gateway.control_auth``), never around it.
"""

from __future__ import annotations

import abc
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final

from fastapi import Request

#: Portal roles an identity may be mapped to (per-instance configuration).
PORTAL_ROLES: Final = frozenset({"viewer", "operator", "admin"})

#: Provider names selectable at install time.
CLOUDFLARE_ACCESS: Final = "cloudflare-access"
BASIC_AUTH: Final = "basic-auth"
TAILNET_ONLY: Final = "tailnet-only"
LOCALHOST_ONLY: Final = "localhost-only"
PROVIDER_NAMES: Final = (CLOUDFLARE_ACCESS, BASIC_AUTH, TAILNET_ONLY, LOCALHOST_ONLY)

#: Default provider: preserves the historical posture (Cloudflare Access at the
#: edge, enforcement via the control credential file) when no instance config
#: exists.
DEFAULT_PROVIDER: Final = CLOUDFLARE_ACCESS

#: Basic-auth users file, relative to the instance state dir.
BASIC_AUTH_USERS_RELATIVE: Final = Path("auth") / "basic-auth-users.json"

#: pbkdf2 iteration count for basic-auth password hashes.
_PBKDF2_ITERATIONS: Final = 260_000


class AuthProviderError(Exception):
    """A provider is misconfigured or its name is unknown (fail closed)."""


@dataclass(frozen=True)
class AuthIdentity:
    """Identity established by a provider for one request."""

    provider: str
    subject: str
    display_name: str


class AuthProvider(abc.ABC):
    """Establishes request identity.  Never grants roles."""

    name: ClassVar[str]

    def __init__(self, state_dir: Path | None = None) -> None:
        self._state_dir = state_dir

    @abc.abstractmethod
    async def authenticate(self, request: Request) -> AuthIdentity | None:
        """Return the request's identity, or None when not established."""

    @property
    def challenge(self) -> str:
        """WWW-Authenticate value used when this provider rejects a request."""
        return "Bearer"


def _client_host(request: Request) -> str | None:
    client = request.client
    if client is None:
        return None
    return client.host or None


class CloudflareAccessProvider(AuthProvider):
    """Identity from Cloudflare Access edge headers.

    Cloudflare Access verifies the visitor session at the edge and injects
    ``Cf-Access-Authenticated-User-Email`` (and the JWT assertion) into the
    request it forwards.  The portal trusts those headers because the edge
    strips any client-supplied copies upstream.  No Cloudflare API calls.
    """

    name: ClassVar[str] = CLOUDFLARE_ACCESS

    async def authenticate(self, request: Request) -> AuthIdentity | None:
        email = request.headers.get("cf-access-authenticated-user-email")
        if not email:
            return None
        email = email.strip().lower()
        if "@" not in email or len(email) > 320:
            return None
        return AuthIdentity(
            provider=self.name, subject=email, display_name=email
        )


def _basic_auth_users_file(state_dir: Path | None) -> Path:
    if state_dir is None:
        raise AuthProviderError("basic-auth provider needs an instance state dir")
    return state_dir / BASIC_AUTH_USERS_RELATIVE


def _read_basic_auth_users(path: Path) -> dict[str, dict[str, str]]:
    """Read and strictly validate the basic-auth users file (fail closed)."""
    try:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise AuthProviderError("basic-auth users file must not be a symlink")
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode):
            raise AuthProviderError("basic-auth users file must be a regular file")
        if os.name != "nt" and metadata.st_mode & 0o077:
            raise AuthProviderError("basic-auth users file must be mode 0600")
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthProviderError("basic-auth users file unreadable") from exc
    if (
        not isinstance(document, dict)
        or document.get("version") != 1
        or not isinstance(document.get("users"), list)
    ):
        raise AuthProviderError("basic-auth users file has an invalid schema")
    users: dict[str, dict[str, str]] = {}
    for entry in document["users"]:
        if not isinstance(entry, dict):
            raise AuthProviderError("basic-auth users file has an invalid schema")
        username = entry.get("username")
        salt_hex = entry.get("salt")
        hash_hex = entry.get("password_hash")
        if (
            not isinstance(username, str)
            or not username.strip()
            or username in users
            or not isinstance(salt_hex, str)
            or not isinstance(hash_hex, str)
        ):
            raise AuthProviderError("basic-auth users file has an invalid schema")
        try:
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
        except ValueError as exc:
            raise AuthProviderError("basic-auth users file has an invalid schema") from exc
        if len(salt) < 16 or len(expected) != 32:
            raise AuthProviderError("basic-auth users file has an invalid schema")
        users[username] = {"salt": salt_hex, "password_hash": hash_hex}
    return users


def hash_basic_auth_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Hash a basic-auth password (pbkdf2_hmac/sha256). Returns (salt_hex, hash_hex)."""
    import secrets

    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


class BasicAuthProvider(AuthProvider):
    """Identity from HTTP Basic credentials checked against the users file."""

    name: ClassVar[str] = BASIC_AUTH

    @property
    def challenge(self) -> str:
        return 'Basic realm="prismatic"'

    async def authenticate(self, request: Request) -> AuthIdentity | None:
        value = request.headers.get("authorization")
        if not value:
            return None
        parts = value.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "basic" or not parts[1]:
            return None
        try:
            decoded = base64.b64decode(parts[1], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
        username, separator, password = decoded.partition(":")
        if not separator or not username:
            return None
        try:
            users = _read_basic_auth_users(_basic_auth_users_file(self._state_dir))
        except AuthProviderError:
            # Missing/misconfigured users file fails closed: nobody authenticates.
            return None
        record = users.get(username)
        if record is None:
            return None
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["password_hash"])
        presented = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
        )
        if not hmac.compare_digest(presented, expected):
            return None
        return AuthIdentity(provider=self.name, subject=username, display_name=username)


class TailnetOnlyProvider(AuthProvider):
    """Identity for peers arriving on Tailscale IPs (VPN-only installs)."""

    name: ClassVar[str] = TAILNET_ONLY

    async def authenticate(self, request: Request) -> AuthIdentity | None:
        host = _client_host(request)
        if not host:
            return None
        try:
            from prismatic.mesh.tailscale import TailscaleMeshClient, is_tailscale_ip
        except Exception:
            return None
        try:
            if not is_tailscale_ip(host):
                return None
        except Exception:
            return None
        login: str | None = None
        display: str | None = None
        try:
            identity = await TailscaleMeshClient().whois(host)
        except Exception:
            identity = None
        if identity is not None:
            login = (identity.user_login or "").strip() or None
            display = (identity.user_display_name or "").strip() or None
        subject = f"tailnet:{login}" if login else f"tailnet:{host}"
        return AuthIdentity(
            provider=self.name, subject=subject, display_name=display or subject
        )


class LocalhostOnlyProvider(AuthProvider):
    """Identity for loopback peers (localhost-only installs)."""

    name: ClassVar[str] = LOCALHOST_ONLY

    async def authenticate(self, request: Request) -> AuthIdentity | None:
        host = _client_host(request)
        if not host:
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return None
        if not address.is_loopback:
            return None
        return AuthIdentity(provider=self.name, subject="localhost", display_name="localhost")


_PROVIDERS: Final = (
    CloudflareAccessProvider,
    BasicAuthProvider,
    TailnetOnlyProvider,
    LocalhostOnlyProvider,
)


def get_provider(name: str, state_dir: Path | None = None) -> AuthProvider:
    """Instantiate the named provider.  Unknown names fail closed."""
    for provider_cls in _PROVIDERS:
        if provider_cls.name == name:
            return provider_cls(state_dir)
    raise AuthProviderError(f"unknown auth provider: {name!r}")
