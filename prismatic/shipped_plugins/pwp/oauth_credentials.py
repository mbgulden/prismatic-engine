from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UBERSUGGEST_SCOPE = "profile domain keywords serp backlinks site_audit content"
UBERSUGGEST_TOKEN_ENDPOINT = "https://ubersuggest-mcp.neilpatelapi.com/token"
UBERSUGGEST_MCP_ENDPOINT = "https://ubersuggest-mcp.neilpatelapi.com/mcp"


class CredentialRefreshError(RuntimeError):
    """Raised when a provider credential cannot be refreshed safely."""


@dataclass(frozen=True)
class OAuthProviderConfig:
    name: str
    client_id: str
    token_url: str
    scope: str | None = None
    token_prefix: str | None = None
    min_token_length: int = 40


@dataclass(frozen=True)
class TokenPaths:
    access_token: Path
    refresh_token: Path
    response_json: Path | None = None


@dataclass(frozen=True)
class RefreshResult:
    provider: str
    access_token_length: int
    refresh_token_length: int
    expires_in: int | None
    scope: str | None
    verification: Mapping[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "ok",
            "provider": self.provider,
            "saved_access_len": self.access_token_length,
            "saved_refresh_len": self.refresh_token_length,
            "expires_in": self.expires_in,
            "scope": self.scope,
        }
        if self.verification is not None:
            payload["verified"] = dict(self.verification)
        return payload


PROVIDERS: dict[str, OAuthProviderConfig] = {
    "ubersuggest": OAuthProviderConfig(
        name="ubersuggest",
        client_id="ubersuggest-mcp",
        token_url=UBERSUGGEST_TOKEN_ENDPOINT,
        scope=UBERSUGGEST_SCOPE,
        token_prefix="ubs_oauth2_",
        min_token_length=40,
    ),
}


def default_token_paths(provider: str) -> TokenPaths:
    if provider != "ubersuggest":
        raise CredentialRefreshError(
            f"No default token paths registered for provider: {provider}"
        )
    return TokenPaths(
        access_token=Path(
            os.environ.get("UBERSUGGEST_ACCESS_TOKEN_FILE", "/tmp/ubs_token")
        ),
        refresh_token=Path(
            os.environ.get("UBERSUGGEST_REFRESH_TOKEN_FILE", "/tmp/ubs_refresh")
        ),
        response_json=Path(
            os.environ.get(
                "UBERSUGGEST_REFRESH_RESPONSE_FILE", "/tmp/ubs_refresh_response.json"
            )
        ),
    )


