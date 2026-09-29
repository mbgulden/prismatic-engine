"""T1 single arming ceremony.

One explicit ceremony arms T1: a signed ``t1_armed`` document is appended
to the trust ledger, and the daemon consults ONLY that record. No record —
or a missing/invalid/expired record — leaves T1 inert. The emergency stop
is the ``disarm`` ceremony, which appends ``t1_disarmed`` (latest of the two
wins).

Signing reuses the merge-receipt Ed25519 key (``_load_signing_key`` from
``prismatic.verification.merge_receipt``): the same canonicalization
contract, the same key loader. Arming REFUSES when no signing key is
available — no unsigned arming records, ever.

CLI::

    python -m prismatic.review_factory.arming arm --approver NAME --rationale TEXT [--expires-in-days N]
    python -m prismatic.review_factory.arming disarm --approver NAME --rationale TEXT
    python -m prismatic.review_factory.arming status

Import graph (no cycles): this module imports ``trust``,
``verification.merge_receipt`` (key loader) and ``verification.attestation``
(canonicalization). It never imports ``merge_stage``; ``merge_stage`` and
``verification_daemon`` import this module.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger(__name__)

MARKER = "t1-arming-record"
SCHEMA_VERSION = 1
ARMING_TIER = 1
ARMING_MODE = "live"
SIGNATURE_ISSUER = "t1-arming-ceremony"

# Env override for the cached public key (tests point this at a temp dir).
PUBKEY_CACHE_ENV = "PRISMATIC_T1_ARMING_PUBKEY_FILE"


def _pubkey_cache_path() -> Path:
    override = os.environ.get(PUBKEY_CACHE_ENV)
    if override:
        return Path(override).expanduser()
    return Path("~/.prismatic/keys/merge-receipt-ed25519.pub").expanduser()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── document construction ─────────────────────────────────────────────


def build_arming_document(
    *,
    approver: str,
    rationale: str,
    expires_at: Optional[str] = None,
    armed_at: Optional[str] = None,
) -> dict:
    """Build the unsigned arming document (schema v1)."""
    if not approver or not approver.strip():
        raise ValueError("approver is required")
    if not rationale or not rationale.strip():
        raise ValueError("rationale is required")
    return {
        "marker": MARKER,
        "schema_version": SCHEMA_VERSION,
        "tier": ARMING_TIER,
        "mode": ARMING_MODE,
        "armed_at": armed_at or _utcnow_iso(),
        "expires_at": expires_at,
        "approver": approver.strip(),
        "rationale": rationale.strip(),
    }


def _load_signing_key():
    """Lazy import of the shared merge-receipt key loader (no import cycle)."""
    from prismatic.verification.merge_receipt import _load_signing_key

    return _load_signing_key()


def _canonical_signing_bytes(doc: dict) -> bytes:
    """Canonical bytes of the document per the merge-receipt contract.

    ``canonicalize_receipt`` excludes only ``signature_or_attestation.value``,
    so the envelope metadata (type/algorithm/key_id/issuer) is covered by
    the signature. Sign with the envelope present and ``value`` empty, then
    fill in the value.
    """
    from prismatic.verification.attestation import canonicalize_receipt

    return canonicalize_receipt(doc)


def sign_arming_document(
    doc: dict,
    private_key: Optional[Ed25519PrivateKey] = None,
    key_id: Optional[str] = None,
) -> dict:
    """Ed25519-sign the arming document in place; returns the document.

    Raises RuntimeError when no signing key is available — arming never
    produces an unsigned record.
    """
    loaded_key_id = key_id
    if private_key is None:
        private_key, loaded_key_id = _load_signing_key()
    if private_key is None:
        raise RuntimeError(
            "no Ed25519 signing key available: refusing to create an "
            "unsigned T1 arming record"
        )
    doc["signature_or_attestation"] = {
        "type": "attestation",
        "algorithm": "ed25519",
        "key_id": loaded_key_id or key_id or "unknown",
        "issuer": SIGNATURE_ISSUER,
        "value": "",
    }
    signature = private_key.sign(_canonical_signing_bytes(doc))
    doc["signature_or_attestation"]["value"] = base64.b64encode(signature).decode(
        "ascii"
    )
    return doc


def cache_public_key(private_key: Ed25519PrivateKey) -> Path:
    """Write the derived Ed25519 public key (PEM) to the cache path.

    The public key is not a secret. Rewritten on every arming so the cache
    always matches the signing key.
    """
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = _pubkey_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(public_pem)
    return path


# ── verification ──────────────────────────────────────────────────────


def _validate_schema(doc: Any) -> tuple[bool, str]:
    """Validate the signed schema. Fail-closed: (False, reason) on any gap."""
    if not isinstance(doc, dict):
        return False, "t1_arming_schema_invalid"
    checks = [
        doc.get("marker") == MARKER,
        doc.get("schema_version") == SCHEMA_VERSION,
        doc.get("tier") == ARMING_TIER,
        doc.get("mode") == ARMING_MODE,
        isinstance(doc.get("approver"), str) and bool(doc["approver"].strip()),
        isinstance(doc.get("rationale"), str) and bool(doc["rationale"].strip()),
        _parse_iso(doc.get("armed_at")) is not None,
    ]
    if not all(checks):
        return False, "t1_arming_schema_invalid"
    sig = doc.get("signature_or_attestation")
    if not isinstance(sig, dict):
        return False, "t1_arming_signature_missing"
    sig_checks = [
        sig.get("type") == "attestation",
        sig.get("algorithm") == "ed25519",
        isinstance(sig.get("key_id"), str) and bool(sig["key_id"].strip()),
        sig.get("issuer") == SIGNATURE_ISSUER,
        isinstance(sig.get("value"), str) and bool(sig["value"].strip()),
    ]
    if not all(sig_checks):
        return False, "t1_arming_signature_missing"
    return True, ""


def _load_cached_public_key():
    """Load the cached Ed25519 public key. Returns None when unavailable."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    path = _pubkey_cache_path()
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        key = serialization.load_pem_public_key(raw)
    except ValueError:
        return None
    return key if isinstance(key, Ed25519PublicKey) else None


