"""Domain models for the social pipeline.

Plain dataclasses only — no Pydantic, no ORM. The engine's JSON-queue
contract wants serializable, debuggable objects.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _photo_id_for(path: str) -> str:
    """Stable per-path photo id. Same path => same id, always."""
    return "ph_" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def _post_id_for(photo_id: str, scheduled_for: str) -> str:
    """Stable per-(photo,slot) post id so re-running a day is idempotent."""
    seed = f"{photo_id}|{scheduled_for}".encode("utf-8")
    return "po_" + hashlib.sha1(seed).hexdigest()[:16]


class PostStatus(str, Enum):
    """State machine for a queued post.

    Allowed transitions::

        PENDING  -> SCHEDULED   (queue slot reserved)
        PENDING  -> POSTING    (manual override / immediate publish)
        SCHEDULED -> POSTING   (slot elapsed, picked up by poster)
        POSTING  -> POSTED     (Graph API confirmed)
        POSTING  -> FAILED     (terminal failure, will not auto-retry)
        SCHEDULED -> FAILED    (slot expired without successful post)
        FAILED   -> PENDING    (operator-initiated retry)

    Any other transition raises ``IllegalStateTransition``.
    """

    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    POSTING = "POSTING"
    POSTED = "POSTED"
    FAILED = "FAILED"

    @classmethod
    def allowed_from(cls, src: "PostStatus") -> set["PostStatus"]:
        return _ALLOWED.get(src, set())


_ALLOWED: dict[PostStatus, set[PostStatus]] = {
    PostStatus.PENDING: {PostStatus.SCHEDULED, PostStatus.POSTING, PostStatus.FAILED},
    PostStatus.SCHEDULED: {PostStatus.POSTING, PostStatus.FAILED},
    PostStatus.POSTING: {PostStatus.POSTED, PostStatus.FAILED},
    PostStatus.POSTED: set(),
    PostStatus.FAILED: {PostStatus.PENDING},
}


@dataclass
class Photo:
    """A photo discovered in the media library."""

    path: str
    photo_id: str = field(default_factory=lambda: "")
    tags: list[str] = field(default_factory=list)
    caption_seed: str = ""
    score: float = 0.0
    taken_at: str | None = None
    width: int = 0
    height: int = 0

    def __post_init__(self) -> None:
        if not self.photo_id:
            self.photo_id = _photo_id_for(self.path)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Photo":
        return cls(**data)


@dataclass
class Caption:
    """Generated caption + hashtag bundle for a photo."""

    photo_id: str
    text: str
    hashtags: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        if not self.hashtags:
            return self.text
        tags_line = " ".join(self.hashtags)
        return f"{self.text}\n\n{tags_line}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Caption":
        return cls(**data)


@dataclass
class Post:
    """A queued social-media post.

    The unit persisted to disk. ``photo`` and ``caption`` are flattened on
    disk for human readability but kept as nested dataclasses in memory.
    """

    post_id: str
    photo: Photo
    caption: Caption
    status: PostStatus = PostStatus.PENDING
    scheduled_for: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    posted_at: str | None = None
    meta_media_id: str | None = None
    error: str | None = None
    attempts: int = 0

    def transition(self, new_status: PostStatus) -> None:
        """Transition to ``new_status`` per the state machine.

        Raises ``IllegalStateTransition`` if disallowed.
        """
        allowed = PostStatus.allowed_from(self.status)
        if new_status not in allowed:
            raise ValueError(
                f"Illegal transition {self.status.value} -> {new_status.value}; "
                f"allowed next states: {[s.value for s in allowed] or '(none, terminal)'}"
            )
        self.status = new_status
        self.updated_at = _utc_now_iso()
        if new_status == PostStatus.POSTED:
            self.posted_at = self.updated_at
            self.error = None
        if new_status == PostStatus.FAILED:
            # Keep self.error set by caller before transition.
            pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "photo": self.photo.to_dict(),
            "caption": self.caption.to_dict(),
            "status": self.status.value,
            "scheduled_for": self.scheduled_for,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "posted_at": self.posted_at,
            "meta_media_id": self.meta_media_id,
            "error": self.error,
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Post":
        d = dict(data)
        d["photo"] = Photo.from_dict(d["photo"])
        d["caption"] = Caption.from_dict(d["caption"])
        d["status"] = PostStatus(d["status"])
        return cls(**d)

    @staticmethod
    def make_id(photo_id: str, scheduled_for: str) -> str:
        return _post_id_for(photo_id, scheduled_for)
