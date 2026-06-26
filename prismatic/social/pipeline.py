"""SocialPipeline — orchestrates the four stages.

Stages
------
1. **select**  — pick top-N photos from the library.
2. **caption** — generate a caption per photo.
3. **queue**   — persist Posts to QueueStore (PENDING or SCHEDULED).
4. **publish** — for any due Posts, advance POSTING -> POSTED via Meta.

Each stage is callable on its own (so the CLI can run ``select-only``
for inspection) and the combined ``run_daily_batch`` is the cron target.

The pipeline is **idempotent on a given calendar day**: re-running it
produces the same ``Post.post_id`` for the same photo + slot, so the
queue does not duplicate entries if cron fires twice.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prismatic.social.captioner import CaptionGenerator
from prismatic.social.config import SocialConfig
from prismatic.social.exceptions import (
    CaptionError,
    MediaLibraryError,
    MetaAPIError,
    SocialPipelineError,
)
from prismatic.social.meta_client import (
    DryRunMetaClient,
    MetaGraphClient,
    build_meta_client,
)
from prismatic.social.models import Caption, Photo, Post, PostStatus
from prismatic.social.queue_store import QueueStore
from prismatic.social.selector import PhotoSelector

log = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    """Summary of a single pipeline run; safe to JSON-dump."""

    selected: list[str] = field(default_factory=list)
    captioned: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    attempted: list[str] = field(default_factory=list)
    posted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "captioned": self.captioned,
            "queued": self.queued,
            "attempted": self.attempted,
            "posted": self.posted,
            "failed": self.failed,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
        }


class SocialPipeline:
    """End-to-end orchestrator. Stateless apart from its dependencies."""

    def __init__(
        self,
        *,
        config: SocialConfig,
        selector: PhotoSelector | None = None,
        captioner: CaptionGenerator | None = None,
        meta_client: MetaGraphClient | DryRunMetaClient | None = None,
        queue: QueueStore | None = None,
    ) -> None:
        self.config = config
        self.queue = queue or QueueStore.load(config.queue_path)
        self._selector = selector or PhotoSelector(
            config,
            previously_posted_ids=self.queue.posted_photo_ids(),
        )
        self._captioner = captioner or CaptionGenerator(config=config)
        self._meta = meta_client or build_meta_client(config)

    # -- top-level --------------------------------------------------------

    def run_daily_batch(
        self, *, day: datetime | None = None, save: bool = True
    ) -> PipelineReport:
        """Select, caption, queue, and publish-due in one pass."""
        report = PipelineReport(dry_run=self.config.dry_run)
        day = day or datetime.now(timezone.utc)

        try:
            photos = self.select(n=self.config.daily_limit)
        except MediaLibraryError as e:
            log.warning("select stage failed: %s", e)
            report.skipped.append(f"select: {e}")
            return report
        report.selected = [p.path for p in photos]

        captions = self.caption(photos)
        report.captioned = [c.photo_id for c in captions]

        queued = self.enqueue(captions=captions, photos=photos, day=day, save=False)
        report.queued = [q.post_id for q in queued]

        published = self.publish_due()
        report.attempted = [p.post_id for p in published["attempted"]]
        report.posted = [p.post_id for p in published["posted"]]
        report.failed = [p.post_id for p in published["failed"]]

        if save:
            try:
                self.queue.save()
            except Exception as e:  # noqa: BLE001  - surface as report field
                log.error("queue save failed: %s", e)
                report.skipped.append(f"queue.save: {e}")
        return report

    # -- stages (each independently runnable) -----------------------------

    def select(self, *, n: int | None = None) -> list[Photo]:
        """Stage 1 — return top-N photos from the library."""
        n = n or self.config.daily_limit
        return self._selector.select(n)

    def caption(self, photos: list[Photo]) -> list[Caption]:
        """Stage 2 — generate captions. Skips photos that fail."""
        out: list[Caption] = []
        for p in photos:
            try:
                out.append(self._captioner.generate(p))
            except CaptionError as e:
                log.warning("caption failed for %s: %s", p.path, e)
        return out

    def enqueue(
        self,
        *,
        captions: list[Caption],
        photos: list[Photo],
        day: datetime,
        save: bool = True,
    ) -> list[Post]:
        """Stage 3 — persist Posts at scheduled slots for the given UTC day."""
        if len(captions) != len(photos):
            raise SocialPipelineError(
                f"caption/photo count mismatch: {len(captions)} captions vs {len(photos)} photos"
            )
        slots = self._slots_for(day=day, count=len(photos))
        queued: list[Post] = []
        for photo, caption, slot_iso in zip(photos, captions, slots, strict=True):
            post_id = Post.make_id(photo.photo_id, slot_iso)
            existing = self.queue.get(post_id)
            if existing is not None:
                # Idempotent re-run: keep prior status, do not duplicate.
                queued.append(existing)
                continue
            post = Post(
                post_id=post_id,
                photo=photo,
                caption=caption,
                status=PostStatus.SCHEDULED,
                scheduled_for=slot_iso,
            )
            self.queue.add(post)
            queued.append(post)
        if save:
            self.queue.save()
        return queued

    def publish_due(self) -> dict[str, list[Post]]:
        """Stage 4 — push any due SCHEDULED posts through POSTING -> POSTED."""
        result: dict[str, list[Post]] = {
            "attempted": [],
            "posted": [],
            "failed": [],
            "skipped": [],
        }
        due = self.queue.due()
        if self.config.disable_posting:
            result["skipped"] = list(due)
            return result

        for post in due:
            result["attempted"].append(post)
            try:
                self.queue.transition(post.post_id, PostStatus.POSTING)
                post.attempts += 1
                image_url = self._public_url_for(post.photo)
                media_id = self._meta.publish(
                    image_url=image_url,
                    caption=post.caption.full_text,
                ).media_id
                post.meta_media_id = media_id
                self.queue.transition(post.post_id, PostStatus.POSTED)
                result["posted"].append(post)
            except MetaAPIError as e:
                post.error = str(e)
                try:
                    self.queue.transition(post.post_id, PostStatus.FAILED)
                except Exception:  # noqa: BLE001
                    pass
                result["failed"].append(post)
                log.warning("post %s failed: %s", post.post_id, e)
        # Persist any state changes.
        try:
            self.queue.save()
        except Exception as e:  # noqa: BLE001
            log.error("queue save during publish failed: %s", e)
        return result

    # -- helpers ----------------------------------------------------------

    def _slots_for(self, *, day: datetime, count: int) -> list[str]:
        """Return ``count`` ISO-8601 UTC timestamps spaced across the day."""
        slots = self.config.cron_hours
        base = day.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Even spacing fallback if the cron list is shorter than count.
        if len(slots) >= count:
            chosen = slots[:count]
        else:
            chosen = list(slots)
            # Spread remaining posts every 2 hours starting after the last slot.
            for i in range(len(slots), count):
                hour = 9 + 2 * i  # 11, 13, 15, ...
                chosen.append(f"{hour % 24:02d}:00")
        out: list[str] = []
        for hhmm in chosen:
            hour, minute = (int(x) for x in hhmm.split(":"))
            ts = base + timedelta(hours=hour, minutes=minute)
            out.append(ts.isoformat(timespec="seconds"))
        return out

    def _public_url_for(self, photo: Photo) -> str:
        """Resolve a photo path to a publicly fetchable URL for Meta.

        If ``SOCIAL_PUBLIC_URL_PREFIX`` is set (e.g. a Cloudflare R2 bucket),
        we use ``urllib.parse.quote`` to build the absolute URL. Otherwise
        we return the ``file://`` URL — Meta cannot fetch ``file://`` in
        production, but ``DryRunMetaClient`` ignores the URL anyway, so this
        keeps the queue consistent in dry-run.
        """
        env_prefix = self._env_public_prefix()
        if env_prefix:
            rel = photo.path
            try:
                rel = str(
                    Path(photo.path)
                    .resolve()
                    .relative_to(Path(self.config.media_library).resolve())
                )
            except ValueError:
                rel = Path(photo.path).name
            return env_prefix.rstrip("/") + "/" + urllib.parse.quote(rel, safe="/")
        return "file://" + photo.path

    @staticmethod
    def _env_public_prefix() -> str | None:
        import os

        v = os.environ.get("SOCIAL_PUBLIC_URL_PREFIX")
        return v.rstrip("/") if v else None