def verify_arming_document(doc: dict) -> tuple[bool, str]:
    """Verify schema + Ed25519 signature. Non-raising, fail-closed."""
    ok, reason = _validate_schema(doc)
    if not ok:
        return False, reason
    public_key = _load_cached_public_key()
    if public_key is None:
        return False, "t1_arming_key_unavailable"
    sig = doc["signature_or_attestation"]
    try:
        raw_sig = base64.b64decode(sig["value"], validate=True)
    except (binascii.Error, ValueError):
        return False, "t1_arming_signature_invalid"
    try:
        public_key.verify(raw_sig, _canonical_signing_bytes(doc))
    except InvalidSignature:
        return False, "t1_arming_signature_invalid"
    return True, ""


# ── the single consult ────────────────────────────────────────────────


def _latest_arming_events(events: list[dict]) -> tuple[Optional[dict], Optional[dict]]:
    """Return (latest t1_armed doc, latest t1_disarmed event).

    ``events`` must be in ledger order (oldest first); the LAST matching
    event of either type wins.
    """
    armed_doc: Optional[dict] = None
    disarmed: Optional[dict] = None
    armed_idx = disarmed_idx = -1
    for idx, ev in enumerate(events):
        etype = ev.get("event_type")
        if etype == "t1_armed":
            armed_doc = ev.get("judgment") or {}
            armed_idx = idx
        elif etype == "t1_disarmed":
            disarmed = ev
            disarmed_idx = idx
    if armed_doc is not None and armed_idx > disarmed_idx:
        return armed_doc, None
    if disarmed is not None and disarmed_idx > armed_idx:
        return None, disarmed
    return None, None


def t1_arming_status(ledger: Any = None) -> dict:
    """The single T1 arming consult.

    Returns ``{"armed": bool, "reason": str, "record": dict | None}``.
    Fail-closed: any ledger/verification failure reports disarmed.
    """
    try:
        if ledger is None:
            from prismatic.review_factory import trust

            ledger = trust.TrustLedger()
        events = ledger.events()
        armed_doc, disarmed = _latest_arming_events(events)
        if disarmed is not None:
            return {"armed": False, "reason": "t1_disarmed", "record": None}
        if armed_doc is None:
            return {"armed": False, "reason": "t1_never_armed", "record": None}
        ok, reason = verify_arming_document(armed_doc)
        if not ok:
            return {"armed": False, "reason": reason, "record": None}
        expires_at = armed_doc.get("expires_at")
        if expires_at is not None:
            # A malformed non-empty expires_at fails closed: without a
            # trustworthy expiry the record cannot arm.
            expiry = _parse_iso(expires_at)
            if expiry is None or expiry <= datetime.now(timezone.utc):
                return {"armed": False, "reason": "t1_arming_expired", "record": None}
        return {"armed": True, "reason": "t1_armed", "record": armed_doc}
    except Exception as exc:
        logger.warning("t1 arming check failed: %s", exc)
        return {"armed": False, "reason": "t1_arming_check_failed", "record": None}


# ── CLI ceremony ──────────────────────────────────────────────────────


def cmd_arm(args: argparse.Namespace) -> int:
    from prismatic.review_factory import trust

    private_key, _key_id = _load_signing_key()
    if private_key is None:
        print("ERROR: no signing key available; refusing to arm", flush=True)
        return 2
    expires_at = None
    if args.expires_in_days is not None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=args.expires_in_days)
        ).isoformat()
    doc = build_arming_document(
        approver=args.approver, rationale=args.rationale, expires_at=expires_at
    )
    sign_arming_document(doc, private_key=private_key, key_id=_key_id)
    cache_public_key(private_key)
    ledger = trust.TrustLedger()
    ledger.record_t1_armed(document=doc)
    print(f"T1 armed by {args.approver}")
    return 0


def cmd_disarm(args: argparse.Namespace) -> int:
    from prismatic.review_factory import trust

    ledger = trust.TrustLedger()
    ledger.record_t1_disarmed(approver=args.approver, rationale=args.rationale)
    print(f"T1 disarmed by {args.approver}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    status = t1_arming_status()
    print(f"armed={status['armed']} reason={status['reason']}")
    record = status.get("record")
    if record:
        print(
            f"tier={record.get('tier')} approver={record.get('approver')} "
            f"armed_at={record.get('armed_at')} expires_at={record.get('expires_at')}"
        )
    return 0 if status["armed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic.review_factory.arming",
        description="T1 single arming ceremony: one explicit command arms T1; "
        "the signed trust-ledger record is the only authority.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_arm = sub.add_parser("arm", help="sign and record the T1 arming document")
    p_arm.add_argument("--approver", required=True)
    p_arm.add_argument("--rationale", required=True)
    p_arm.add_argument("--expires-in-days", type=int, default=None)
    p_arm.set_defaults(func=cmd_arm)

    p_dis = sub.add_parser("disarm", help="record the T1 disarm (emergency stop)")
    p_dis.add_argument("--approver", required=True)
    p_dis.add_argument("--rationale", required=True)
    p_dis.set_defaults(func=cmd_disarm)

    p_status = sub.add_parser("status", help="print the current T1 arming state")
    p_status.set_defaults(func=cmd_status)
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
