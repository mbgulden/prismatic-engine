"""Tests for the state machine and serialization."""

from __future__ import annotations

import json

import pytest

from prismatic.social.models import Caption, Photo, Post, PostStatus


def _make_post(status: PostStatus = PostStatus.PENDING) -> Post:
    p = Photo(path="/tmp/x.jpg", tags=["sunset"], caption_seed="hi")
    c = Caption(photo_id=p.photo_id, text="hello", hashtags=["#a", "#b"])
    return Post(
        post_id="po_test",
        photo=p,
        caption=c,
        status=status,
        scheduled_for="2026-06-26T10:00:00+00:00",
    )


@pytest.mark.parametrize(
    "start, target",
    [
        (PostStatus.PENDING, PostStatus.SCHEDULED),
        (PostStatus.PENDING, PostStatus.POSTING),
        (PostStatus.PENDING, PostStatus.FAILED),
        (PostStatus.SCHEDULED, PostStatus.POSTING),
        (PostStatus.SCHEDULED, PostStatus.FAILED),
        (PostStatus.POSTING, PostStatus.POSTED),
        (PostStatus.POSTING, PostStatus.FAILED),
        (PostStatus.FAILED, PostStatus.PENDING),
    ],
)
def test_legal_transitions(start: PostStatus, target: PostStatus) -> None:
    post = _make_post(status=start)
    post.transition(target)
    assert post.status == target


@pytest.mark.parametrize(
    "start, target",
    [
        (PostStatus.PENDING, PostStatus.POSTED),
        (PostStatus.SCHEDULED, PostStatus.POSTED),
        (PostStatus.POSTED, PostStatus.PENDING),
        (PostStatus.POSTED, PostStatus.FAILED),
        (PostStatus.POSTED, PostStatus.SCHEDULED),
        (PostStatus.FAILED, PostStatus.SCHEDULED),
    ],
)
def test_illegal_transitions_raise(start: PostStatus, target: PostStatus) -> None:
    post = _make_post(status=start)
    with pytest.raises(ValueError):
        post.transition(target)


def test_posted_sets_posted_at() -> None:
    post = _make_post(status=PostStatus.POSTING)
    post.transition(PostStatus.POSTED)
    assert post.posted_at is not None


def test_roundtrip_serialization() -> None:
    post = _make_post(status=PostStatus.SCHEDULED)
    blob = json.dumps(post.to_dict())
    rehydrated = Post.from_dict(json.loads(blob))
    assert rehydrated.post_id == post.post_id
    assert rehydrated.photo.path == post.photo.path
    assert rehydrated.caption.full_text == post.caption.full_text
    assert rehydrated.status == post.status


def test_caption_full_text_appends_hashtags() -> None:
    c = Caption(photo_id="p", text="hi", hashtags=["#a", "#b"])
    assert c.full_text == "hi\n\n#a #b"


def test_caption_full_text_without_hashtags() -> None:
    c = Caption(photo_id="p", text="hi", hashtags=[])
    assert c.full_text == "hi"


def test_photo_id_is_stable_for_same_path() -> None:
    p1 = Photo(path="/tmp/a.jpg")
    p2 = Photo(path="/tmp/a.jpg")
    assert p1.photo_id == p2.photo_id
    assert p1.photo_id.startswith("ph_")


def test_post_id_changes_with_slot() -> None:
    pid_a = Post.make_id("ph_x", "2026-06-26T10:00:00+00:00")
    pid_b = Post.make_id("ph_x", "2026-06-26T14:00:00+00:00")
    assert pid_a != pid_b


def test_illegal_transition_via_queue_raises(empty_queue) -> None:
    post = _make_post()
    empty_queue.add(post)
    with pytest.raises(Exception):
        empty_queue.transition(post.post_id, PostStatus.POSTED)
