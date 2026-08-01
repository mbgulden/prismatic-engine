"""auth_loader — standardized credential lookup for PWP capability clients.

Solves the "where are my creds?" problem across the PWP plugin. Every
client (Cloudflare, Vercel, Stripe, Google, Linear, Zapier) calls into
this module to discover their credentials, regardless of where the user
has them stored.

Resolution order (earliest wins) for each secret type:

  1. Explicit argument (raw value or path)
  2. Environment variables (LINKED_API_KEY, STRIPE_SECRET_KEY, etc.)
  3. ~/.hermes/profiles/<active>/.env (current profile's env file)
  4. ~/.hermes/profiles/<active>/home/.config/gcloud/application_default_credentials.json
  5. ~/work/<project>/.env files (project-local; walked up from CWD)
  6. Midnight secrets.json (if defined)

The loader never logs raw secret values. It returns a typed AuthResult
object with the value, source, and a redaction hint for diagnostics.

Usage:
    from plugins.pwp.capabilities.provision_site.auth_loader import (
        get_secret, AUTH_RESOLVERS,
    )

    stripe = get_secret("stripe_secret_key")
    if stripe.found:
        client = StripeClient(api_key=stripe.value)
    else:
        log("Stripe not configured: %s", stripe.hint)

CLI exposure:
    pwp-kpi-tracker auth list                 # Show what we found
    pwp-kpi-tracker auth detect --type stripe # Show resolution trace
    pwp-kpi-tracker auth register --type stripe --value <sk_...>
                                            # Write to ~/.hermes/profiles/<active>/.env
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- Path constants ------------------------------------------------------
#
# All paths must be environment-variable-driven for portability across
# dev / staging / prod. Defaults are bare, OS-agnostic values; callers
# should always export HERMES_HOME, WORK_DIR, and HERMES_PROFILE before
# importing this module.
#
# Two layouts are supported, detected automatically:
#
#   Layout A (hermes root):  HERMES_HOME = ~/.hermes
#                            PROFILES_DIR = HERMES_HOME/profiles
#                            Per-profile .env = PROFILES_DIR/<name>/.env
#
#   Layout B (profile root): HERMES_HOME = ~/.hermes/profiles/<name>
#                            PROFILES_DIR = HERMES_HOME.parent (which IS
#                                          the profiles directory)
#                            Per-profile .env = HERMES_HOME/.env
#                            (HERMES_HOME itself is the profile root)
#
# This auto-detection makes the module work both when invoked from
# outside Hermes (HERMES_HOME points to the canonical root) and from
# inside a Hermes profile session (HERMES_HOME points to the profile).

HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", "~/.hermes")
).expanduser()

# Pattern: HERMES_HOME = <root>/.hermes/profiles/<name>
_PROFILE_ROOT_RE = re.compile(
    r"/\.hermes/profiles/([^/]+)/?$"
)


def _resolve_profiles_dir(hermes_home: Path) -> tuple[Path, str | None]:
    """Return (profiles_dir, profile_from_path).

    If `hermes_home` ends in `/profiles/<name>`, the caller is inside
    a Hermes profile session and PROFILES_DIR is the parent (which
    IS the profiles directory — no further `/profiles` suffix needed).
    Otherwise, PROFILES_DIR = hermes_home/profiles.

    profile_from_path is the profile name if HERMES_HOME points at a
    profile root (Layout B), else None.
    """
    m = _PROFILE_ROOT_RE.search(str(hermes_home))
    if m:
        profile = m.group(1)
        # The parent of ~/.hermes/profiles/<name> is ~/.hermes/profiles
        # which IS the profiles directory itself.
        return hermes_home.parent, profile
    return hermes_home / "profiles", None


PROFILES_DIR, _HERMES_HOME_PROFILE = _resolve_profiles_dir(HERMES_HOME)
WORK_DIR = Path(
    os.environ.get("WORK_DIR", "~/work")
).expanduser()

# --- Active profile resolution ------------------------------------------

def _resolve_active_profile() -> str:
    """Resolve the active profile name from the environment.

    Precedence:
      1. HERMES_PROFILE (when set to a bare profile name like 'ned')
      2. HERMES_PROFILE (full path like <hermes_root>/.hermes/profiles/<name>)
         — extract <name>
      3. HERMES_HOME (if it points to <hermes_root>/.hermes/profiles/<name>)
         — extract <name>
      4. Default 'ned'
    """
    raw = os.environ.get("HERMES_PROFILE", "") or ""
    if raw:
        if "/" in raw:
            return Path(raw).name
        return raw
    # If HERMES_HOME was detected at import time as a profile root,
    # trust that detection (Layout B).
    if _HERMES_HOME_PROFILE:
        return _HERMES_HOME_PROFILE
    hh = os.environ.get("HERMES_HOME", "")
    m = _PROFILE_ROOT_RE.search(hh)
    if m:
        return m.group(1)
    return "ned"


def _profile_env_path(profile: str) -> Path:
    """Resolve a profile spec to a .env file path.

    Accepts either a bare profile name ('ned') or a full path
    ('<HERMES_HOME>/profiles/<name>').
    """
    p = Path(profile)
    if p.is_absolute():
        return p / ".env"
    return PROFILES_DIR / profile / ".env"


# Active profile (env override > default 'ned')
ACTIVE_PROFILE = _resolve_active_profile()


def _profile_name(p: str) -> str:
    """Extract the profile name from a string that's either 'ned' or
    '<HERMES_HOME>/profiles/<name>'."""
    if "/" in p:
        return Path(p).name
    return p


# --- Types ---------------------------------------------------------------

@dataclass(frozen=True)
class AuthResult:
    """The result of a credential lookup.

    Attributes:
      value:     The resolved secret value, or None if not found.
      source:    Where the value came from ("env", "gcloud-adc", "project-env",
                 "explicit", "redirect", "none").
      env_var:   The canonical env var name the client should also export
                 (so subprocesses and child APIs find it).
      hint:      A human-readable hint showing the search trace and next
                 step to provide the credential.
      redaction: A safe string showing the value's prefix and length
                 (e.g. "sk_live_XXX...len=107"); never includes the raw
                 secret.
    """
    value: str | None
    source: str
    env_var: str
    hint: str
    redaction: str
    env: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        """Redact the value in repr to prevent accidental logging of
        raw secrets. Callers who need the value should use `result.value`
        explicitly; everything else (logs, errors, tracebacks) should
        only ever see `redaction`."""
        return (
            f"AuthResult(found={self.found}, source={self.source!r}, "
            f"env_var={self.env_var!r}, redaction={self.redaction!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def found(self) -> bool:
        return self.value is not None

    def export_to_env(self) -> None:
        """Export the value (and any associated env vars) into os.environ
        so subprocesses and standard lib clients (Stripe, urllib, etc.)
        can find it without a custom code path."""
        if self.value is not None:
            os.environ[self.env_var] = self.value
        for k, v in self.env.items():
            os.environ.setdefault(k, v)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "source": self.source,
            "env_var": self.env_var,
            "hint": self.hint,
            "redaction": self.redaction,
        }


# --- Redaction helpers ---------------------------------------------------

def _redact(value: str) -> str:
    """Return a safe redaction of a secret string. Default: first 7 chars
    + 3 dots + length. Never includes the raw value."""
    if not value:
        return "<empty>"
    head = value[:7]
    return f"{head}...len={len(value)}"


# --- Loaders -------------------------------------------------------------

def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env-style file into a dict. Supports dotted pairs with
    single/double quotes and `#` comments. No shell interpolation."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        # Strip surrounding quotes
        if len(value) >= 2 and (
            (value[0] == '"' and value[-1] == '"')
            or (value[0] == "'" and value[-1] == "'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def _load_profile_env(profile: str) -> dict[str, str]:
    """Load the active profile's .env file."""
    path = _profile_env_path(profile)
    return _load_env_file(path)


