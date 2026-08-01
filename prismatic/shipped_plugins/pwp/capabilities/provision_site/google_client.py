"""google_client — minimal Google APIs client for PWP provision_site Phase 2.

Covers three Google APIs for new-site onboarding:
  1. Google Analytics Admin API v1beta  — create GA4 property + web stream,
     return measurement ID (G-XXXXXX).
  2. Google Tag Manager API v2         — create GTM container + workspace,
     return container ID (GTM-XXXXXXX).
  3. Google Search Console API v1      — site verification (rare; usually
     done via DNS TXT, which uses Cloudflare here).

Auth model (Phase 2):
  Each Google API accepts either OAuth2 user tokens OR a service-account
  JWT. For new-site bootstrapping we use a single SHARED service
  account that has been pre-granted:
    - analytics.edit on the GA4 Account (so we can create properties
      under it without per-site OAuth)
    - tagmanager.edit on the GTM Account
    - No GSC API access (we do GSC verification via DNS TXT using
      Cloudflare's API, which is what this provisioner already uses
      for the verify_domain step — same technique, different TXT
      record value).

  The service account JSON is loaded from $GOOGLE_SA_JSON env var
  (path to a JSON key file) OR $GOOGLE_SA_INLINE (the JSON content
  itself). The corresponding email gets written to the site's `.env`
  as `GA4_SERVICE_ACCOUNT=<email>` for the site to use later when
  reading from GA4 Data API.

Design choices:
  - Thin `requests`-based wrapper, mirroring CloudflareClient shape
    (`from_env()`, `zone_create`-style helpers).
  - All API responses parsed into typed dataclasses.
  - Retries + exponential backoff for 429/5xx.
  - No third-party SDK (the official `google-api-python-client` would
    work but pulls 20+ transitive deps; we only need 3 endpoints).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

# --- Errors ---------------------------------------------------------------

class GoogleError(Exception):
    """Generic Google API error. Carries the parsed message + status code."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        api: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api = api


class GoogleAuthError(GoogleError):
    """Service-account / OAuth credential problem."""


class GoogleQuotaError(GoogleError):
    """429 / quota-exceeded. Caller may retry with backoff."""


# --- Response shapes ------------------------------------------------------

@dataclass
class GA4Property:
    """Result of `ga4_property_create()`."""
    name: str          # e.g. "properties/123456789"
    property_id: str   # numeric id "123456789"
    measurement_id: str  # "G-ABC123DEF4" (from data stream)
    data_stream_name: str
    create_time: str = ""


@dataclass
class GTMContainer:
    """Result of `gtm_container_create()`."""
    public_id: str       # e.g. "GTM-P5H2XK8"
    account_id: str
    container_id: str    # numeric
    container_name: str


@dataclass
class GSCSite:
    """Result of `gsc_site_add()`."""
    site_url: str        # e.g. "sc-domain:example.com" or "https://example.com/"
    permission_level: str  # "siteOwner" / "siteFullUser" / etc.


# --- Service Account JWT auth --------------------------------------------

def _load_service_account(
    json_path: str | None = None,
    inline: str | None = None,
) -> dict[str, Any] | None:
    """Load + validate a Google credentials JSON key.

    Returns the parsed key dict, or None if no credentials are
    configured. Raises GoogleAuthError only on parse / validation
    errors when a candidate is found but malformed.

    The caller (`GoogleClient.from_env`) is responsible for falling
    back to auth_loader when this returns None.
    """
    candidates_json_path = json_path or os.environ.get("GOOGLE_SA_JSON", "").strip()
    candidates_inline = inline or os.environ.get("GOOGLE_SA_INLINE", "").strip()
    if not candidates_json_path and not candidates_inline:
        return None  # no candidates; caller should fall back to auth_loader
    if candidates_inline:
        try:
            sa = json.loads(candidates_inline)
        except json.JSONDecodeError as e:
            raise GoogleAuthError(f"GOOGLE_SA_INLINE is not valid JSON: {e}")
    else:
        p = Path(candidates_json_path)
        if not p.exists():
            raise GoogleAuthError(f"GOOGLE_SA_JSON points to nonexistent file: {p}")
        try:
            sa = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise GoogleAuthError(f"GOOGLE_SA_JSON file is not valid JSON: {e}")
    # Minimal validation — branch on type.
    if sa.get("type") == "service_account":
        required = ("type", "client_email", "private_key", "token_uri")
        missing = [k for k in required if k not in sa]
        if missing:
            raise GoogleAuthError(
                "Service-account JSON missing required fields: "
                + ", ".join(missing)
            )
    elif sa.get("type") == "authorized_user":
        required = ("type", "client_id", "client_secret", "refresh_token")
        missing = [k for k in required if k not in sa]
        if missing:
            raise GoogleAuthError(
                "OAuth user-credentials JSON missing required fields: "
                + ", ".join(missing)
            )
    else:
        raise GoogleAuthError(
            f"Unsupported credentials type: {sa.get('type')!r}. "
            "Expected 'service_account' or 'authorized_user'."
        )
    return sa


