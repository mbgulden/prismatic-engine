"""
prismatic/credential/manager.py — Centralized Settings & Credential Store.

Provides a unified manager for configuring, reading, redacting, and persisting
external service credentials (Google Service Account, GA4, GTM, Cloudflare,
Vercel, Stripe, GitHub, Linear, Ubersuggest) across Prismatic Engine and plugins.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

PRISMATIC_HOME = os.environ.get("PRISMATIC_HOME", os.path.expanduser("~"))
ENV_PATH_CANDIDATES = [
    Path(PRISMATIC_HOME) / ".hermes" / "profiles" / "orchestrator" / ".env",
    Path(PRISMATIC_HOME) / ".env",
    Path.cwd() / ".env",
]

MANAGED_KEYS = [
    "GOOGLE_SA_JSON",
    "GOOGLE_SA_INLINE",
    "GA4_ACCOUNT_ID",
    "GTM_ACCOUNT_ID",
    "GSC_VERIFICATION_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "VERCEL_TOKEN",
    "STRIPE_SECRET_KEY",
    "GITHUB_TOKEN",
    "LINEAR_API_KEY",
    "UBERSUGGEST_ACCESS_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "HERMES_ENDPOINT",
    "AGY_MODEL",
    "JULES_DB_PATH",
]


def _get_target_env_file() -> Path:
    """Return the primary .env file path for persisting secrets."""
    for p in ENV_PATH_CANDIDATES:
        if p.exists():
            return p
    target = ENV_PATH_CANDIDATES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _redact_value(key: str, val: str) -> str:
    if not val:
        return ""
    if key in ("GA4_ACCOUNT_ID", "GTM_ACCOUNT_ID"):
        return val  # Account IDs are non-secret identifiers
    if len(val) <= 8:
        return "****"
    return f"{val[:4]}...{val[-4:]}"


def check_google_antigravity_oauth() -> Dict[str, Any]:
    """Check if Google Antigravity OAuth credentials exist and are fulfilled."""
    home = Path(PRISMATIC_HOME)
    cache_dir = home / ".gemini" / "antigravity"

    fulfilled = False
    details = "Not Connected"

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("GEMINI_API_KEY"):
        fulfilled = True
        details = "Fulfilled via Environment API Key / Credentials"
    elif cache_dir.exists():
        fulfilled = True
        details = f"Fulfilled via Google OAuth ({cache_dir})"

    return {
        "service": "google_antigravity",
        "name": "Google Antigravity OAuth",
        "category": "oauth",
        "auth_type": "oauth2",
        "fulfilled": fulfilled,
        "details": details,
        "icon": "⚡",
        "action_label": "Re-authenticate OAuth" if fulfilled else "Connect Google OAuth",
    }


def check_jules_cli_oauth() -> Dict[str, Any]:
    """Check if Jules CLI OAuth credentials & daily capacity ledger are active."""
    home = Path(PRISMATIC_HOME)
    db_path = home / ".prismatic" / "db" / "jules_capacity.sqlite3"

    fulfilled = False
    details = "Not Configured"

    try:
        from prismatic.jules_capacity import capacity_payload

        payload = capacity_payload()
        if payload.get("ok"):
            fulfilled = True
            details = f"Fulfilled (Daily Limit 300 / Observed: {payload.get('observed_launches', 0)})"
    except Exception:
        pass

    if not fulfilled and db_path.exists():
        fulfilled = True
        details = "Fulfilled via Jules Capacity Store"

    return {
        "service": "jules_cli",
        "name": "Jules CLI OAuth",
        "category": "oauth",
        "auth_type": "oauth2",
        "fulfilled": fulfilled,
        "details": details,
        "icon": "🚀",
        "action_label": "Re-setup Jules OAuth" if fulfilled else "Authorize Jules OAuth",
    }


def get_credentials_status() -> Dict[str, Any]:
    """Inspect current credential status across os.environ, OAuth stores, and .env files."""
    status = {}
    target_env = _get_target_env_file()

    for key in MANAGED_KEYS:
        val = os.environ.get(key, "").strip()
        source = "environment" if key in os.environ and val else "none"

        if not val and target_env.exists():
            try:
                for line in target_env.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith(f"{key}="):
                        val = line.split("=", 1)[1].strip().strip("\"'")
                        source = f".env ({target_env.name})"
                        os.environ[key] = val
                        break
            except Exception:
                pass

        configured = bool(val)
        status[key] = {
            "key": key,
            "configured": configured,
            "source": source,
            "redacted_value": _redact_value(key, val) if configured else "",
            "settings_tab_url": "https://prismatic.growthwebdev.com/tab/settings",
        }

    oauth_services = {
        "google_antigravity": check_google_antigravity_oauth(),
        "jules_cli": check_jules_cli_oauth(),
    }

    return {
        "ok": True,
        "credentials": status,
        "oauth_services": oauth_services,
        "target_env_file": str(target_env),
        "settings_tab_url": "https://prismatic.growthwebdev.com/tab/settings",
    }


def update_credentials(new_creds: Dict[str, str]) -> Dict[str, Any]:
    """Update and persist credential key-value pairs."""
    target_env = _get_target_env_file()
    existing_lines: list[str] = []

    if target_env.exists():
        try:
            existing_lines = target_env.read_text(encoding="utf-8").splitlines()
        except Exception:
            existing_lines = []

    env_map: Dict[str, str] = {}
    for line in existing_lines:
        line_s = line.strip()
        if line_s and not line_s.startswith("#") and "=" in line_s:
            k, v = line_s.split("=", 1)
            env_map[k.strip()] = v.strip().strip("\"'")

    updated_keys = []
    for k, v in new_creds.items():
        if k in MANAGED_KEYS:
            clean_v = str(v or "").strip()
            if clean_v:
                os.environ[k] = clean_v
                env_map[k] = clean_v
                updated_keys.append(k)

    new_lines = []
    for k, v in env_map.items():
        new_lines.append(f"{k}={v}")

    target_env.parent.mkdir(parents=True, exist_ok=True)
    target_env.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "updated_keys": updated_keys,
        "target_env_file": str(target_env),
        "status": get_credentials_status()["credentials"],
    }