def _load_gcloud_adc(profile: str) -> dict[str, Any]:
    """Load the gcloud application_default_credentials.json file.

    Returns parsed JSON; client_id / refresh_token / client_secret.
    """
    candidates = [
        # 1. Active Hermes profile's gcloud dir (most common case)
        PROFILES_DIR / profile / "home" / ".config" / "gcloud"
                  / "application_default_credentials.json",
        # 2. Active profile if it's given as a full path
        Path(profile) / "home" / ".config" / "gcloud"
                  / "application_default_credentials.json",
        # 3. Bare HOME (when not under ~/.hermes/profiles)
        Path.home() / ".config" / "gcloud" / "application_default_credentials.json",
    ]
    for cand in candidates:
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


def _walk_project_env(start: Path) -> list[Path]:
    """Walk up from `start` looking for .env files in ancestor project dirs.
    Returns candidates in priority order (innermost first).

    Also scans WORK_DIR for known project .env files (hd-platform,
    hd-platform-staging) since those are siblings of the active repo
    and may contain the user's shared production credentials.
    """
    seen: set = set()
    candidates: list[Path] = []

    # 1. Walk up from cwd
    cur = start.resolve()
    for _ in range(8):  # don't walk beyond 8 levels
        if not cur or cur in seen:
            break
        seen.add(cur)
        for fname in (".env", ".env.local", ".env.production", ".env.staging"):
            p = cur / fname
            if p.exists() and p not in candidates:
                candidates.append(p)
        if cur == Path("/") or cur.parent == cur:
            break
        cur = cur.parent

    # 2. Common shared locations (siblings of the active project)
    for shared in (WORK_DIR / "hd-platform" / ".env",
                   WORK_DIR / "hd-platform-staging" / ".env",
                   WORK_DIR / "hd-platform-GRO-3988" / ".env"):
        if shared.exists() and shared not in candidates:
            candidates.append(shared)

    return candidates


