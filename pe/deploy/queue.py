"""P1: Webhook Queue & Dead-Letter Queue for Deploy Hook V1.

Durable queueing of incoming webhook payloads to ``~/.prismatic/db/webhook_queue.json``
with exponential backoff retries (1s, 5s, 30s, 5m, 30m) and dead-letter storage
in ``~/.prismatic/db/webhook_dead_letter.json`` after 5 failed attempts.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BACKOFF_SCHEDULE = [1, 5, 30, 300, 1800]  # seconds
MAX_ATTEMPTS = 5


def default_state_dir() -> Path:
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
    p = Path(state_dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_queue_path() -> Path:
    return default_state_dir() / "webhook_queue.json"


def default_dead_letter_path() -> Path:
    return default_state_dir() / "webhook_dead_letter.json"


@dataclass
class WebhookItem:
    item_id: str
    payload: Dict[str, Any]
    queued_at: float
    attempts: int = 0
    next_retry_at: float = 0.0
    last_error: str = ""
    status: str = "pending"  # pending, processing, completed, dead_letter

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WebhookItem:
        return cls(
            item_id=data.get("item_id", f"wh-{uuid.uuid4().hex[:8]}"),
            payload=data.get("payload", {}),
            queued_at=data.get("queued_at", time.time()),
            attempts=data.get("attempts", 0),
            next_retry_at=data.get("next_retry_at", 0.0),
            last_error=data.get("last_error", ""),
            status=data.get("status", "pending"),
        )


class WebhookQueue:
    """Durable queue for deploy webhooks."""

    def __init__(
        self,
        queue_path: Optional[Path] = None,
        dead_letter_path: Optional[Path] = None,
    ):
        self.queue_path = queue_path or default_queue_path()
        self.dead_letter_path = dead_letter_path or default_dead_letter_path()
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_items(self, path: Path) -> List[WebhookItem]:
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                return [WebhookItem.from_dict(d) for d in raw]
        except Exception as exc:
            logger.error("Failed to read webhook queue file %s: %s", path, exc)
            return []

    def _write_items(self, path: Path, items: List[WebhookItem]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump([item.to_dict() for item in items], f, indent=2)
        except Exception as exc:
            logger.error("Failed to write webhook queue file %s: %s", path, exc)

    def enqueue(self, payload: Dict[str, Any]) -> WebhookItem:
        """Enqueue a new webhook payload."""
        items = self._read_items(self.queue_path)
        item = WebhookItem(
            item_id=f"wh-{uuid.uuid4().hex[:8]}",
            payload=payload,
            queued_at=time.time(),
            next_retry_at=time.time(),
        )
        items.append(item)
        self._write_items(self.queue_path, items)
        logger.info("Enqueued webhook item %s", item.item_id)
        return item

    def get_pending(self) -> List[WebhookItem]:
        """Get items ready for processing."""
        now = time.time()
        items = self._read_items(self.queue_path)
        return [
            item
            for item in items
            if item.status in ("pending", "processing") and item.next_retry_at <= now
        ]

    def record_success(self, item_id: str) -> None:
        """Mark a webhook item as successfully processed."""
        items = self._read_items(self.queue_path)
        updated = []
        for item in items:
            if item.item_id == item_id:
                item.status = "completed"
            else:
                updated.append(item)
        self._write_items(self.queue_path, updated)

    def record_failure(self, item_id: str, error_msg: str) -> Optional[WebhookItem]:
        """Record a processing failure, scheduling a retry or moving to dead-letter queue."""
        items = self._read_items(self.queue_path)
        dead_letters = self._read_items(self.dead_letter_path)
        target_item: Optional[WebhookItem] = None

        remaining = []
        for item in items:
            if item.item_id == item_id:
                item.attempts += 1
                item.last_error = error_msg

                if item.attempts >= MAX_ATTEMPTS:
                    item.status = "dead_letter"
                    dead_letters.append(item)
                    logger.warning(
                        "Webhook item %s moved to dead-letter queue after %d attempts: %s",
                        item.item_id,
                        item.attempts,
                        error_msg,
                    )
                else:
                    backoff = BACKOFF_SCHEDULE[
                        min(item.attempts - 1, len(BACKOFF_SCHEDULE) - 1)
                    ]
                    item.next_retry_at = time.time() + backoff
                    item.status = "pending"
                    remaining.append(item)
                    logger.info(
                        "Webhook item %s retry #%d scheduled in %ds",
                        item.item_id,
                        item.attempts,
                        backoff,
                    )
                target_item = item
            else:
                remaining.append(item)

        self._write_items(self.queue_path, remaining)
        self._write_items(self.dead_letter_path, dead_letters)
        return target_item

    def list_queue(self) -> List[Dict[str, Any]]:
        return [i.to_dict() for i in self._read_items(self.queue_path)]

    def list_dead_letters(self) -> List[Dict[str, Any]]:
        return [i.to_dict() for i in self._read_items(self.dead_letter_path)]