def _read_token(path: Path, label: str) -> str:
    if not path.exists():
        raise CredentialRefreshError(f"{label} token file missing: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise CredentialRefreshError(f"{label} token file is empty: {path}")
    return token


def validate_token_shape(
    token: str, *, label: str, provider: OAuthProviderConfig
) -> None:
    if "..." in token:
        raise CredentialRefreshError(
            f"{label} token contains literal ellipsis; token was display-mangled"
        )
    if len(token) <= provider.min_token_length:
        raise CredentialRefreshError(
            f"{label} token suspiciously short: {len(token)} chars"
        )
    if provider.token_prefix and not token.startswith(provider.token_prefix):
        raise CredentialRefreshError(
            f"{label} token has unexpected prefix: {token[: min(len(token), 12)]!r}"
        )


def _atomic_write(path: Path, value: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _post_form(url: str, data: Mapping[str, str], timeout: float) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise CredentialRefreshError(
            f"Token endpoint HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise CredentialRefreshError(f"Token endpoint request failed: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CredentialRefreshError(
            f"Token endpoint returned non-JSON response: {raw[:200]!r}"
        ) from exc
    return parsed


def refresh_oauth_token(
    provider: OAuthProviderConfig,
    paths: TokenPaths,
    *,
    timeout: float = 30,
    http_post: Callable[[str, Mapping[str, str], float], Mapping[str, Any]]
    | None = None,
    verifier: Callable[[str], Mapping[str, Any]] | None = None,
) -> RefreshResult:
    """Rotate an OAuth access token using a stored refresh token.

    The function never logs or returns token material. It validates token shape,
    writes access+refresh tokens atomically with 0600 permissions, and can run a
    provider-specific verification callback before returning success.
    """

    old_refresh = _read_token(paths.refresh_token, "refresh")
    validate_token_shape(old_refresh, label="refresh", provider=provider)

    post = http_post or _post_form
    response = dict(
        post(
            provider.token_url,
            {
                "grant_type": "refresh_token",
                "client_id": provider.client_id,
                "refresh_token": old_refresh,
            },
            timeout,
        )
    )
    if paths.response_json is not None:
        _atomic_write(
            paths.response_json, json.dumps(response, indent=2, sort_keys=True)
        )

    if "access_token" not in response or "refresh_token" not in response:
        raise CredentialRefreshError(
            f"Token endpoint did not return access+refresh tokens: {response}"
        )

    access = str(response["access_token"])
    refresh = str(response["refresh_token"])
    validate_token_shape(access, label="access", provider=provider)
    validate_token_shape(refresh, label="refresh", provider=provider)

    _atomic_write(paths.access_token, access)
    _atomic_write(paths.refresh_token, refresh)

    verification = verifier(access) if verifier is not None else None
    return RefreshResult(
        provider=provider.name,
        access_token_length=len(access),
        refresh_token_length=len(refresh),
        expires_in=response.get("expires_in"),
        scope=response.get("scope"),
        verification=verification,
    )


def verify_ubersuggest_mcp(access_token: str) -> Mapping[str, Any]:
    """Verify Ubersuggest MCP access with auth_status + domain_overview.

    Importing MCP is intentionally lazy so validation/unit tests do not require
    the optional dependency unless live verification is requested.
    """

    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def _run() -> dict[str, Any]:
        async with streamablehttp_client(
            UBERSUGGEST_MCP_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as (read, write, _), ClientSession(read, write) as session:
            await session.initialize()
            auth = await session.call_tool("auth_status", {})
            overview = await session.call_tool(
                "domain_overview",
                {"domain": "activeoahutours.com"},
            )
            auth_text = getattr(auth.content[0], "text", "") if auth.content else ""
            overview_text = (
                getattr(overview.content[0], "text", "{}")
                if overview.content
                else "{}"
            )
            overview_data = json.loads(overview_text)
            return {
                "auth_status": auth_text,
                "organic": overview_data.get("organic"),
                "domainAuthority": overview_data.get("domainAuthority"),
            }

    return asyncio.run(_run())


def _cmd_refresh(args: argparse.Namespace) -> int:
    provider = PROVIDERS[args.provider]
    paths = TokenPaths(
        access_token=Path(args.access_token_file)
        if args.access_token_file
        else default_token_paths(args.provider).access_token,
        refresh_token=Path(args.refresh_token_file)
        if args.refresh_token_file
        else default_token_paths(args.provider).refresh_token,
        response_json=Path(args.response_file)
        if args.response_file
        else default_token_paths(args.provider).response_json,
    )
    verifier = (
        verify_ubersuggest_mcp
        if args.provider == "ubersuggest" and not args.no_verify
        else None
    )
    result = refresh_oauth_token(
        provider, paths, timeout=args.timeout, verifier=verifier
    )
    if args.verbose:
        print(json.dumps(result.public_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    provider = PROVIDERS[args.provider]
    paths = default_token_paths(args.provider)
    access = _read_token(paths.access_token, "access")
    refresh = _read_token(paths.refresh_token, "refresh")
    validate_token_shape(access, label="access", provider=provider)
    validate_token_shape(refresh, label="refresh", provider=provider)
    payload: dict[str, Any] = {
        "status": "ok",
        "provider": args.provider,
        "access_token_len": len(access),
        "refresh_token_len": len(refresh),
    }
    if args.verify and args.provider == "ubersuggest":
        payload["verified"] = dict(verify_ubersuggest_mcp(access))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PWP credential provider tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    refresh = sub.add_parser(
        "refresh", help="rotate provider OAuth credentials with a refresh token"
    )
    refresh.add_argument("provider", choices=sorted(PROVIDERS))
    refresh.add_argument("--access-token-file")
    refresh.add_argument("--refresh-token-file")
    refresh.add_argument("--response-file")
    refresh.add_argument("--timeout", type=float, default=30)
    refresh.add_argument(
        "--no-verify", action="store_true", help="skip live provider smoke verification"
    )
    refresh.add_argument(
        "--verbose", action="store_true", help="print non-secret refresh summary"
    )
    refresh.set_defaults(func=_cmd_refresh)

    status = sub.add_parser("status", help="validate local provider token files")
    status.add_argument("provider", choices=sorted(PROVIDERS))
    status.add_argument(
        "--verify", action="store_true", help="run live provider smoke verification"
    )
    status.set_defaults(func=_cmd_status)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CredentialRefreshError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
