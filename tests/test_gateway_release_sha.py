"""Tests for the gateway /health release-SHA field (release_info.get_running_sha)."""

from pathlib import Path

import pytest

from prismatic.gateway.release_info import ENV_VAR, get_running_sha


def _release_anchor(tmp_path: Path, sha: str) -> Path:
    """Build a fake <versions>/prismatic-engine-<sha>/prismatic/gateway/ tree."""
    anchor = tmp_path / "versions" / f"prismatic-engine-{sha}" / "prismatic" / "gateway"
    anchor.mkdir(parents=True)
    return anchor


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


def test_env_var_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "deadbeef1234")
    assert get_running_sha(anchor=_release_anchor(tmp_path, "abc123")) == "deadbeef1234"


def test_derives_sha_from_release_dir(tmp_path):
    anchor = _release_anchor(tmp_path, "e6335d783a40")
    assert get_running_sha(anchor=anchor) == "e6335d783a40"


def test_adversarial_different_release_dir_returns_different_sha(tmp_path):
    """Pointing the gateway at another release must report the other SHA."""
    first = _release_anchor(tmp_path, "aaaaaaaaaaaa")
    second = _release_anchor(tmp_path, "bbbbbbbbbbbb")
    assert get_running_sha(anchor=first) == "aaaaaaaaaaaa"
    assert get_running_sha(anchor=second) == "bbbbbbbbbbbb"


def test_plain_checkout_returns_none(tmp_path):
    anchor = tmp_path / "some-checkout" / "prismatic" / "gateway"
    anchor.mkdir(parents=True)
    assert get_running_sha(anchor=anchor) is None


def test_symlinked_release_resolves(tmp_path):
    target = _release_anchor(tmp_path, "c9d055b31234")
    link = tmp_path / "releases" / "prismatic-engine"
    link.parent.mkdir(parents=True)
    link.symlink_to(target.parents[1], target_is_directory=True)
    assert get_running_sha(anchor=link / "prismatic" / "gateway") == "c9d055b31234"
