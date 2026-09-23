"""RF-5: Event Broadcasting Helper for Review Factory V1.

Publishes realtime SwarmEvent messages via the Gateway EventBus singleton:
- ``review_factory.job_enqueued``
- ``review_factory.job_state_changed``
- ``review_factory.receipt_issued``
- ``review_factory.authorization_created``
- ``review_factory.merge_completed``
- ``review_factory.repair_exhausted``
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from prismatic.gateway.event_bus import get_event_bus

    _HAS_EVENT_BUS = True
except ImportError:
    _HAS_EVENT_BUS = False

logger = logging.getLogger(__name__)


def emit_rf_event(
    event_type: str, payload: dict[str, Any], source: str = "review_factory"
) -> None:
    """Safely publish an event to the Gateway EventBus (no-op if bus is unavailable)."""
    if not _HAS_EVENT_BUS:
        return

    try:
        bus = get_event_bus()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(bus.publish(event_type, source, payload))
        except RuntimeError:
            # No running event loop in thread; run synchronously or skip
            asyncio.run(bus.publish(event_type, source, payload))
    except Exception as exc:
        logger.debug("Failed to publish event %s: %s", event_type, exc)
