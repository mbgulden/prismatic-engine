"""Meta Graph API client + dry-run shim.

Production path
---------------
``MetaGraphClient`` implements the *Content Publishing* flow documented at
https://developers.facebook.com/docs/instagram-api/guides/content-publishing :

  1. ``POST /{ig-user-id}/media``         (image_url + caption + is_carousel_item)
  2. ``POST /{ig-user-id}/media_publish`` (creation_id)
  3. Inspect response for ``media_id`` and any error envelope.

Dry-run path
------------
``DryRunMetaClient`` implements the same ``publish`` method but does no
network I/O and returns a deterministic synthetic media id derived from
the caption.  It exists so cron and CI can exercise the full pipeline
without live credentials.

Both clients raise ``MetaAPIError`` subclasses so the pipeline can apply
the right retry semantics without inspecting the underlying error code.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from prismatic.social.config import SocialConfig
from prismatic.social.exceptions import AuthError, MetaAPIError, RateLimitError


def _classify_http(status: int, body: str) -> Exception:
    snippet = body[:240] if body else ""
    if status in (401, 403):
        return AuthError(f"Meta auth error {status}: {snippet}")
    if status == 429:
        return RateLimitError(f"Meta rate-limited (429): {snippet}")
    return MetaAPIError(f"Meta API error {status}: {snippet}")


@dataclass
class MetaPublishResult:
    media_id: str
    raw: dict[str, Any]


class MetaGraphClient:
    """Thin wrapper around the Instagram Graph ``media`` + ``media_publish`` flow."""

    def __init__(self, config: SocialConfig, *, timeout: float = 30.0) -> None:
        if not config.live_meta_available():
            raise AuthError(
                "MetaGraphClient requires META_ACCESS_TOKEN and META_BUSINESS_ID"
            )
        self._cfg = config
        self._timeout = timeout

    # -- public API --------------------------------------------------------

    def publish(
        self, *, image_url: str, caption: str, is_carousel_item: bool = False
    ) -> MetaPublishResult:
        """Two-step publish: create container, then publish container.

        ``image_url`` must be publicly reachable — Meta fetches it server-side.
        """
        container_id = self._create_media_container(
            image_url=image_url, caption=caption, is_carousel_item=is_carousel_item
        )
        result = self._publish_container(container_id)
        return result

    # -- internals ---------------------------------------------------------

    @property
    def _base(self) -> str:
        return f"https://graph.facebook.com/{self._cfg.meta_api_version}"

    def _create_media_container(
        self, *, image_url: str, caption: str, is_carousel_item: bool
    ) -> str:
        params = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self._cfg.meta_access_token or "",
        }
        if is_carousel_item:
            params["is_carousel_item"] = "true"
        url = (
            f"{self._base}/{self._cfg.meta_business_id}/media?"
            + urllib.parse.urlencode(params)
        )
        data = self._post_json(url)
        container_id = data.get("id")
        if not container_id:
            raise MetaAPIError(f"no media container id in response: {data}")
        return str(container_id)

    def _publish_container(self, container_id: str) -> MetaPublishResult:
        params = {
            "creation_id": container_id,
            "access_token": self._cfg.meta_access_token or "",
        }
        url = (
            f"{self._base}/{self._cfg.meta_business_id}/media_publish?"
            + urllib.parse.urlencode(params)
        )
        data = self._post_json(url)
        media_id = data.get("id") or container_id
        return MetaPublishResult(media_id=str(media_id), raw=data)

    def _post_json(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(url, method="POST")
        req.add_header("User-Agent", self._cfg.user_agent)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as e:
            body = (
                e.read().decode("utf-8", errors="replace")
                if hasattr(e, "read")
                else str(e)
            )
            raise _classify_http(e.code, body) from e
        except urllib.error.URLError as e:
            raise MetaAPIError(f"network error talking to Meta: {e}") from e
        if status >= 400:
            raise _classify_http(status, body)
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            raise MetaAPIError(f"non-JSON response from Meta: {body[:200]!r}") from e
        if isinstance(parsed, dict) and "error" in parsed:
            err = parsed["error"]
            raise MetaAPIError(f"Meta error envelope: {err}")
        return parsed if isinstance(parsed, dict) else {"raw": parsed}


class DryRunMetaClient:
    """No-network stand-in. Useful for cron dry-runs and tests."""

    def __init__(self, config: SocialConfig) -> None:
        self._cfg = config

    def publish(
        self, *, image_url: str, caption: str, is_carousel_item: bool = False
    ) -> MetaPublishResult:
        del is_carousel_item  # unused in dry-run
        seed = f"{image_url}|{caption}".encode("utf-8")
        media_id = "dry_" + hashlib.sha1(seed).hexdigest()[:24]
        return MetaPublishResult(
            media_id=media_id, raw={"dry_run": True, "media_id": media_id}
        )


def build_meta_client(config: SocialConfig) -> MetaGraphClient | DryRunMetaClient:
    """Factory: live client when creds + not dry-run, dry-run shim otherwise."""
    if config.dry_run or not config.live_meta_available():
        return DryRunMetaClient(config)
    return MetaGraphClient(config)
