"""QueueStore — JSON-backed persistent post queue.

The queue is a single JSON object:

  {
    "schema_version": 1,
    "updated_at": "2026-06-26T18:00:00+00:00",
    "posts": [ ...Post.to_dict()... ]
  }

Persistence choices
-------------------
* **Atomic writes** — write to ``<path>.tmp`` then ``os.replace`` so a crash
  mid-write cannot leave a partial JSON file.  ``os.replace`` is POSIX-atomic
  on the same filesystem.
* **Pretty-printed** — humans grep this file during incident response; size
  is bounded by the daily post volume (≤ a few hundred posts/week).
* **No partial reads** — a corrupt file is treated as empty after a copy to
  ``<path>.corrupt-<ts>``.  We never raise on read so cron stays green.

The store owns the state-machine transitions; the pipeline asks for a
transition by name and the store applies it (and persists).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from prismatic.social.exceptions import IllegalStateTransition, QueueError
from prismatic.social.models import Post, PostStatus


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class QueueStore:
    """JSON-backed post queue."""

    path: Path
    _posts: dict[str, Post] = field(default_factory=dict)

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "QueueStore":
        """Read queue from disk; corrupt file is quarantined, not fatal."""
        store = cls(path=path)
        if not path.exists():
            return store
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            shutil.copy2(path, path.with_suffix(path.suffix + f".corrupt-{ts}"))
            return store
        posts = data.get("posts") if isinstance(data, dict) else None
        if not isinstance(posts, list):
            return store
        for raw in posts:
            try:
                p = Post.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                # Skip malformed entry but keep going.
                continue
            store._posts[p.post_id] = p
        return store

    def save(self) -> None:
        """Atomic write to disk."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise QueueError(f"cannot create queue dir: {e}") from e
        payload = {
            "schema_version": 1,
            "updated_at": _utc_now_iso(),
            "posts": [p.to_dict() for p in self._posts.values()],
        }
        # Write to a sibling temp file in the same directory for atomic replace.
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=".queue.", suffix=".tmp", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fp:
                    json.dump(payload, fp, indent=2, sort_keys=True)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp_name, self.path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as e:
            raise QueueError(f"queue write failed: {e}") from e

    # -- queue ops ---------------------------------------------------------

    def add(self, post: Post) -> None:
        """Insert or replace a post.  Idempotent on ``post.post_id``."""
        self._posts[post.post_id] = post

    def get(self, post_id: str) -> Post | None:
        return self._posts.get(post_id)

    def all(self) -> list[Post]:
        return list(self._posts.values())

    def by_status(self, status: PostStatus) -> list[Post]:
        return [p for p in self._posts.values() if p.status == status]

    def due(self, *, now_iso: str | None = None) -> list[Post]:
        """Posts whose ``scheduled_for`` has passed and are SCHEDULED."""
        now_iso = now_iso or _utc_now_iso()
        return [
            p
            for p in self._posts.values()
            if p.status == PostStatus.SCHEDULED
            and p.scheduled_for
            and p.scheduled_for <= now_iso
        ]

    def transition(self, post_id: str, new_status: PostStatus) -> Post:
        post = self._posts.get(post_id)
        if post is None:
            raise QueueError(f"unknown post_id {post_id!r}")
        try:
            post.transition(new_status)
        except ValueError as e:
            raise IllegalStateTransition(str(e)) from e
        return post

    def posted_photo_ids(self) -> set[str]:
        return {
            p.photo.photo_id
            for p in self._posts.values()
            if p.status == PostStatus.POSTED
        }

    def __len__(self) -> int:
        return len(self._posts)

    def __iter__(self) -> Iterable[Post]:
        return iter(self._posts.values())
