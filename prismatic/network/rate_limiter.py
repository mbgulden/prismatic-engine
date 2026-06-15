"""Token-bucket rate limiting and resilient retry helpers.

The defaults intentionally match the EDGE-WORK-001 pre-flight requirements:
critical domains (LinkedIn/Google) run at 0.2 req/s, cautious business
registry domains at 0.5 req/s, and everything else at 1 req/s.
"""

from __future__ import annotations

import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from prismatic.telemetry import DEFAULT_DB_PATH

DEFAULT_RETRY_STATUSES = {429, 502, 503, 504}


@dataclass(frozen=True)
class DomainPolicy:
    """Rate-limit policy for a domain tier."""

    tier: str
    requests_per_second: float
    burst: int
    min_delay_between_requests_seconds: float
    max_requests_per_session: int
    requires_proxy: bool = False
    domains: tuple[str, ...] = field(default_factory=tuple)


DOMAIN_TIERS: tuple[DomainPolicy, ...] = (
    DomainPolicy(
        tier="critical",
        domains=("linkedin.com", "google.com", "googleapis.com"),
        requests_per_second=0.2,
        burst=1,
        min_delay_between_requests_seconds=5.0,
        max_requests_per_session=20,
        requires_proxy=True,
    ),
    DomainPolicy(
        tier="cautious",
        domains=("yellowpages.com", "yelp.com", "manta.com", "bbb.org", "angi.com"),
        requests_per_second=0.5,
        burst=2,
        min_delay_between_requests_seconds=3.0,
        max_requests_per_session=50,
    ),
    DomainPolicy(
        tier="standard",
        domains=("*",),
        requests_per_second=1.0,
        burst=3,
        min_delay_between_requests_seconds=1.0,
        max_requests_per_session=100,
    ),
)


@dataclass(frozen=True)
class BrowserProfile:
    """Realistic browser request headers used to avoid default bot headers."""

    user_agent: str
    accept_language: str = "en-US,en;q=0.9"
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            "Accept": self.accept,
        }