def _b64url(data: bytes) -> str:
    """RFC 7519 base64url (no padding)."""
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_jwt(sa: dict[str, Any], *, scope: str, aud: str) -> str:
    """Build a signed JWT for the given scope+audience.

    Used as the assertion grant for `https://oauth2.googleapis.com/token`.
    This is a tiny from-scratch RS256 signer — we don't want to pull in
    `cryptography` / `PyJWT` as a dep for 30 lines of crypto.
    """
    header = {"alg": "RS256", "typ": "JWT"}
    now = int(time.time())
    claims = {
        "iss": sa["client_email"],
        "scope": scope,
        "aud": aud,
        "iat": now,
        "exp": now + 3600,
    }
    header_b = _b64url(json.dumps(header, separators=(",", ":")).encode())
    claims_b = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = (header_b + "." + claims_b).encode()
    # Sign with the service account's RSA private key.
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as e:
        raise GoogleAuthError(
            "The `cryptography` package is required to sign JWTs for "
            "service-account auth. Install it with `pip install cryptography`. "
            f"({e})"
        )
    private_key = serialization.load_pem_private_key(
        sa["private_key"].encode(), password=None,
    )
    signature = private_key.sign(
        signing_input, padding.PKCS1v15(), hashes.SHA256(),
    )
    return signing_input.decode() + "." + _b64url(signature)


def _exchange_jwt_for_access_token(sa: dict[str, Any], scope: str) -> str:
    """Exchange a signed JWT for an OAuth2 access token via the token endpoint."""
    aud = "https://oauth2.googleapis.com/token"
    jwt_assertion = _make_jwt(sa, scope=scope, aud=aud)
    resp = requests.post(
        aud,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_assertion,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise GoogleAuthError(
            f"Failed to exchange JWT for access token: {resp.status_code} {resp.text}",
            status_code=resp.status_code,
        )
    body = resp.json()
    return body["access_token"]


def _exchange_refresh_token_for_access_token(
    client_id: str, client_secret: str, refresh_token: str, scope: str,
) -> str:
    """Exchange an OAuth user-credentials refresh_token for an access token.

    This is the path for end-user credentials (gcloud ADC's
    authorized_user type) — uses grant_type=refresh_token instead of
    the JWT-bearer grant used by service accounts.
    """
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": scope,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise GoogleAuthError(
            f"refresh_token exchange failed: HTTP {resp.status_code} "
            f"{resp.text[:300]}",
            status_code=resp.status_code,
        )
    body = resp.json()
    return body["access_token"]


# --- The client -----------------------------------------------------------

