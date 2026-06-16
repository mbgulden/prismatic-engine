"""
prismatic/security/egress_scanner.py — Egress Secret & PII Scanner

Regex/entropy-based scanner that intercepts outbound calls and blocks
any text containing secrets, credentials, or PII before they leave the
Prismatic Engine.

Architecture:
    - EgressScanner: main class with regex patterns + entropy checks
    - ScanResult: structured result from scan_text()
    - SecurityVulnerabilityAlert: event emitted on secret detection
    - scan_egress: decorator for egress client methods
    - Fail-secure: scanner errors block ALL egress traffic

Wire points (to be connected by implementation issues):
    - github_client.post_comment()
    - linear_client.create_comment()
    - slack_client.send_message()
    - git_client.push()
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ClassVar, Pattern

logger = logging.getLogger("prismatic.security.egress_scanner")

# ── Constants ────────────────────────────────────────────────

# Minimum Shannon entropy threshold for secret detection
DEFAULT_ENTROPY_THRESHOLD = 4.5

# Quarantine directory for blocked payloads
DEFAULT_QUARANTINE_DIR = Path(
    os.environ.get("PRISMATIC_HOME", os.path.expanduser("~/.prismatic"))
) / "db" / "quarantine"


# ── Regex Patterns ───────────────────────────────────────────

# GitHub tokens: classic (ghp_), fine-grained (github_pat_), OAuth (gho_),
# refresh (ghr_), and installation tokens (ghs_)
GITHUB_TOKEN_PATTERN: Pattern[str] = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{36,}|github_pat_[A-Za-z0-9_]{22,})",
    re.IGNORECASE,
)

# AWS access keys: AKIA (long-term) and ASIA (temporary session)
AWS_KEY_PATTERN: Pattern[str] = re.compile(
    r"(?:AKIA|ASIA)[A-Z0-9]{16}",
)

# AWS Secret Access Key (base64-like, 40 chars)
AWS_SECRET_PATTERN: Pattern[str] = re.compile(
    r"(?:aws.{0,20}?secret.{0,20}?|[^A-Za-z0-9+/])([A-Za-z0-9+/]{40})(?:[^A-Za-z0-9+/]|$)",
    re.IGNORECASE,
)

# Social Security Numbers (US) — XXX-XX-XXXX
SSN_PATTERN: Pattern[str] = re.compile(
    r"\b(?!000|666|9\d{2})([0-8]\d{2}|7[0-6]\d|77[0-2])[-]?(?!00)\d{2}[-]?(?!0000)\d{4}\b",
)

# Credit card numbers (Visa, MasterCard, Amex, Discover)
CC_PATTERN: Pattern[str] = re.compile(
    r"\b(?:4[0-9]{12}(?:[0-9]{3})?|"  # Visa
    r"5[1-5][0-9]{14}|"  # MasterCard
    r"3[47][0-9]{13}|"  # Amex
    r"6(?:011|5[0-9]{2})[0-9]{12}|"  # Discover
    r"(?:2131|1800|35\d{3})\d{11})"  # JCB
    r"(?:\s|-)?(?:\d{3,4})?\b",
)

# Database connection URLs (postgres, mysql, mongo, redis, etc.)
DATABASE_URL_PATTERN: Pattern[str] = re.compile(
    r"(?:jdbc:)?(?:postgres(?:ql)?|mysql|mongo(?:db)?|redis|sqlite|oracle|mssql|mariadb|cassandra|couchbase)://"
    r"[^\s\"'<>]+",
    re.IGNORECASE,
)

# Generic secret patterns — assignment of sensitive-looking variables
GENERIC_SECRET_PATTERN: Pattern[str] = re.compile(
    r"(?i)(?:"
    r"password|passwd|secret|token|api[_-]?key|private[_-]?key|auth[_-]?token"
    r"|access[_-]?key|credential"
    r")\s*[:=]\s*[\"']?([^\s\"']{8,})[\"']?",
)

# Bearer token patterns — Authorization headers leaking
BEARER_TOKEN_PATTERN: Pattern[str] = re.compile(
    r"(?i)authorization\s*[:=]\s*[\"']?\s*(?:Bearer\s+)?([A-Za-z0-9_\-\.]{20,})[\"']?",
)

# SSH private key headers
SSH_PRIVATE_KEY_PATTERN: Pattern[str] = re.compile(
    r"-----BEGIN (?:RSA|DSA|EC|OPENSSH|ED25519) PRIVATE KEY-----",
)

# PGP private key headers
PGP_PRIVATE_KEY_PATTERN: Pattern[str] = re.compile(
    r"-----BEGIN PGP PRIVATE KEY BLOCK-----",
)

# Stripe keys (sk_live_ / rk_live_)
_STRIPE_PREFIX = "sk_" + "live_"
_STRIPE_RESTRICTED = "rk_" + "live_"
STRIPE_SECRET_PATTERN: Pattern[str] = re.compile(
    r"(?:" + _STRIPE_PREFIX + "|" + _STRIPE_RESTRICTED + r")[A-Za-z0-9_]{24,}",
)

# OpenAI / Anthropic / provider API keys
AI_API_KEY_PATTERN: Pattern[str] = re.compile(
    r"(?:sk-[A-Za-z0-9]{32,}|sk-ant-[A-Za-z0-9_\-]{32,}|AIza[A-Za-z0-9_\-]{35})",
)

# All patterns in scan order (more specific first for efficiency)
ALL_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("github_token", GITHUB_TOKEN_PATTERN),
    ("aws_key", AWS_KEY_PATTERN),
    ("aws_secret", AWS_SECRET_PATTERN),
    ("stripe_secret", STRIPE_SECRET_PATTERN),
    ("ai_api_key", AI_API_KEY_PATTERN),
    ("database_url", DATABASE_URL_PATTERN),
    ("ssh_private_key", SSH_PRIVATE_KEY_PATTERN),
    ("pgp_private_key", PGP_PRIVATE_KEY_PATTERN),
    ("bearer_token", BEARER_TOKEN_PATTERN),
    ("generic_secret", GENERIC_SECRET_PATTERN),
    ("ssn", SSN_PATTERN),
    ("credit_card", CC_PATTERN),
]


# ── Shannon Entropy ──────────────────────────────────────────

def shannon_entropy(text: str) -> float:
    """Calculate Shannon entropy of a string.

    Higher entropy suggests randomness — encoded tokens, keys, or
    encrypted blobs. A threshold of 4.5 catches base64-encoded
    secrets while passing natural language.
    """
    if not text:
        return 0.0

    counts = Counter(text)
    length = len(text)

    entropy = 0.0
    for count in counts.values():
        probability = count / length
        entropy -= probability * math.log2(probability)

    return entropy


def _find_high_entropy_segments(
    text: str,
    threshold: float = DEFAULT_ENTROPY_THRESHOLD,
    window_size: int = 32,
) -> list[dict[str, Any]]:
    """Scan text with a sliding window for high-entropy segments.

    Returns list of dicts with 'snippet', 'entropy', and 'position'.
    """
    results: list[dict[str, Any]] = []
    if len(text) < window_size:
        return results

    for i in range(len(text) - window_size + 1):
        window = text[i : i + window_size]
        # Skip windows that are mostly whitespace or repetitive
        unique_ratio = len(set(window)) / len(window)
        if unique_ratio < 0.3:
            continue

        entropy = shannon_entropy(window)
        if entropy >= threshold:
            results.append({
                "snippet": window,
                "entropy": round(entropy, 2),
                "position": i,
            })

    # Deduplicate overlapping matches — keep highest entropy
    if results:
        results = _deduplicate_entropy_matches(results, window_size // 2)

    return results


def _deduplicate_entropy_matches(
    matches: list[dict[str, Any]], min_distance: int
) -> list[dict[str, Any]]:
    """Merge overlapping entropy matches, keeping the highest entropy."""
    if not matches:
        return matches

    sorted_matches = sorted(matches, key=lambda m: m["position"])
    merged: list[dict[str, Any]] = []
    current = sorted_matches[0]

    for match in sorted_matches[1:]:
        if match["position"] - current["position"] < min_distance:
            if match["entropy"] > current["entropy"]:
                current = match
        else:
            merged.append(current)
            current = match
    merged.append(current)

    return merged


# ── ScanResult & SecurityVulnerabilityAlert ──────────────────


@dataclass
class ScanResult:
    """Result of an egress content scan.

    Attributes:
        blocked: whether the content should be blocked from egress
        findings: list of matched patterns with details
        entropy_hits: high-entropy segments detected
    """

    blocked: bool = False
    findings: list[dict[str, Any]] = field(default_factory=list)
    entropy_hits: list[dict[str, Any]] = field(default_factory=list)
    scanned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_finding(
        self, rule: str, snippet: str, position: int | None = None
    ) -> None:
        self.findings.append({
            "rule": rule,
            "snippet": snippet[:100],
            "position": position,
        })
        self.blocked = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "findings": self.findings,
            "entropy_hits": self.entropy_hits,
            "scanned_at": self.scanned_at,
        }


class SecurityVulnerabilityAlert:
    """Alert emitted when the egress scanner blocks content.

    Designed to be published via the gateway EventBus (SwarmEvent
    with type='security.vulnerability'). The alertmanager picks
    these up and routes to Telegram/human review.
    """

    def __init__(
        self,
        scan_result: ScanResult,
        agent_id: str = "",
        method: str = "",
        quarantined_path: str = "",
    ) -> None:
        self.scan_result = scan_result
        self.agent_id = agent_id
        self.method = method
        self.quarantined_path = quarantined_path
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_event_payload(self) -> dict[str, Any]:
        """Return a dict suitable for SwarmEvent.payload."""
        return {
            "alert_type": "security.vulnerability",
            "blocked": self.scan_result.blocked,
            "findings": self.scan_result.findings,
            "entropy_hits": self.scan_result.entropy_hits,
            "agent_id": self.agent_id,
            "method": self.method,
            "quarantined_path": self.quarantined_path,
            "alerted_at": self.timestamp,
        }


class EgressSecurityError(Exception):
    """Raised when the egress scanner itself fails — fail-secure trigger."""

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


class EgressBlockedError(Exception):
    """Raised when egress content matches security patterns."""

    def __init__(self, result: ScanResult):
        self.result = result
        finding_summary = ", ".join(
            f["rule"] for f in result.findings[:5]
        )
        super().__init__(
            f"Egress blocked: {len(result.findings)} secret pattern(s) "
            f"detected ({finding_summary})"
        )


# ── EgressScanner ────────────────────────────────────────────


class EgressScanner:
    """Regex + entropy scanner for egress content.

    Scans text before it leaves the engine boundary. On match:
        1. Blocks the operation
        2. Quarantines the content to disk
        3. Raises EgressBlockedError
        4. Emits SecurityVulnerabilityAlert

    Fail-secure behavior:
        If the scanner itself raises an unexpected error during
        scan_text(), the quarantine_lock is set to True, blocking
        ALL subsequent egress until human review (via admin CLI:
        prismatic security quarantine release).
    """

    # Class-level fail-secure lock. Once tripped, ALL egress is blocked
    # until reset by an administrator.
    quarantine_lock: ClassVar[bool] = False
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        entropy_threshold: float = DEFAULT_ENTROPY_THRESHOLD,
        quarantine_dir: Path | None = None,
        block_on_match: bool = True,
    ) -> None:
        self.entropy_threshold = entropy_threshold
        self.quarantine_dir = quarantine_dir or DEFAULT_QUARANTINE_DIR
        self.block_on_match = block_on_match
        self._scan_count: int = 0
        self._block_count: int = 0

    # ── Public API ───────────────────────────────────────

    def scan_text(self, text: str, agent_id: str = "", method: str = "") -> ScanResult:
        """Scan text for secrets and PII.

        Args:
            text: content to scan before egress
            agent_id: agent that originated the content
            method: egress method being called (for alert context)

        Returns:
            ScanResult with findings and blocked flag

        Raises:
            EgressBlockedError: when block_on_match is True and secrets found
            EgressSecurityError: when scanner fails (fail-secure)
        """
        # Check fail-secure lock first
        if EgressScanner.quarantine_lock:
            raise EgressSecurityError(
                "Egress quarantine lock is active — all outbound traffic "
                "blocked pending human review. Use: prismatic security "
                "quarantine release"
            )

        try:
            result = self._do_scan(text, agent_id, method)

            if result.blocked and self.block_on_match:
                quarantine_path = self._quarantine(text, result, agent_id, method)
                self._emit_alert(result, agent_id, method, quarantine_path)
                raise EgressBlockedError(result)

            return result

        except EgressBlockedError:
            raise
        except Exception as exc:
            # Fail-secure: any scanner error sets the quarantine lock
            self._set_quarantine_lock()
            logger.critical(
                "EgressScanner failed — quarantine lock engaged. "
                "ALL outbound traffic BLOCKED. Error: %s",
                exc,
                exc_info=True,
            )
            raise EgressSecurityError(
                f"EgressScanner internal failure — quarantine lock engaged: {exc}",
                original_error=exc,
            ) from exc

    def scan_hook(
        self,
        agent_id: str = "",
    ) -> Callable:
        """Return a decorator that intercepts egress method calls.

        Usage:
            scanner = EgressScanner()
            @scanner.scan_hook(agent_id="ned")
            def post_comment(text: str) -> bool: ...

        The decorator scans all string arguments for secrets before
        the wrapped function executes.
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                # Collect all string arguments for scanning
                text_parts: list[str] = []
                for arg in args:
                    if isinstance(arg, str):
                        text_parts.append(arg)
                for val in kwargs.values():
                    if isinstance(val, str):
                        text_parts.append(val)

                if text_parts:
                    combined = " ".join(text_parts)
                    method_name = getattr(func, "__qualname__", func.__name__)
                    self.scan_text(
                        combined,
                        agent_id=agent_id,
                        method=method_name,
                    )

                return func(*args, **kwargs)
            return wrapper
        return decorator

    @property
    def stats(self) -> dict[str, int]:
        return {
            "scan_count": self._scan_count,
            "block_count": self._block_count,
            "quarantine_locked": EgressScanner.quarantine_lock,
        }

    # ── Internal ─────────────────────────────────────────

    def _do_scan(
        self, text: str, agent_id: str, method: str
    ) -> ScanResult:
        """Run regex patterns and entropy checks on the text."""
        self._scan_count += 1
        result = ScanResult()

        # Phase 1: Regex pattern matching
        for rule_name, pattern in ALL_PATTERNS:
            for match in pattern.finditer(text):
                snippet = match.group(0)[:100]
                result.add_finding(rule_name, snippet, match.start())

        # Phase 2: Entropy scan for base64/encoded secrets
        entropy_hits = _find_high_entropy_segments(
            text, self.entropy_threshold
        )
        if entropy_hits:
            result.entropy_hits = entropy_hits
            result.blocked = True
            # If entropy found patterns the regex missed, flag it
            for hit in entropy_hits[:3]:
                result.findings.append({
                    "rule": "high_entropy",
                    "snippet": hit["snippet"],
                    "entropy": hit["entropy"],
                    "position": hit["position"],
                })

        if result.blocked:
            self._block_count += 1

        return result

    def _quarantine(
        self,
        text: str,
        result: ScanResult,
        agent_id: str,
        method: str,
    ) -> str:
        """Write blocked content to quarantine for human review."""
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"quarantine_{timestamp}.json"
        path = self.quarantine_dir / filename

        quarantine_record = {
            "agent_id": agent_id,
            "method": method,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "findings": result.findings,
            "entropy_hits": result.entropy_hits,
            "content_truncated": text[:5000],
        }

        path.write_text(json.dumps(quarantine_record, indent=2, default=str))

        logger.warning(
            "Content quarantined: %s (agent=%s, method=%s, findings=%d)",
            filename,
            agent_id,
            method,
            len(result.findings),
        )
        return str(path)

    def _emit_alert(
        self,
        result: ScanResult,
        agent_id: str,
        method: str,
        quarantine_path: str,
    ) -> None:
        """Emit a SecurityVulnerabilityAlert.

        The alert is published as a SwarmEvent via the gateway
        event bus. If the gateway is not available, logs the alert
        locally and writes to the alertmanager's event log.
        """
        alert = SecurityVulnerabilityAlert(
            scan_result=result,
            agent_id=agent_id,
            method=method,
            quarantined_path=quarantine_path,
        )

        # Try to publish via gateway event bus
        try:
            from prismatic.gateway.event_bus import get_event_bus

            event_bus = get_event_bus()
            # Fire-and-forget — we cannot await in sync code,
            # so schedule the publish on the event loop if available
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    event_bus.publish(
                        "security.vulnerability",
                        f"egress_scanner:{agent_id}",
                        alert.to_event_payload(),
                    )
                )
            except RuntimeError:
                # No running loop — run synchronously in a new one
                try:
                    asyncio.run(
                        event_bus.publish(
                            "security.vulnerability",
                            f"egress_scanner:{agent_id}",
                            alert.to_event_payload(),
                        )
                    )
                except Exception:
                    pass
        except ImportError:
            logger.debug(
                "Gateway event bus not available — alert logged locally"
            )
        except Exception:
            logger.exception("Failed to emit alert via event bus")

        # Always log locally as fallback
        alert_log = self.quarantine_dir.parent / "alerts.log"
        try:
            with open(alert_log, "a") as f:
                f.write(json.dumps(alert.to_event_payload(), default=str) + "\n")
        except OSError:
            pass

    @classmethod
    def _set_quarantine_lock(cls) -> None:
        """Engage the fail-secure quarantine lock."""
        with cls._lock:
            cls.quarantine_lock = True
        logger.critical(
            "FAIL-SECURE: Quarantine lock engaged. ALL egress BLOCKED."
        )

    @classmethod
    def release_quarantine_lock(cls) -> None:
        """Release the fail-secure quarantine lock (admin action)."""
        with cls._lock:
            was_locked = cls.quarantine_lock
            cls.quarantine_lock = False
        if was_locked:
            logger.warning("Quarantine lock released by administrator")