BROWSER_PROFILES: tuple[BrowserProfile, ...] = (
    BrowserProfile("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
    BrowserProfile("Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0"),
    BrowserProfile("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"),
    BrowserProfile("Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"),
    BrowserProfile("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"),
    BrowserProfile("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0"),
    BrowserProfile("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/125.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15"),
    BrowserProfile("Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (Android 14; Mobile; rv:126.0) Gecko/126.0 Firefox/126.0"),
    BrowserProfile("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    BrowserProfile("Mozilla/5.0 (X11; Fedora; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"),
    BrowserProfile("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", "en-US,en;q=0.9,haw;q=0.5"),
)


class ResponseLike(Protocol):
    status_code: int


@dataclass(frozen=True)
class RateLimitDecision:
    domain: str
    tier: str
    delay_seconds: float
    proxy_required: bool
    session_request_count: int


@dataclass(frozen=True)
class NetworkEvent:
    agent_id: str
    lane_id: str
    target_domain: str
    target_url: str
    http_status: int | None = None
    response_time_ms: int | None = None
    rate_limit_hit: bool = False
    proxy_used: str | None = None
    backoff_applied: bool = False
    retry_count: int = 0


class TokenBucket:
    """Thread-safe token bucket with injectable clock for tests."""

    def __init__(
        self,
        rate_per_second: float,
        capacity: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.rate_per_second = rate_per_second
        self.capacity = capacity
        self._clock = clock
        self._tokens = float(capacity)
        self._updated_at = clock()
        self._lock = threading.Lock()

    def reserve(self, tokens: int = 1) -> float:
        """Reserve tokens and return required wait seconds before the request."""
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._updated_at)
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)
            self._updated_at = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0
            missing = tokens - self._tokens
            wait = missing / self.rate_per_second
            self._tokens = 0.0
            self._updated_at = now + wait
            return wait


class TokenBucketRateLimiter:
    """Per-domain token bucket limiter with min-delay and session caps."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
        self._rng = rng or random.Random()
        self._buckets: dict[str, TokenBucket] = {}
        self._last_request_at: dict[str, float] = {}
        self._session_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def choose_browser_profile(self) -> BrowserProfile:
        return self._rng.choice(BROWSER_PROFILES)

    def browser_headers(self) -> dict[str, str]:
        return self.choose_browser_profile().headers()

    def reserve(self, url_or_domain: str, *, sleep: bool = True) -> RateLimitDecision:
        domain = normalize_domain(url_or_domain)
        policy = get_domain_policy(domain)
        with self._lock:
            count = self._session_counts.get(domain, 0) + 1
            if count > policy.max_requests_per_session:
                raise RuntimeError(
                    f"session cap exceeded for {domain}: {count}>{policy.max_requests_per_session}"
                )
            self._session_counts[domain] = count
            bucket = self._buckets.get(domain)
            if bucket is None:
                bucket = TokenBucket(policy.requests_per_second, policy.burst, clock=self._clock)
                self._buckets[domain] = bucket
            token_delay = bucket.reserve()
            now = self._clock()
            previous = self._last_request_at.get(domain)
            min_delay = 0.0
            if previous is not None:
                min_delay = max(0.0, policy.min_delay_between_requests_seconds - (now - previous))
            delay = max(token_delay, min_delay)
            self._last_request_at[domain] = now + delay
        if sleep and delay > 0:
            self._sleep(delay)
        return RateLimitDecision(
            domain=domain,
            tier=policy.tier,
            delay_seconds=delay,
            proxy_required=policy.requires_proxy,
            session_request_count=count,
        )

    def backoff_delay(self, retry_index: int, *, base: float = 1.0, cap: float = 32.0) -> float:
        deterministic_ceiling = min(cap, base * (2 ** retry_index))
        return self._rng.uniform(0.0, deterministic_ceiling)


def normalize_domain(url_or_domain: str) -> str:
    parsed = urlparse(url_or_domain if "://" in url_or_domain else f"//{url_or_domain}")
    host = (parsed.hostname or url_or_domain).lower().strip().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def get_domain_policy(url_or_domain: str) -> DomainPolicy:
    domain = normalize_domain(url_or_domain)
    standard = DOMAIN_TIERS[-1]
    for policy in DOMAIN_TIERS:
        if "*" in policy.domains:
            standard = policy
            continue
        for suffix in policy.domains:
            if domain == suffix or domain.endswith(f".{suffix}"):
                return policy
    return standard


def ensure_network_events_table(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS network_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                agent_id TEXT,
                lane_id TEXT,
                target_domain TEXT,
                target_url TEXT,
                http_status INTEGER,
                response_time_ms INTEGER,
                rate_limit_hit INTEGER DEFAULT 0,
                proxy_used TEXT,
                backoff_applied INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_network_events_domain ON network_events(target_domain, timestamp)")
        conn.commit()
    finally:
        conn.close()


def record_network_event(event: NetworkEvent, *, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    ensure_network_events_table(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO network_events
               (timestamp, agent_id, lane_id, target_domain, target_url, http_status,
                response_time_ms, rate_limit_hit, proxy_used, backoff_applied, retry_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                event.agent_id,
                event.lane_id,
                event.target_domain,
                event.target_url,
                event.http_status,
                event.response_time_ms,
                1 if event.rate_limit_hit else 0,
                event.proxy_used,
                1 if event.backoff_applied else 0,
                event.retry_count,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def guarded_request(
    request_fn: Callable[..., ResponseLike],
    url: str,
    *,
    limiter: TokenBucketRateLimiter | None = None,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    proxy: str | None = None,
    max_retries: int = 5,
    retry_statuses: set[int] | None = None,
    agent_id: str = "unknown",
    lane_id: str = "unknown",
    db_path: str | Path | None = None,
    **kwargs: Any,
) -> ResponseLike:
    """Run a request behind token-bucket and exponential-backoff guards.

    `request_fn` is injected to keep this module dependency-free. It can be
    `requests.request`, an httpx-compatible wrapper, or a fake in tests.
    """
    limiter = limiter or TokenBucketRateLimiter()
    retry_statuses = retry_statuses or DEFAULT_RETRY_STATUSES
    domain = normalize_domain(url)
    response: ResponseLike | None = None
    last_status: int | None = None
    response_ms = 0
    backoff_applied = False
    for attempt in range(max_retries + 1):
        decision = limiter.reserve(url, sleep=True)
        merged_headers = limiter.browser_headers()
        if headers:
            merged_headers.update(dict(headers))
        start = time.monotonic()
        response = request_fn(method, url, headers=merged_headers, proxy=proxy, **kwargs)
        response_ms = int((time.monotonic() - start) * 1000)
        last_status = int(getattr(response, "status_code", 0))
        if last_status not in retry_statuses or attempt >= max_retries:
            if db_path is not None:
                record_network_event(
                    NetworkEvent(
                        agent_id=agent_id,
                        lane_id=lane_id,
                        target_domain=domain,
                        target_url=url,
                        http_status=last_status,
                        response_time_ms=response_ms,
                        rate_limit_hit=decision.delay_seconds > 0,
                        proxy_used=proxy,
                        backoff_applied=backoff_applied,
                        retry_count=attempt,
                    ),
                    db_path=db_path,
                )
            return response
        backoff_applied = True
        limiter._sleep(limiter.backoff_delay(attempt))
    raise RuntimeError(f"request retry loop exhausted for {url}; last_status={last_status}")
