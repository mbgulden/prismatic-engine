"""Tests for the JSON-backed QueueStore."""

from __future__ import annotations

from pathlib import Path

from prismatic.social.models import Caption, Photo, Post, PostStatus
from prismatic.social.queue_store import QueueStore


def _post(
    pid: str,
    status: PostStatus = PostStatus.PENDING,
    slot: str = "2026-06-26T10:00:00+00:00",
) -> Post:
    photo = Photo(path=f"/tmp/{pid}.jpg", photo_id=f"ph_{pid}")
    cap = Caption(photo_id=photo.photo_id, text="hi", hashtags=["#t"])
    return Post(
        post_id=pid, photo=photo, caption=cap, status=status, scheduled_for=slot
    )


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    store = QueueStore.load(tmp_path / "nope.json")
    assert len(store) == 0


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = QueueStore.load(tmp_path / "q.json")
    store.add(_post("a"))
    store.add(_post("b", status=PostStatus.POSTED))
    store.save()

    fresh = QueueStore.load(tmp_path / "q.json")
    assert len(fresh) == 2
    assert fresh.by_status(PostStatus.POSTED)[0].post_id == "b"


def test_corrupt_file_is_quarantined(tmp_path: Path) -> None:
    p = tmp_path / "q.json"
    p.write_text("not json {", encoding="utf-8")
    store = QueueStore.load(p)
    assert len(store) == 0
    # A .corrupt-* sibling now exists.
    siblings = list(p.parent.glob("q.json.corrupt-*"))
    assert siblings


def test_add_is_idempotent_by_id(tmp_path: Path) -> None:
    store = QueueStore.load(tmp_path / "q.json")
    store.add(_post("a"))
    store.add(_post("a"))  # replace
    assert len(store) == 1


def test_due_returns_only_scheduled_in_past(tmp_path: Path) -> None:
    store = QueueStore.load(tmp_path / "q.json")
    past = _post("past", slot="2026-06-26T10:00:00+00:00", status=PostStatus.SCHEDULED)
    future = _post(
        "future", slot="2099-01-01T10:00:00+00:00", status=PostStatus.SCHEDULED
    )
    posted = _post("posted", slot="2026-06-26T09:00:00+00:00", status=PostStatus.POSTED)
    store.add(past)
    store.add(future)
    store.add(posted)
    due = store.due()
    assert [p.post_id for p in due] == ["past"]


def test_transition_updates_status(tmp_path: Path) -> None:
    store = QueueStore.load(tmp_path / "q.json")
    store.add(_post("a", status=PostStatus.SCHEDULED))
    store.transition("a", PostStatus.POSTING)
    assert store.get("a").status == PostStatus.POSTING


def test_posted_photo_ids_returns_only_posted(tmp_path: Path) -> None:
    store = QueueStore.load(tmp_path / "q.json")
    store.add(_post("a", status=PostStatus.POSTED))
    store.add(_post("b", status=PostStatus.PENDING))
    assert {p.photo.photo_id for p in store.all() if p.status == PostStatus.POSTED} == {
        "ph_a"
    }


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "q.json"
    store = QueueStore.load(deep)
    store.add(_post("a"))
    store.save()
    assert deep.exists()
