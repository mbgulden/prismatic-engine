"""domain_verifier — DNS TXT challenge for domain ownership.

Before we touch anything for a domain, we MUST verify the person
who initiated the provisioning flow actually owns (or controls) the
domain. Otherwise anyone could type "google.com" and we'd happily
start creating DNS records.

The challenge protocol:

  1. The provisioner generates a random token (e.g.
     "pwp-verify-7f3a9c2b8e").
  2. The owner creates a DNS TXT record at `_pwp-verify.example.com`
     with that token as the value.
  3. The provisioner queries the public DNS (via Cloudflare's DNS
     over HTTPS resolver, or via the Cloudflare zone's own DNS
     records if the zone is already on Cloudflare) and checks the
     TXT value matches.

Why DNS TXT and not HTML file upload or email?

  - HTML file upload requires write access to the site's source repo.
    We're explicitly NOT building that in Phase 1.
  - Email (admin@, webmaster@) is unreliable (most domains don't
    forward these anymore).
  - DNS TXT is the same mechanism Google Search Console uses for
    `sc-domain:` verification — well-known, widely supported.

For Phase 1 we use Cloudflare's DNS-over-HTTPS resolver
(`https://cloudflare-dns.com/dns-query`) which is public, doesn't
require an API token, and is fast. If the zone is already on
Cloudflare, we use the zone's own DNS records (authoritative) as a
faster alternative.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Optional

import requests


VERIFY_PREFIX = "_pwp-verify"
CF_DOH_RESOLVER = "https://cloudflare-dns.com/dns-query"


def generate_challenge_token() -> str:
    """Generate a random token for the DNS TXT challenge.

    Format: `pwp-verify-<16 hex chars>`. The hex form avoids DNS
    quoting concerns and keeps the record readable.
    """
    return f"pwp-verify-{secrets.token_hex(8)}"


def expected_record_name(domain: str) -> str:
    """Return the FQDN of the TXT record the owner must create.

    For domain `example.com` this returns `_pwp-verify.example.com`.
    """
    return f"{VERIFY_PREFIX}.{domain}"


@dataclass
class VerifyResult:
    """Result of a domain-ownership verification attempt."""
    verified: bool
    record_name: str
    expected_value: str
    observed_values: list[str]
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "verified": self.verified,
            "record_name": self.record_name,
            "expected_value": self.expected_value,
            "observed_values": list(self.observed_values),
            "error": self.error,
        }


def _query_txt_via_doh(fqdn: str) -> list[str]:
    """Query TXT records for `fqdn` via Cloudflare DNS-over-HTTPS.

    Returns the list of string values found (decoded from the DNS
    response). Empty list if the record doesn't exist or the query
    failed.
    """
    try:
        resp = requests.get(
            CF_DOH_RESOLVER,
            params={"name": fqdn, "type": "TXT"},
            headers={"Accept": "application/dns-json"},
            timeout=10,
        )
    except requests.RequestException as exc:
        return []
    if resp.status_code != 200:
        return []
    try:
        payload = resp.json()
    except ValueError:
        return []
    out: list[str] = []
    for answer in payload.get("Answer", []) or []:
        if answer.get("type") == 16:  # TXT
            txt = answer.get("data", "")
            # Cloudflare DoH returns TXT data wrapped in quotes.
            if txt.startswith('"') and txt.endswith('"'):
                txt = txt[1:-1]
            out.append(txt)
    return out


def verify(
    domain: str,
    expected_value: str,
    *,
    observed_values: Optional[list[str]] = None,
) -> VerifyResult:
    """Verify the DNS TXT challenge for `domain`.

    Args:
      domain: the bare domain (e.g. "example.com").
      expected_value: the token the owner was instructed to set.
      observed_values: optional pre-collected TXT values (e.g. from
        a Cloudflare zone lookup). If provided, the DoH query is
        skipped. Used in tests.

    Returns:
      VerifyResult with verified=True iff any observed TXT record at
      `_pwp-verify.<domain>` matches `expected_value` exactly.
    """
    record_name = expected_record_name(domain)
    if observed_values is None:
        observed_values = _query_txt_via_doh(record_name)
    matched = expected_value in observed_values
    return VerifyResult(
        verified=matched,
        record_name=record_name,
        expected_value=expected_value,
        observed_values=observed_values,
        error=None if matched else f"no TXT record at {record_name} matches expected value",
    )