# --- Auth resolution config ----------------------------------------------

@dataclass(frozen=True)
class AuthSpec:
    """How to find a credential."""
    name: str
    env_vars: tuple[str, ...]
    doc: str
    # Optional: if found, also set these env vars (for client compat)
    extra_env: dict[str, str] = field(default_factory=dict)
    # Optional: a function to extract the value from gcloud ADC
    gcloud_extractor: Callable[[dict[str, Any]], str | None] | None = None


# Registry of known secret types
AUTH_SPECS: dict[str, AuthSpec] = {
    "stripe_secret_key": AuthSpec(
        name="stripe_secret_key",
        env_vars=(
            "STRIPE_RESTRICTED_KEY",
            "STRIPE_API_KEY",
            "STRIPE_SECRET_KEY",
        ),
        doc="Stripe API secret key (sk_live_*, sk_test_*, or rk_live_*).",
        extra_env={"STRIPE_API_KEY": ""},  # filled in at lookup time
    ),
    "google_adc": AuthSpec(
        name="google_adc",
        env_vars=("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SA_JSON"),
        doc="Google Application Default Credentials (path or JSON).",
        gcloud_extractor=lambda adc: (
            json.dumps(adc) if adc and "refresh_token" in adc else None
        ),
    ),
    "google_oauth_client_id": AuthSpec(
        name="google_oauth_client_id",
        env_vars=("GOOGLE_OAUTH_CLIENT_ID",),
        doc="Google OAuth Client ID (for OAuth flow).",
        gcloud_extractor=lambda adc: adc.get("client_id") if adc else None,
    ),
    "vercel_token": AuthSpec(
        name="vercel_token",
        env_vars=("VERCEL_TOKEN", "VERCEL_API_TOKEN"),
        doc="Vercel API token (used for project + env-var management).",
    ),
    "linear_api_key": AuthSpec(
        name="linear_api_key",
        env_vars=("LINEAR_API_KEY", "LINEAR_PERSONAL_TOKEN", "LINEAR_TOKEN"),
        doc="Linear API key (used for ticket dispatching).",
    ),
    "zapier_webhook_url": AuthSpec(
        name="zapier_webhook_url",
        env_vars=("ZAPIER_WEBHOOK_URL",),
        doc="Zapier webhook URL (used to pull external data sources).",
    ),
    "cloudflare_token": AuthSpec(
        name="cloudflare_token",
        env_vars=(
            "CLOUDFLARE_PAGES_API_TOKEN",
            "CLOUDFLARE_API_TOKEN",
            "CF_API_TOKEN",
        ),
        doc="Cloudflare API token (used for DNS + zone management).",
    ),
    "github_token": AuthSpec(
        name="github_token",
        env_vars=("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"),
        doc=(
            "GitHub Personal Access Token or gh-CLI auth token. "
            "Used for repo metadata, webhooks, Actions secrets."
        ),
    ),
}


# --- The main lookup function --------------------------------------------

def _build_hint(spec: AuthSpec, searched: list[str]) -> str:
    """Build a human-readable hint for a missing credential."""
    env_list = ", ".join(spec.env_vars)
    return (
        f"No credential found for '{spec.name}'. Searched: {', '.join(searched)}. "
        f"To register, run: pwp-kpi-tracker auth register --type {spec.name} "
        f"--value <secret> (this writes to ~/.hermes/profiles/<active>/.env as "
        f"{env_list.split(',')[0].strip()})."
    )


