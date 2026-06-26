"""Tests for the caption generator."""

from __future__ import annotations

from pathlib import Path

from prismatic.social.captioner import CaptionGenerator
from prismatic.social.config import SocialConfig
from prismatic.social.models import Photo


def _photo(tmp_path: Path, name: str, tags: list[str], seed: str = "") -> Photo:
    p = tmp_path / name
    p.write_bytes(b"x")
    return Photo(path=str(p), tags=tags, caption_seed=seed)


def test_caption_has_hashtags(cfg: SocialConfig, tmp_path) -> None:
    p = _photo(tmp_path, "a.jpg", tags=["sunset"])
    cap = CaptionGenerator(cfg).generate(p)
    assert cap.photo_id == p.photo_id
    assert cap.text
    assert cap.hashtags
    assert all(h.startswith("#") for h in cap.hashtags)


def test_caption_hook_for_sunset(cfg: SocialConfig, tmp_path) -> None:
    p = _photo(tmp_path, "a.jpg", tags=["sunset"])
    cap = CaptionGenerator(cfg).generate(p)
    assert "Golden hour" in cap.text or "Sunset" in cap.text or "Chasing" in cap.text


def test_caption_fallback_hook_when_no_tags(cfg: SocialConfig, tmp_path) -> None:
    p = _photo(tmp_path, "b.jpg", tags=[])
    cap = CaptionGenerator(cfg).generate(p)
    assert cap.text


def test_caption_includes_seed(cfg: SocialConfig, tmp_path) -> None:
    p = _photo(tmp_path, "c.jpg", tags=[], seed="Hello, world.")
    cap = CaptionGenerator(cfg).generate(p)
    assert "Hello, world." in cap.text


def test_caption_min_hashtag_count(cfg: SocialConfig, tmp_path) -> None:
    p = _photo(tmp_path, "d.jpg", tags=[])
    cap = CaptionGenerator(cfg).generate(p)
    assert len(cap.hashtags) >= 5


def test_caption_max_hashtag_count(cfg: SocialConfig, tmp_path) -> None:
    p = _photo(tmp_path, "e.jpg", tags=[f"tag{i}" for i in range(50)])
    cap = CaptionGenerator(cfg).generate(p)
    assert len(cap.hashtags) <= 15


def test_hashtag_normalization(cfg: SocialConfig) -> None:
    norm = CaptionGenerator._normalize_hashtag
    assert norm("hello") == "#hello"
    assert norm("#hello") == "#hello"
    assert norm("##hello!") == "#hello"
    assert norm("hello world") == "#helloworld"
    assert norm("") == ""
    assert norm("a") == "#a"  # 1 char content is fine — `#a` is 2 chars total
    assert norm("#") == ""


def test_caption_dedup(cfg: SocialConfig, tmp_path) -> None:
    p = _photo(tmp_path, "f.jpg", tags=["growthwebdev", "growthwebdev", "ocean"])
    cap = CaptionGenerator(cfg).generate(p)
    seen = set()
    for h in cap.hashtags:
        assert h not in seen
        seen.add(h)
