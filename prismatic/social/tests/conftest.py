"""Pytest fixtures shared by the social test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismatic.social.config import SocialConfig
from prismatic.social.queue_store import QueueStore


@pytest.fixture
def tmp_media_library(tmp_path: Path) -> Path:
    """Create a small media library of 12 photos with sidecar tags."""
    lib = tmp_path / "lib"
    lib.mkdir()
    images = []
    for i in range(12):
        name = f"photo_{i:02d}.jpg"
        path = lib / name
        path.write_bytes(b"\xff\xd8\xff\xe0" + name.encode("utf-8"))
        images.append(path)
        # Half the photos get a sunset tag; a few get hikes or food.
        if i % 2 == 0:
            (lib / (name + ".tags.json")).write_text(
                json.dumps(["sunset", "ocean"]), encoding="utf-8"
            )
        elif i % 5 == 0:
            (lib / (name + ".tags.json")).write_text(
                json.dumps(
                    {"tags": ["hike", "mountain"], "caption_seed": "Top of the world."}
                ),
                encoding="utf-8",
            )
    return lib


@pytest.fixture
def cfg(tmp_media_library: Path, tmp_path: Path) -> SocialConfig:
    """A dry-run config pointing at ``tmp_media_library`` and a tmp queue."""
    return SocialConfig(
        media_library=tmp_media_library,
        queue_path=tmp_path / "queue.json",
        daily_limit=3,
        dry_run=True,
        disable_posting=False,
        hashtags=["#growthwebdev", "#smallbiz", "#marketing"],
        cron_hours=["10:00", "14:00", "18:00"],
        meta_access_token=None,
        meta_business_id=None,
        meta_api_version="v21.0",
    )


@pytest.fixture
def empty_queue(cfg: SocialConfig) -> QueueStore:
    return QueueStore.load(cfg.queue_path)
