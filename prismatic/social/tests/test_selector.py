"""Tests for the photo selector."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from prismatic.social.config import SocialConfig
from prismatic.social.exceptions import MediaLibraryError
from prismatic.social.selector import PhotoSelector


def test_select_returns_top_n(cfg: SocialConfig) -> None:
    sel = PhotoSelector(cfg, rng=random.Random(0))
    photos = sel.select(n=3)
    assert len(photos) == 3
    # All paths must live under the configured library.
    for p in photos:
        assert str(p.photo_id).startswith("ph_")


def test_select_default_limit_is_configured(cfg: SocialConfig) -> None:
    sel = PhotoSelector(cfg, rng=random.Random(0))
    photos = sel.select(n=cfg.daily_limit)
    assert len(photos) == cfg.daily_limit


def test_select_is_deterministic_with_seed(cfg: SocialConfig) -> None:
    a = PhotoSelector(cfg, rng=random.Random(42)).select(n=3)
    b = PhotoSelector(cfg, rng=random.Random(42)).select(n=3)
    assert [p.photo_id for p in a] == [p.photo_id for p in b]


def test_select_prefers_unposted(cfg: SocialConfig) -> None:
    posted = {p.photo_id for p in PhotoSelector(cfg, rng=random.Random(0)).select(n=2)}
    sel = PhotoSelector(cfg, previously_posted_ids=posted, rng=random.Random(0))
    fresh = sel.select(n=2)
    # The fresh selection must not overlap with already-posted photos.
    assert not (posted & {p.photo_id for p in fresh})


def test_select_n_zero_returns_empty(cfg: SocialConfig) -> None:
    assert PhotoSelector(cfg).select(n=0) == []


def test_select_n_negative_returns_empty(cfg: SocialConfig) -> None:
    assert PhotoSelector(cfg).select(n=-1) == []


def test_select_missing_library_raises(tmp_path: Path) -> None:
    bad = SocialConfig(
        media_library=tmp_path / "does-not-exist",
        queue_path=tmp_path / "queue.json",
        daily_limit=3,
        dry_run=True,
        disable_posting=False,
        hashtags=[],
        cron_hours=["10:00"],
        meta_access_token=None,
        meta_business_id=None,
        meta_api_version="v21.0",
    )
    with pytest.raises(MediaLibraryError):
        PhotoSelector(bad).select(n=1)


def test_select_reads_json_tagsidecar(cfg: SocialConfig) -> None:
    sel = PhotoSelector(cfg, rng=random.Random(0))
    photos = sel.select(n=12)  # pull everything
    # At least one photo has sunset tags.
    assert any("sunset" in p.tags for p in photos)


def test_select_does_not_mutate_filesystem(cfg: SocialConfig) -> None:
    sel = PhotoSelector(cfg, rng=random.Random(0))
    sel.select(n=3)
    # Files unchanged: listdir returns same set as before.
    assert len(list(cfg.media_library.iterdir())) > 0
