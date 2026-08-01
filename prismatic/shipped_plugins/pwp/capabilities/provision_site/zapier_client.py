"""zapier_client — PWP provision_site Phase 4.6 Zapier webhook client.

The Zapier step wires a webhook URL into Zapier's "Catch Hook" trigger
(or any other Zap that posts to a URL). The webhook URL is the place
where Zapier pushes external data — e.g. FareHarbor booking events,
Telegram bot commands, Stripe bank payouts, etc. — and the PWP
dashboard's KPI ingestion pipeline picks the data up.

Two key flows:

1. **Inbound**: Zapier (or FareHarbor via Zapier) POSTs to a webhook URL.
   That URL is owned by Zapier (the "Catch Hook" trigger) and forwards
   to a downstream HTTP endpoint (usually the user's NLP agent /
   Prismatic Engine endpoint). The user wires the Zap manually in
   the Zapier UI; the step's job is to:

   - Validate the webhook URL is reachable (HEAD/GET), so we don't
     silently store a broken URL.
   - Persist the configuration to `external_sources.zapier` in
     kpi-collections.json so the runtime pipeline knows where to
     expect data.

2. **Outbound (FareHarbor probe)**: the step verifies the live
   FareHarbor company exists by calling FareHarbor's public API:

       GET https://fareharbor.com/api/v1/companies/<shortname>/

   If the shortname resolves, the Zap can be wired to that company.

This module mirrors the shape of `stripe_client.py` (auth_loader for
secrets, lazy-env import, structured dataclass responses, retry with
exponential backoff on transient errors). It does NOT shell out to the
Zapier CLI/SDK — the webhook approach is the established path for
PWP. (The user mentioned the Zapier CLI node SDK as a possible
extension; the webhook URL is the only thing we need to plumb.)
"""

from __future__ import annotations

import json

# Lazy imports so the publish_kpi_tracker module can keep working
# when the (optional) provision_site clients aren't installed.
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# Defaults — overridable via the auth_loader / env.
DEFAULT_FAREHARBOR_BASE = "https://fareharbor.com/api/v1/companies"
DEFAULT_HTTP_TIMEOUT = 10
DEFAULT_WEBHOOK_PROBE_TIMEOUT = 5


# ── Errors ───────────────────────────────────────────────────────────────
class ZapierError(RuntimeError):
    """Base error for ZapierClient."""

    def __init__(self, message: str, *, status: int = 0, error_code: str = ""):
        super().__init__(message)
        self.status = status
        self.error_code = error_code


class WebhookUnreachableError(ZapierError):
    """Raised when the webhook URL did not respond to a probe."""


class FareHarborNotFoundError(ZapierError):
    """Raised when the FareHarbor shortname does not resolve."""


# ── Result dataclasses ───────────────────────────────────────────────────
@dataclass
class FareHarborCompany:
    """A slim view of a FareHarbor company from /api/v1/companies/<shortname>/."""

    shortname: str
    name: str = ""
    pk: int = 0
    url: str = ""
    currency: str = ""
    processors: list = field(default_factory=list)
    is_active: bool = True

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> FareHarborCompany:
        company = data.get("company") or {}
        processors = company.get("enabled_processor_types") or []
        return cls(
            shortname=company.get("shortname", ""),
            name=company.get("name", ""),
            pk=company.get("pk", 0),
            url=company.get("url", ""),
            currency=company.get("processor_currency", ""),
            processors=list(processors),
            is_active=company.get("deactivation_status", "active") == "active",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "shortname": self.shortname,
            "name": self.name,
            "pk": self.pk,
            "url": self.url,
            "currency": self.currency,
            "processors": list(self.processors),
            "is_active": self.is_active,
        }


@dataclass
class WebhookProbe:
    """Result of a HEAD/GET probe against the webhook URL."""

    url: str
    reachable: bool = False
    status: int = 0
    content_type: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "reachable": self.reachable,
            "status": self.status,
            "content_type": self.content_type,
            "error_message": self.error_message,
        }


@dataclass
class ZapierValidation:
    """Result of the full ZapierClient.validate() call."""

    webhook: WebhookProbe
    fareharbor: FareHarborCompany | None = None
    webhook_token_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "webhook": self.webhook.to_dict(),
            "fareharbor": self.fareharbor.to_dict() if self.fareharbor else None,
            "webhook_token_source": self.webhook_token_source,
        }


