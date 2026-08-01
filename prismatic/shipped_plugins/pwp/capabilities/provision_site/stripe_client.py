"""stripe_client — minimal Stripe REST API v1 client for PWP provision_site Phase 4.

Mirrors the shape of cloudflare_client.py / vercel_client.py so step
implementations can swap between platforms with the same idioms.

Stripe's REST API uses Bearer auth (`Authorization: Bearer sk_live_xxx`).
The base URL is https://api.stripe.com/v1. Auth keys can be either:
  - Standard secret keys (sk_live_*, sk_test_*) — full account access.
  - Restricted keys (rk_live_*) — scoped access defined in the Stripe
    Dashboard; RECOMMENDED for the PWP use case (read-only on products,
    prices, charges, subscriptions).

We support BOTH via `STRIPE_RESTRICTED_KEY` (preferred) and `STRIPE_API_KEY`
(fallback). The `validate` call hits `/v1/balance` which works for both
key types as long as the read permission is granted.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

STRIPE_API_URL = "https://api.stripe.com/v1"


class StripeError(RuntimeError):
    """Raised when the Stripe API returns an error response."""
    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        error_code: str | None = None,
        error_type: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.error_type = error_type


@dataclass(frozen=True)
class StripeProduct:
    """A typed view of a Stripe Product."""
    id: str
    name: str
    active: bool
    description: str = ""
    metadata: dict[str, str] = None  # type: ignore[assignment]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> StripeProduct:
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            active=bool(data.get("active", True)),
            description=data.get("description") or "",
            metadata=data.get("metadata") or {},
        )


@dataclass(frozen=True)
class StripePrice:
    """A typed view of a Stripe Price."""
    id: str
    product_id: str
    currency: str
    unit_amount: int  # cents
    active: bool
    recurring_interval: str | None = None  # 'month' | 'year' | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> StripePrice:
        recurring = data.get("recurring") or {}
        return cls(
            id=data["id"],
            product_id=data.get("product", ""),
            currency=data.get("currency", "usd"),
            unit_amount=int(data.get("unit_amount") or 0),
            active=bool(data.get("active", True)),
            recurring_interval=recurring.get("interval"),
        )


class StripeClient:
    """Minimal Stripe REST API v1 client.

    Usage:
        client = StripeClient.from_env()
        products = client.list_products()
    """

    def __init__(
        self,
        api_key: str,
        *,
        account_id: str | None = None,
        api_url: str = STRIPE_API_URL,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        token_source: str = "explicit",
    ):
        if not api_key:
            raise ValueError("StripeClient requires a non-empty api_key")
        self.api_key = api_key
        self.account_id = account_id
        self.api_url = api_url
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.token_source = token_source

    # -- factory --------------------------------------------------------
    @classmethod
    def from_env(cls) -> StripeClient:
        """Construct from environment variables.

        Precedence: STRIPE_RESTRICTED_KEY > STRIPE_API_KEY > STRIPE_SECRET_KEY.
        STRIPE_ACCOUNT_ID is optional (used for Connect accounts).

        Falls back to auth_loader.get_secret('stripe_secret_key') which
        discovers credentials in the active Hermes profile's .env,
        project .env files, etc.
        """
        api_key = (
            os.environ.get("STRIPE_RESTRICTED_KEY")
            or os.environ.get("STRIPE_API_KEY")
            or os.environ.get("STRIPE_SECRET_KEY")
        )
        source = (
            "STRIPE_RESTRICTED_KEY" if os.environ.get("STRIPE_RESTRICTED_KEY")
            else "STRIPE_API_KEY" if os.environ.get("STRIPE_API_KEY")
            else "STRIPE_SECRET_KEY" if os.environ.get("STRIPE_SECRET_KEY")
            else None
        )
        if not api_key:
            # Fall back to auth_loader
            from . import auth_loader
            result = auth_loader.get_secret("stripe_secret_key")
            if result.found:
                api_key = result.value
                source = f"auth_loader:{result.source}"
                result.export_to_env()  # make available to subprocesses
        if not api_key:
            raise ValueError(
                "No Stripe API key configured. Set STRIPE_RESTRICTED_KEY "
                "(preferred) or STRIPE_API_KEY or STRIPE_SECRET_KEY, OR "
                "register via: pwp-kpi-tracker auth register "
                "--type stripe_secret_key --value <sk_...>"
            )
        return cls(
            api_key,
            account_id=os.environ.get("STRIPE_ACCOUNT_ID"),
            token_source=source or "explicit",
        )

    # -- core HTTP ------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a Stripe REST call. Stripe uses application/x-www-form-urlencoded
        for write requests and returns JSON."""
        url = f"{self.api_url}{path}"
        body: bytes | None = None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "pwp-provision-site/0.1",
        }
        if method in ("POST", "PUT", "PATCH") and params:
            # Use form-encoded body
            from urllib.parse import urlencode
            body = urlencode(params).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif params:
            # GET — append as query string
            from urllib.parse import urlencode
            qs = urlencode(params)
            url = f"{url}?{qs}"

        last_err: Exception | None = None
        total_attempts = max(1, self.max_retries + 1)
        for attempt in range(total_attempts):
            try:
                req = urllib.request.Request(
                    url, data=body, headers=headers, method=method,
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                last_err = e
                err_body = ""
                try:
                    err_body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                try:
                    err_payload = json.loads(err_body) if err_body else {}
                except Exception:
                    err_payload = {}
                stripe_err = err_payload.get("error", {}) if isinstance(err_payload, dict) else {}
                # Retry on 429 (rate-limited) or 5xx (server error).
                if e.code in (429, 500, 502, 503, 504) and attempt < total_attempts - 1:
                    time.sleep(self.retry_backoff * (2 ** attempt))
                    continue
                raise StripeError(
                    f"Stripe API {method} {path} -> HTTP {e.code}: "
                    f"{stripe_err.get('message', err_body[:200] or str(e))}",
                    status=e.code,
                    error_code=stripe_err.get("code"),
                    error_type=stripe_err.get("type"),
                ) from e
            except urllib.error.URLError as e:
                last_err = e
                if attempt < total_attempts - 1:
                    time.sleep(self.retry_backoff * (2 ** attempt))
                    continue
                raise StripeError(
                    f"Stripe API connection error: {e.reason}",
                ) from e

        raise StripeError(
            f"Stripe API {method} {path} failed after {total_attempts} "
            f"attempts: {last_err!r}"
        )

    # -- public API -----------------------------------------------------
    def validate(self) -> dict[str, Any]:
        """Validate the API key by hitting /v1/balance.

        Returns the parsed balance payload. Raises StripeError on auth failure.
        """
        return self._request("GET", "/balance")

    def list_products(
        self,
        *,
        active: bool | None = None,
        limit: int = 50,
    ) -> list[StripeProduct]:
        """List products in the account."""
        params: dict[str, Any] = {"limit": int(limit)}
        if active is not None:
            params["active"] = "true" if active else "false"
        data = self._request("GET", "/products", params=params)
        return [StripeProduct.from_api(p) for p in data.get("data", [])]

    def list_prices(
        self,
        *,
        product_id: str | None = None,
        active: bool | None = None,
        limit: int = 50,
    ) -> list[StripePrice]:
        """List prices, optionally filtered by product."""
        params: dict[str, Any] = {"limit": int(limit)}
        if product_id:
            params["product"] = product_id
        if active is not None:
            params["active"] = "true" if active else "false"
        data = self._request("GET", "/prices", params=params)
        return [StripePrice.from_api(p) for p in data.get("data", [])]
