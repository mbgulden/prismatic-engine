"""Tests for the Meta client + dry-run shim."""

from __future__ import annotations


import pytest

from prismatic.social.config import SocialConfig
from prismatic.social.exceptions import AuthError
from prismatic.social.meta_client import (
    DryRunMetaClient,
    MetaGraphClient,
    build_meta_client,
)


def test_dry_run_client_works_without_creds(cfg: SocialConfig) -> None:
    cli = DryRunMetaClient(cfg)
    res = cli.publish(image_url="https://example.com/x.jpg", caption="hello #world")
    assert res.media_id.startswith("dry_")
    assert res.raw["dry_run"] is True


def test_dry_run_is_deterministic_for_same_inputs(cfg: SocialConfig) -> None:
    cli = DryRunMetaClient(cfg)
    a = cli.publish(image_url="u", caption="c").media_id
    b = cli.publish(image_url="u", caption="c").media_id
    assert a == b


def test_build_meta_client_picks_dry_when_no_creds(cfg: SocialConfig) -> None:
    assert isinstance(build_meta_client(cfg), DryRunMetaClient)


def test_build_meta_client_picks_live_when_creds_present(tmp_path) -> None:
    cfg = SocialConfig(
        media_library=tmp_path,
        queue_path=tmp_path / "queue.json",
        daily_limit=1,
        dry_run=False,
        disable_posting=False,
        hashtags=["#x"],
        cron_hours=["10:00"],
        meta_access_token="tok",
        meta_business_id="12345",
        meta_api_version="v21.0",
    )
    cli = build_meta_client(cfg)
    assert isinstance(cli, MetaGraphClient)


def test_meta_graph_client_requires_creds(tmp_path) -> None:
    cfg = SocialConfig(
        media_library=tmp_path,
        queue_path=tmp_path / "queue.json",
        daily_limit=1,
        dry_run=False,
        disable_posting=False,
        hashtags=["#x"],
        cron_hours=["10:00"],
        meta_access_token=None,
        meta_business_id="12345",
        meta_api_version="v21.0",
    )
    with pytest.raises(AuthError):
        MetaGraphClient(cfg)


def test_meta_graph_client_requires_both_fields(tmp_path) -> None:
    cfg = SocialConfig(
        media_library=tmp_path,
        queue_path=tmp_path / "queue.json",
        daily_limit=1,
        dry_run=False,
        disable_posting=False,
        hashtags=["#x"],
        cron_hours=["10:00"],
        meta_access_token="tok",
        meta_business_id=None,
        meta_api_version="v21.0",
    )
    with pytest.raises(AuthError):
        MetaGraphClient(cfg)