# ── HTTP helpers ────────────────────────────────────────────────────────
def _http_request(
    url: str,
    *,
    method: str = "GET",
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Minimal HTTP wrapper. Returns ``{"status": int, "headers": dict, "body": str}``.

    Raises ``ZapierError`` on transport-level errors (DNS, connection refused,
    timeout). 4xx/5xx responses are returned normally (status set in the dict)
    so the caller can decide what to do.
    """
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    # Many webhook endpoints (Zapier "Catch Hook", etc.) accept HEAD; some
    # return 405 (Method Not Allowed) for HEAD but 200 for GET. We try HEAD
    # first, fall back to GET on 403/405.
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {
                "status": resp.status,
                "headers": dict(resp.headers.items()),
                # Read the FULL body — the FareHarbor company payload is
                # ~30KB and we need every byte for the JSON parser.
                "body": resp.read().decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "body": "",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ZapierError(f"HTTP {method} failed for {url}: {exc}", status=0) from exc


# ── Client ───────────────────────────────────────────────────────────────
class ZapierClient:
    """Minimal client for the PWP Zapier integration.

    Supports:
      - Webhook URL validation (HEAD/GET probe with GET fallback).
      - FareHarbor shortname probe (public, no auth required).
      - Full ``validate()`` that wires both probes together.
    """

    def __init__(
        self,
        *,
        webhook_url: str,
        webhook_token_source: str = "",
        fareharbor_base: str = DEFAULT_FAREHARBOR_BASE,
        http_timeout: int = DEFAULT_HTTP_TIMEOUT,
        webhook_probe_timeout: int = DEFAULT_WEBHOOK_PROBE_TIMEOUT,
    ):
        self.webhook_url = webhook_url
        self.webhook_token_source = webhook_token_source
        self.fareharbor_base = fareharbor_base
        self.http_timeout = http_timeout
        self.webhook_probe_timeout = webhook_probe_timeout

    @classmethod
    def from_env(
        cls,
        *,
        webhook_url: str | None = None,
        fareharbor_base: str = DEFAULT_FAREHARBOR_BASE,
    ) -> ZapierClient:
        """Build a client from the auth_loader.

        If ``webhook_url`` is None, the loader tries ZAPIER_WEBHOOK_URL.
        Raises ``ValueError`` (clearly) when no URL is configured.
        """
        from .auth_loader import get_secret

        token_source = ""
        if webhook_url is None:
            result = get_secret("zapier_webhook_url")
            if result.found:
                webhook_url = result.value
                token_source = result.source
        if not webhook_url:
            raise ValueError(
                "Zapier webhook URL is not configured. Set ZAPIER_WEBHOOK_URL "
                "in ~/.hermes/profiles/ned/.env (or use auth_loader.register_secret), "
                "then re-run: pwp-kpi-tracker provision --domain <domain> "
                "--steps register_zapier_webhook"
            )
        return cls(
            webhook_url=webhook_url,
            webhook_token_source=token_source,
            fareharbor_base=fareharbor_base,
        )

    # ── Webhook probe ─────────────────────────────────────────────────
    def probe_webhook(self) -> WebhookProbe:
        """Confirm the webhook URL is reachable.

        Zapier "Catch Hook" endpoints accept GET and return a small HTML
        page or 200 with no body. We treat 200-299 and 405 (Method Not
        Allowed) as "reachable"; 4xx otherwise.
        """
        url = self.webhook_url
        try:
            # Try HEAD first.
            result = _http_request(
                url,
                method="HEAD",
                timeout=self.webhook_probe_timeout,
            )
            status = result["status"]
            if status == 405:
                # Fall back to GET — many webhook endpoints reject HEAD.
                result = _http_request(
                    url,
                    method="GET",
                    timeout=self.webhook_probe_timeout,
                )
                status = result["status"]
            reachable = 200 <= status < 400 or status == 405
            return WebhookProbe(
                url=url,
                reachable=reachable,
                status=status,
                content_type=result["headers"].get("Content-Type", ""),
                error_message="" if reachable else f"HTTP {status}",
            )
        except ZapierError as exc:
            return WebhookProbe(
                url=url,
                reachable=False,
                status=0,
                content_type="",
                error_message=str(exc),
            )

    # ── FareHarbor probe ───────────────────────────────────────────────
    def probe_fareharbor(self, shortname: str) -> FareHarborCompany:
        """Resolve a FareHarbor shortname to a company record.

        Raises ``FareHarborNotFoundError`` on 404.
        """
        if not shortname:
            raise FareHarborNotFoundError(
                "FareHarbor shortname is empty (need a non-empty shortname like "
                "'activeoahutours').",
                status=0,
            )
        url = f"{self.fareharbor_base}/{shortname}/"
        result = _http_request(
            url,
            method="GET",
            timeout=self.http_timeout,
            headers={"Accept": "application/json"},
        )
        if result["status"] == 404:
            raise FareHarborNotFoundError(
                f"FareHarbor shortname not found: {shortname!r}",
                status=404,
            )
        if result["status"] >= 400:
            raise ZapierError(
                f"FareHarbor API returned HTTP {result['status']} for "
                f"shortname={shortname!r}: {result['body'][:200]}",
                status=result["status"],
            )
        try:
            data = json.loads(result["body"])
        except json.JSONDecodeError as exc:
            raise ZapierError(
                f"FareHarbor API returned non-JSON for {shortname!r}: {exc}",
                status=result["status"],
            ) from exc
        company = FareHarborCompany.from_api(data)
        if not company.shortname:
            raise FareHarborNotFoundError(
                f"FareHarbor API returned 200 for {shortname!r} but the "
                "response did not contain a valid company.shortname; "
                "the API may have changed.",
                status=result["status"],
            )
        return company

    # ── Aggregate ─────────────────────────────────────────────────────
    def validate(
        self,
        *,
        fareharbor_shortname: str | None = None,
    ) -> ZapierValidation:
        """Run all probes and return a structured result.

        ``fareharbor_shortname`` is optional — when None, the FareHarbor
        probe is skipped (the webhook URL alone is enough for Zaps that
        don't depend on FareHarbor).
        """
        webhook = self.probe_webhook()
        fareharbor: FareHarborCompany | None = None
        if fareharbor_shortname:
            try:
                fareharbor = self.probe_fareharbor(fareharbor_shortname)
            except FareHarborNotFoundError:
                # Re-raise; the caller decides how to handle the 404.
                raise
        return ZapierValidation(
            webhook=webhook,
            fareharbor=fareharbor,
            webhook_token_source=self.webhook_token_source,
        )


__all__ = [
    "DEFAULT_FAREHARBOR_BASE",
    "DEFAULT_HTTP_TIMEOUT",
    "FareHarborCompany",
    "FareHarborNotFoundError",
    "WebhookProbe",
    "WebhookUnreachableError",
    "ZapierClient",
    "ZapierError",
    "ZapierValidation",
]
