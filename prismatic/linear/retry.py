"""Resilient Linear API retry helper with exponential backoff on HTTP 429."""

from __future__ import annotations

import email.utils
import json
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


def execute_linear_request(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str = "POST",
    timeout: float = 30.0,
) -> Any:
    """Executes a request to the Linear API, retrying on HTTP 429 using
    exponential backoff with jitter and respecting rate limit headers.

    Retries up to 10 times (11 attempts total).
    """
    max_retries = 10
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                delay = None
                if exc.headers:
                    # 1. Respect Retry-After header
                    retry_after = exc.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            # Try parsing HTTP date
                            try:
                                dt = email.utils.parsedate_to_datetime(retry_after)
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                diff = (dt - datetime.now(timezone.utc)).total_seconds()
                                delay = max(0.0, diff)
                            except Exception:
                                pass

                    # 2. Respect X-RateLimit headers
                    if delay is None:
                        for hname in (
                            "X-RateLimit-Requests-Reset",
                            "x-ratelimit-requests-reset",
                            "X-RateLimit-Reset",
                            "x-ratelimit-reset",
                        ):
                            reset_val = exc.headers.get(hname)
                            if reset_val:
                                try:
                                    val_f = float(reset_val)
                                    # Distinguish epoch seconds vs epoch milliseconds
                                    if val_f > 1_000_000_000_000:
                                        diff = (val_f / 1000.0) - time.time()
                                    else:
                                        diff = val_f - time.time()
                                    delay = max(0.0, diff)
                                    break
                                except ValueError:
                                    pass

                # 3. Fallback to exponential backoff with jitter
                if delay is None or delay <= 0:
                    base_delay = 2.0 ** attempt  # 1s, 2s, 4s, 8s, ...
                    jitter = random.uniform(0.0, 1.0)
                    delay = base_delay + jitter

                print(
                    f"[Linear API] HTTP 429 Rate Limit Hit. "
                    f"Retrying attempt {attempt + 1}/{max_retries} in {delay:.2f} seconds..."
                )
                time.sleep(delay)
                continue
            raise