def get_secret(
    name: str,
    *,
    explicit: str | None = None,
    profile: str | None = None,
    cwd: Path | None = None,
) -> AuthResult:
    """Look up a secret by name, returning a typed AuthResult.

    Args:
      name: The secret type ("stripe_secret_key", "google_adc", etc.)
      explicit: An explicit value to use (skips all lookup).
      profile: Override the active Hermes profile.
      cwd: Override cwd for project-local .env walking.

    Returns:
      AuthResult with value, source, env_var, hint, redaction.
    """
    spec = AUTH_SPECS.get(name)
    if spec is None:
        return AuthResult(
            value=None,
            source="none",
            env_var="",
            hint=f"Unknown auth spec '{name}'. Known: {', '.join(AUTH_SPECS.keys())}",
            redaction="<unknown>",
        )

    profile = profile or ACTIVE_PROFILE
    cwd = cwd or Path.cwd()
    searched: list[str] = []

    # 1. Explicit
    if explicit:
        return AuthResult(
            value=explicit,
            source="explicit",
            env_var=spec.env_vars[0] if spec.env_vars else "",
            hint="value provided as explicit argument",
            redaction=_redact(explicit),
            env={**{v: explicit for v in spec.env_vars}, **spec.extra_env},
        )

    # 2. Environment variables
    for v in spec.env_vars:
        if os.environ.get(v):
            val = os.environ[v]
            return AuthResult(
                value=val,
                source="env",
                env_var=v,
                hint=f"Found in environment variable {v}",
                redaction=_redact(val),
                env={**{v: val for v in spec.env_vars}, **spec.extra_env
                    | {spec.env_vars[0]: val}},
            )
    searched.append("env(" + ",".join(spec.env_vars) + ")")

    # 3. Profile .env
    profile_env = _load_profile_env(profile)
    for v in spec.env_vars:
        if profile_env.get(v):
            val = profile_env[v]
            return AuthResult(
                value=val,
                source="profile-env",
                env_var=v,
                hint=f"Found in ~/.hermes/profiles/{profile}/.env as {v}",
                redaction=_redact(val),
                env={**{v: val for v in spec.env_vars}, **spec.extra_env},
            )
    searched.append(f"profile-env(~/.hermes/profiles/{profile}/.env)")

    # 4. gcloud ADC (for google_* specs)
    if spec.gcloud_extractor is not None:
        adc = _load_gcloud_adc(profile)
        if adc:
            val = spec.gcloud_extractor(adc)
            if val:
                return AuthResult(
                    value=val,
                    source="gcloud-adc",
                    env_var="GOOGLE_APPLICATION_CREDENTIALS",
                    hint=f"Found in gcloud ADC for profile {profile}",
                    redaction=_redact(val),
                    env={"GOOGLE_APPLICATION_CREDENTIALS": val},
                )
        searched.append("gcloud-adc")

    # 5. Project-local .env files (walk up from cwd)
    for p in _walk_project_env(cwd):
        proj_env = _load_env_file(p)
        for v in spec.env_vars:
            if proj_env.get(v):
                val = proj_env[v]
                return AuthResult(
                    value=val,
                    source="project-env",
                    env_var=v,
                    hint=f"Found in {p}",
                    redaction=_redact(val),
                    env={**{v: val for v in spec.env_vars}, **spec.extra_env},
                )
    searched.append("project-env(walk-up)")

    # 6. None
    return AuthResult(
        value=None,
        source="none",
        env_var=spec.env_vars[0] if spec.env_vars else "",
        hint=_build_hint(spec, searched),
        redaction="<missing>",
    )


# --- Registration helper ------------------------------------------------

def register_secret(
    name: str,
    *,
    value: str,
    env_var: str | None = None,
    profile: str | None = None,
) -> Path:
    """Persist a credential to the active profile's .env file.

    Updates an existing key if present, otherwise appends. Returns the
    path of the file that was written.

    This is the canonical way to onboard a new credential through the
    PWP plugin.
    """
    spec = AUTH_SPECS.get(name)
    if spec is None:
        raise ValueError(f"Unknown auth spec '{name}'")
    profile = profile or ACTIVE_PROFILE
    env_var = env_var or spec.env_vars[0]
    path = _profile_env_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_env_file(path)
    existing[env_var] = value

    # Write back all keys (preserves file shape)
    lines = [f"{k}={v}" for k, v in existing.items()]
    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    # Restrict to 0600
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return path


# --- Self-test list of all known specs ------------------------------------

def list_known() -> list[dict[str, str]]:
    """Return a list of the registered auth specs as dicts."""
    return [
        {
            "name": spec.name,
            "env_vars": ", ".join(spec.env_vars),
            "doc": spec.doc,
        }
        for spec in AUTH_SPECS.values()
    ]
