"""
Credential registry for `prismatic providers attach`.

Harness-agnostic: imports only stdlib, PyYAML, and the engine's GitHub/Linear
provider probes. Writes a single user config file at
``$PRISMATIC_HOME/.prismatic/config.yaml`` with the validated token and any
optional per-provider metadata. Pure stdlib + PyYAML otherwise.
"""

from __future__ import annotations

import os
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

USER_CONFIG_PATH_ENV = "PRISMATIC_USER_CONFIG"


def _default_config_path() -> Path:
    prismatic_home = Path(os.environ.get("PRISMATIC_HOME", os.path.expanduser("~")))
    return prismatic_home / ".prismatic" / "config.yaml"


@dataclass(frozen=True)
class AttachResult:
    ok: bool
    credential_source: str = ""
    error: str = ""
    remediation: str = ""
    path: str = ""


# Probe registry: each entry knows how to validate a credential against the
# provider's API surface. Pure stdlib — no harness imports.
_PROBES: dict[str, Callable[[str], tuple[bool, dict[str, Any]]]] = {}


def register_probe(name: str, probe: Callable[[str], tuple[bool, dict[str, Any]]]) -> None:
    """Register a credential probe for a named provider.

    Probes receive the token and return ``(ok, info)``. ``info`` is the
    raw response payload (or an ``{"error": ...}`` dict on failure).
    """
    _PROBES[name] = probe


def supported_providers() -> list[str]:
    """Return provider names supported by the credential registry."""
    return sorted(_PROBES)


def _github_probe(token: str) -> tuple[bool, dict[str, Any]]:
    """Validate a GitHub token by calling ``GET /user``."""
    if not token:
        return False, {"error": "Empty token"}

    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Prismatic-Engine",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            payload = json.loads(body)
            return True, payload
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            err_body = {"message": exc.reason}
        return False, {"error": f"HTTP {exc.code}", "detail": err_body}
    except Exception as exc:  # pragma: no cover - defensive
        return False, {"error": str(exc)}


# Built-in probe set. Additional providers register their own probes.
register_probe("github", _github_probe)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        with open(path) as fh:
            data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)


class CredentialRegistry:
    """Validate and persist user-supplied provider credentials.

    The registry owns the on-disk user config file and the named
    validation probes. It does not depend on any agent harness.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        env_path = os.environ.get(USER_CONFIG_PATH_ENV)
        if env_path:
            self.config_path = Path(env_path)
        elif config_path is not None:
            self.config_path = Path(config_path)
        else:
            self.config_path = _default_config_path()

    def _validate(self, name: str, token: str) -> tuple[bool, dict[str, Any]]:
        """Run the named provider probe. Override in subclasses for tests."""
        return _PROBES[name](token)

    def attach(
        self,
        name: str,
        *,
        token: str | None = None,
        repository: str | None = None,
        **extra: Any,
    ) -> AttachResult:
        """Validate *token* against the named provider, then persist.

        Returns an :class:`AttachResult`. On success the registry writes
        the token (and optional provider metadata) to the user config
        file so that the next ``prismatic status`` run picks it up via
        the standard provider discovery path.
        """
        if name not in _PROBES:
            return AttachResult(
                ok=False,
                error=f"Unknown provider '{name}'. Supported: {', '.join(sorted(_PROBES))}.",
                remediation="Run `prismatic providers list` to see supported providers.",
            )

        if not token:
            return AttachResult(
                ok=False,
                error="Missing --token.",
                remediation="Pass --token <token> so the registry can validate it.",
            )

        ok, info = self._validate(name, token)
        if not ok:
            message = ""
            detail = info.get("detail") if isinstance(info, dict) else None
            if isinstance(detail, dict):
                message = detail.get("message", "")
            err = info.get("error", "validation failed") if isinstance(info, dict) else "validation failed"
            return AttachResult(
                ok=False,
                error=f"{name} token validation failed: {err}{(' — ' + message) if message else ''}",
                remediation=(
                    "Verify the token is valid, has not expired, and the env var matches the provider."
                ),
            )

        data: dict[str, Any] = _read_yaml(self.config_path)
        existing = data.get(name)
        provider_section: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
        if token is not None:
            provider_section["token"] = token
        if repository is not None and repository.strip():
            provider_section["repository"] = repository.strip()
        if extra:
            for key, value in extra.items():
                if value is not None and value != "":
                    provider_section[key] = value
        data[name] = provider_section
        _write_yaml(self.config_path, data)

        return AttachResult(
            ok=True,
            credential_source=f"config.yaml ({name}.token)",
            path=str(self.config_path),
        )