class GoogleClient:
    """Client for Google Analytics Admin, Tag Manager, and Search Console APIs.

    Construction:
      gc = GoogleClient.from_env()  # reads GOOGLE_SA_JSON or GOOGLE_SA_INLINE
      # OR
      gc = GoogleClient(service_account_json={...})
    """

    GA_ADMIN_BASE = "https://analyticsadmin.googleapis.com/$discovery/rest"
    GTM_BASE = "https://tagmanager.googleapis.com/api/v2"
    GSC_BASE = "https://searchconsole.googleapis.com/webmasters/v3"

    def __init__(
        self,
        *,
        service_account: dict[str, Any] | None = None,
        ga4_account_id: str | None = None,
        gtm_account_id: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff: float = 1.5,
    ) -> None:
        if not service_account:
            raise GoogleAuthError(
                "service_account is required. Use GoogleClient.from_env() or "
                "pass service_account={\"type\": \"service_account\", ...}."
            )
        self._sa = service_account
        # Credentials kind — "service_account" or "authorized_user".
        # Determines which OAuth grant we use to obtain access tokens.
        sa_type = service_account.get("type", "")
        if sa_type == "service_account":
            self._creds_kind = "service_account"
        elif sa_type == "authorized_user":
            self._creds_kind = "authorized_user"
        else:
            raise GoogleAuthError(
                f"Unsupported credentials type: {sa_type!r}. "
                "Expected 'service_account' or 'authorized_user'."
            )
        self._service_account_email = service_account.get("client_email", "")
        self._ga4_account_id = (
            ga4_account_id or os.environ.get("GA4_ACCOUNT_ID", "").strip()
        )
        self._gtm_account_id = (
            gtm_account_id or os.environ.get("GTM_ACCOUNT_ID", "").strip()
        )
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        # Cached access tokens per (scope, expiry).
        self._token_cache: dict[tuple[str, int], str] = {}

    # -- Token management -------------------------------------------------

    def _access_token(self, scope: str) -> str:
        """Return a cached access token for `scope`, refreshing if needed."""
        now = int(time.time())
        # Look for any cached token for this scope whose expiry > 5 min.
        for (cached_scope, expiry), token in list(self._token_cache.items()):
            if cached_scope == scope and expiry > now + 300:
                return token
        # Mint fresh — branch on credential type.
        if self._creds_kind == "service_account":
            token = _exchange_jwt_for_access_token(self._sa, scope=scope)
        elif self._creds_kind == "authorized_user":
            token = _exchange_refresh_token_for_access_token(
                client_id=self._sa["client_id"],
                client_secret=self._sa["client_secret"],
                refresh_token=self._sa["refresh_token"],
                scope=scope,
            )
        else:
            raise GoogleAuthError(
                f"Unknown credential kind: {self._creds_kind}"
            )
        self._token_cache[(scope, now)] = token
        # Garbage-collect expired entries (avoid unbounded growth).
        stale = [k for k in self._token_cache if k[1] <= now - 60]
        for k in stale:
            self._token_cache.pop(k, None)
        return token

    def _request(
        self,
        method: str,
        url: str,
        *,
        scope: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generic authenticated request with retries on 429/5xx."""
        last_exc: GoogleError | None = None
        for attempt in range(self._max_retries):
            try:
                token = self._access_token(scope)
                resp = requests.request(
                    method,
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=json_body,
                    params=params,
                    timeout=self._timeout,
                )
            except requests.RequestException as e:
                last_exc = GoogleError(f"network error: {e}")
                time.sleep(self._retry_backoff ** attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = GoogleQuotaError(
                    f"transient {resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                )
                time.sleep(self._retry_backoff ** attempt)
                continue
            if not resp.ok:
                body = _safe_json(resp)
                raise GoogleError(
                    f"{method} {url} -> {resp.status_code} {body}",
                    status_code=resp.status_code,
                )
            return _safe_json(resp) or {}
        raise last_exc or GoogleError("retry exhausted")

    # -- Helpers -----------------------------------------------------------

    @property
    def service_account_email(self) -> str:
        return self._service_account_email

    @property
    def ga4_account_id(self) -> str:
        if not self._ga4_account_id:
            raise GoogleError(
                "GA4 account ID is not configured. Set GA4_ACCOUNT_ID env var "
                "or pass ga4_account_id=..."
            )
        return self._ga4_account_id

    @property
    def gtm_account_id(self) -> str:
        if not self._gtm_account_id:
            raise GoogleError(
                "GTM account ID is not configured. Set GTM_ACCOUNT_ID env var "
                "or pass gtm_account_id=..."
            )
        return self._gtm_account_id

    @classmethod
    def from_env(cls) -> GoogleClient:
        """Construct from GOOGLE_SA_JSON / GOOGLE_SA_INLINE env vars.

        If those aren't set, falls back to auth_loader.get_secret('google_adc')
        which discovers the gcloud application_default_credentials.json file.
        """
        sa = _load_service_account()
        if sa is None:
            from . import auth_loader
            result = auth_loader.get_secret("google_adc")
            if result.found:
                # Treat the ADC as a service-account-like dict
                try:
                    sa = json.loads(result.value) if isinstance(result.value, str) else result.value
                except Exception as e:
                    raise GoogleAuthError(
                        f"google_adc from {result.source} is not valid JSON: {e}"
                    ) from e
        if sa is None:
            raise GoogleAuthError(
                "No Google credentials found. Set GOOGLE_SA_JSON env var to a "
                "service-account JSON path, or run `gcloud auth "
                "application-default login` to populate "
                "~/.config/gcloud/application_default_credentials.json"
            )
        return cls(service_account=sa)

    # -- GA4 Admin API -----------------------------------------------------

    GA4_SCOPE = "https://www.googleapis.com/auth/analytics.edit"
    GA4_READ_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

    def ga4_property_create(
        self,
        *,
        domain: str,
        site_name: str,
        timezone: str = "America/Los_Angeles",
    ) -> GA4Property:
        """Create a GA4 property + web data stream.

        Returns the property (with measurement_id from the new data stream).

        The GA4 Admin API requires the `analytics.edit` scope; we use
        `properties.create` and then `dataStreams.create` on the new
        property. The web data stream gives us the measurement ID (G-XXXXXX).
        """
        # 1. Create the property.
        prop_url = (
            "https://analyticsadmin.googleapis.com/v1beta/properties"
        )
        prop_body = {
            "parent": f"accounts/{self.ga4_account_id}",
            "displayName": site_name,
            "industryCategory": "TECHNOLOGY",
            "timeZone": timezone,
            "currencyCode": "USD",
            "deleted": False,
        }
        prop_resp = self._request("POST", prop_url, scope=self.GA4_SCOPE, json_body=prop_body)
        prop_name = prop_resp.get("name", "")
        if not prop_name.startswith("properties/"):
            raise GoogleError(f"unexpected property response: {prop_resp}")
        property_id = prop_name.split("/", 1)[1]

        # 2. Create the web data stream.
        stream_url = f"https://analyticsadmin.googleapis.com/v1beta/{prop_name}/dataStreams"
        stream_body = {
            "type": "WEB_DATA_STREAM",
            "displayName": f"{site_name} — Web",
            "webStreamData": {
                "defaultUri": f"https://{domain}/",
            },
        }
        stream_resp = self._request(
            "POST", stream_url, scope=self.GA4_SCOPE, json_body=stream_body
        )
        stream_name = stream_resp.get("name", "")
        # The measurement ID is in the webStreamData sibling; pull it.
        measurement_id = (
            stream_resp.get("webStreamData", {}).get("measurementId", "") or ""
        )

        return GA4Property(
            name=prop_name,
            property_id=property_id,
            measurement_id=measurement_id,
            data_stream_name=stream_name,
        )

    # -- GTM API -----------------------------------------------------------

    GTM_SCOPE = "https://www.googleapis.com/auth/tagmanager.edit.containers"

    def gtm_container_create(
        self,
        *,
        site_name: str,
        domain: str,
    ) -> GTMContainer:
        """Create a GTM container for the given site."""
        url = f"https://tagmanager.googleapis.com/api/v2/accounts/{self.gtm_account_id}/containers"
        body = {
            "name": site_name,
            "domains": [domain],
            "usageContext": ["web"],
        }
        resp = self._request("POST", url, scope=self.GTM_SCOPE, json_body=body)
        public_id = resp.get("publicId", "")
        cid = resp.get("containerId", "")
        if not public_id.startswith("GTM-"):
            raise GoogleError(f"unexpected GTM response: {resp}")
        return GTMContainer(
            public_id=public_id,
            account_id=self.gtm_account_id,
            container_id=str(cid),
            container_name=site_name,
        )

    # -- GSC API (placeholder) --------------------------------------------

    GSC_SCOPE = "https://www.googleapis.com/auth/webmasters"

    def gsc_site_add(self, *, site_url: str) -> GSCSite:
        """Add a site to Google Search Console.

        NOTE: most sites are verified via DNS TXT, not this API. This
        is here for completeness; the provisioner currently does GSC
        verification via Cloudflare-managed TXT records, not GSC API.
        """
        url = "https://searchconsole.googleapis.com/webmasters/v3/sites/" + quote(site_url)
        resp = self._request("PUT", url, scope=self.GSC_SCOPE)
        return GSCSite(
            site_url=site_url,
            permission_level=resp.get("permissionLevel", ""),
        )


# --- helpers --------------------------------------------------------------

def _safe_json(resp: requests.Response) -> dict[str, Any]:
    try:
        body = resp.json()
        if not isinstance(body, dict):
            return {"raw": body}
        return body
    except Exception:
        return {"raw": resp.text}


def quote(s: str) -> str:
    """URL-quote a path component."""
    from urllib.parse import quote as _q
    return _q(s, safe="")
