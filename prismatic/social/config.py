"""Configuration loader for the social pipeline.

Environment contract
--------------------
``SOCIAL_MEDIA_LIBRARY``  Root path containing photos. Default:
    ``$PRISMATIC_HOME/media_library`` or ``~/mounts/synology-photo``.

``SOCIAL_DAILY_LIMIT``    Number of posts/day (3-5 per GRO-572). Default: 4.

``SOCIAL_QUEUE_PATH``     JSON queue file. Default:
    ``$PRISMATIC_HOME/social_queue.json``.

``META_ACCESS_TOKEN``     Long-lived Instagram Graph API token.
``META_BUSINESS_ID``      Instagram Business Account ID (numeric).
``META_API_VERSION``      Graph API version. Default: ``v21.0``.

``SOCIAL_DRY_RUN``        Force dry-run even if creds are present ("1").
``SOCIAL_DISABLE_POSTING`` Do not advance POSTING -> POSTED. Useful for staging.

``SOCIAL_HASHTAGS``       Comma-separated fallback hashtags when photo has no tags.

``SOCIAL_CRON_HOURS``     Comma-separated HH:MM slots for scheduled posts
                          (in UTC). Default: ``15:00,17:00,19:00,21:00`` for 4/day.

The loader never crashes on missing optional fields — only on truly
required paths. Meta credentials are optional so dry-run works.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from prismatic.social.exceptions import ConfigError


def _default_home() -> Path:
    return Path(
        os.environ.get("PRISMATIC_HOME") or Path.home() / "work" / "prismatic_state"
    )


@dataclass(frozen=True)
class SocialConfig:
    """Resolved, immutable configuration."""

    media_library: Path
    queue_path: Path
    daily_limit: int
    dry_run: bool
    disable_posting: bool
    hashtags: list[str]
    cron_hours: list[str]
    meta_access_token: str | None
    meta_business_id: str | None
    meta_api_version: str
    user_agent: str = "prismatic-social/1.0 (+GRO-572)"

    def live_meta_available(self) -> bool:
        return bool(self.meta_access_token and self.meta_business_id)


def _parse_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e
    if not (lo <= v <= hi):
        raise ConfigError(f"{name} must be between {lo} and {hi}, got {v}")
    return v


def _parse_hhmm_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    slots = [s.strip() for s in raw.split(",") if s.strip()]
    if not slots:
        return list(default)
    pat = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    for s in slots:
        if not pat.match(s):
            raise ConfigError(f"{name} slot {s!r} is not HH:MM (24h)")
    return slots


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> SocialConfig:
    """Read env vars and return an immutable ``SocialConfig``."""

    home = _default_home()
    media_lib = Path(
        os.environ.get("SOCIAL_MEDIA_LIBRARY")
        or (Path.home() / "mounts" / "synology-photo")
    )
    queue = Path(os.environ.get("SOCIAL_QUEUE_PATH") or (home / "social_queue.json"))
    daily_limit = _parse_int("SOCIAL_DAILY_LIMIT", default=4, lo=1, hi=12)

    token = os.environ.get("META_ACCESS_TOKEN") or None
    biz = os.environ.get("META_BUSINESS_ID") or None

    api_version = os.environ.get("META_API_VERSION") or "v21.0"

    # Dry-run whenever creds are missing OR explicit flag is set.
    dry_run = (not (token and biz)) or _truthy("SOCIAL_DRY_RUN")
    disable_posting = _truthy("SOCIAL_DISABLE_POSTING")

    tags_raw = os.environ.get("SOCIAL_HASHTAGS") or ""
    hashtags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    if not hashtags:
        hashtags = ["#growthwebdev", "#smallbusiness", "#marketing"]

    cron_hours = _parse_hhmm_list(
        "SOCIAL_CRON_HOURS", default=["15:00", "17:00", "19:00", "21:00"]
    )

    return SocialConfig(
        media_library=media_lib,
        queue_path=queue,
        daily_limit=daily_limit,
        dry_run=dry_run,
        disable_posting=disable_posting,
        hashtags=hashtags,
        cron_hours=cron_hours,
        meta_access_token=token,
        meta_business_id=biz,
        meta_api_version=api_version,
    )
