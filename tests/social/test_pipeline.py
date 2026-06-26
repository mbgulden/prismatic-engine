"""Tests for the end-to-end SocialPipeline orchestrator."""

from __future__ import annotations

import dataclasses
import random
from datetime import datetime, timezone


from prismatic.social.captioner import CaptionGenerator
from prismatic.social.config import SocialConfig
from prismatic.social.meta_client import DryRunMetaClient
from prismatic.social.models import PostStatus
from prismatic.social.pipeline import SocialPipeline, PipelineReport
from prismatic.social.queue_store import QueueStore
from prismatic.social.selector import PhotoSelector


def _pipeline(cfg: SocialConfig, queue: QueueStore | None = None) -> SocialPipeline:
    return SocialPipeline(
        config=cfg,
        selector=PhotoSelector(cfg, rng=random.Random(0)),
        captioner=CaptionGenerator(config=cfg),
        meta_client=DryRunMetaClient(cfg),
        queue=queue or QueueStore.load(cfg.queue_path),
    )


def test_run_daily_batch_writes_posts(cfg: SocialConfig) -> None:
    p = _pipeline(cfg)
    # Pick a day in the far future so all slots are SCHEDULED, not yet due.
    day = datetime(2099, 1, 1, tzinfo=timezone.utc)
    report = p.run_daily_batch(day=day)
    assert isinstance(report, PipelineReport)
    assert len(report.selected) == cfg.daily_limit
    assert len(report.queued) == cfg.daily_limit
    # All queued posts should be in the store as SCHEDULED (slots in future).
    for pid in report.queued:
        stored = p.queue.get(pid)
        assert stored is not None
        assert stored.status == PostStatus.SCHEDULED


def test_run_daily_batch_is_idempotent_on_same_day(cfg: SocialConfig) -> None:
    p = _pipeline(cfg)
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    a = p.run_daily_batch(day=day)
    b = p.run_daily_batch(day=day)
    # Same post ids both runs.
    assert set(a.queued) == set(b.queued)
    # Store did not double up.
    assert len(p.queue) == cfg.daily_limit


def test_run_daily_batch_publishes_due_posts_in_past(cfg: SocialConfig) -> None:
    p = _pipeline(cfg)
    # Pick a day in the past so scheduled slots are immediately due.
    past_day = datetime(2020, 1, 1, tzinfo=timezone.utc)
    report = p.run_daily_batch(day=past_day)
    assert report.attempted, "expected at least one due post"
    assert report.posted, "expected at least one successful post"
    # Posted posts have a synthetic dry-run media id.
    for pid in report.posted:
        stored = p.queue.get(pid)
        assert stored is not None
        assert stored.meta_media_id is not None
        assert stored.status == PostStatus.POSTED


def test_disable_posting_leaves_due_in_scheduled(cfg: SocialConfig) -> None:
    cfg2 = dataclasses.replace(cfg, disable_posting=True)
    p = _pipeline(cfg2)
    past_day = datetime(2020, 1, 1, tzinfo=timezone.utc)
    report = p.run_daily_batch(day=past_day)
    assert len(report.attempted) == 0
    # Posts were queued but not flipped to POSTED.
    for pid in report.queued:
        stored = p.queue.get(pid)
        assert stored is not None
        assert stored.status == PostStatus.SCHEDULED


def test_select_stage_returns_photos(cfg: SocialConfig) -> None:
    p = _pipeline(cfg)
    photos = p.select(n=2)
    assert len(photos) == 2


def test_caption_stage_handles_failures_gracefully(cfg: SocialConfig, tmp_path) -> None:
    p = _pipeline(cfg)
    # Caption stage with one bad photo should skip it.
    from prismatic.social.models import Photo

    bad = Photo(path="", tags=[])  # path is empty -> CaptionError inside generator
    good = next(iter(p.select(n=1)))
    captions = p.caption([bad, good])
    assert len(captions) == 1


def test_enqueue_assigns_distinct_slots(cfg: SocialConfig) -> None:
    p = _pipeline(cfg)
    day = datetime(2026, 6, 26, tzinfo=timezone.utc)
    photos = p.select(n=3)
    captions = p.caption(photos)
    posts = p.enqueue(captions=captions, photos=photos, day=day, save=False)
    slots = {p.scheduled_for for p in posts}
    assert len(slots) == 3