# ── Singleton & Decorator ────────────────────────────────────

_scanner: EgressScanner | None = None
_scanner_lock = threading.Lock()


def get_scanner(
    entropy_threshold: float = DEFAULT_ENTROPY_THRESHOLD,
) -> EgressScanner:
    """Return the module-level EgressScanner singleton."""
    global _scanner
    with _scanner_lock:
        if _scanner is None:
            _scanner = EgressScanner(entropy_threshold=entropy_threshold)
        return _scanner


def scan_egress(agent_id: str = ""):
    """Decorator to wrap egress client methods with secret scanning.

    Usage:
        @scan_egress(agent_id="fred")
        def linear_create_comment(issue_id: str, body: str) -> bool:
            ...
    """
    return get_scanner().scan_hook(agent_id=agent_id)


# ── Quarantine Admin Helpers ─────────────────────────────────

def list_quarantine(
    quarantine_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """List all quarantined payloads."""
    qdir = quarantine_dir or DEFAULT_QUARANTINE_DIR
    if not qdir.exists():
        return []

    entries: list[dict[str, Any]] = []
    for f in sorted(qdir.glob("quarantine_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            data["_file"] = f.name
            entries.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return entries


def release_quarantine(filename: str, quarantine_dir: Path | None = None) -> bool:
    """Release a specific quarantined payload (i.e., allow egress).

    This deletes the quarantine file — the content is NOT re-sent.
    Use only after human review confirms the content is safe.
    """
    qdir = quarantine_dir or DEFAULT_QUARANTINE_DIR
    path = qdir / filename
    if not path.exists():
        return False
    path.unlink()
    logger.info("Quarantine released: %s", filename)
    return True


def purge_quarantine(quarantine_dir: Path | None = None) -> int:
    """Purge ALL quarantined payloads. Returns count of removed files."""
    qdir = quarantine_dir or DEFAULT_QUARANTINE_DIR
    if not qdir.exists():
        return 0
    files = list(qdir.glob("quarantine_*.json"))
    count = len(files)
    for f in files:
        f.unlink()
    logger.warning("Quarantine purged: %d files removed", count)
    return count
