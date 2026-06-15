"""Network edge guards for Prismatic outbound automation.

This package provides conservative, testable primitives for lead-gen and
outreach lanes: per-domain token buckets, retry/backoff, proxy rotation,
realistic browser header rotation, and semaphore-based session governance.
"""

from prismatic.network.rate_limiter import (
    DEFAULT_RETRY_STATUSES,
    DOMAIN_TIERS,
    BrowserProfile,
    DomainPolicy,
    NetworkEvent,
    RateLimitDecision,
    TokenBucket,
    TokenBucketRateLimiter,
    get_domain_policy,
    guarded_request,
    record_network_event,
)
from prismatic.network.proxy_rotator import ProxyConfig, ProxyRotator
from prismatic.network.session_governor import SessionGovernor, SessionLease

__all__ = [
    "DEFAULT_RETRY_STATUSES",
    "DOMAIN_TIERS",
    "BrowserProfile",
    "DomainPolicy",
    "NetworkEvent",
    "ProxyConfig",
    "ProxyRotator",
    "RateLimitDecision",
    "SessionGovernor",
    "SessionLease",
    "TokenBucket",
    "TokenBucketRateLimiter",
    "get_domain_policy",
    "guarded_request",
    "record_network_event",
]
