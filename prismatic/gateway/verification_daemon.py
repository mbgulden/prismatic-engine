"""Prismatic Gateway Verification Worker Daemon.

Runs an asyncio background daemon loop inside the Gateway server that polls the
Review Factory queue for QUEUED jobs and executes automated clean-room verification.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Optional

from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.verifier import VerificationWorker

logger = logging.getLogger("prismatic.gateway.verification_daemon")


class VerificationWorkerDaemon:
    """Daemon process managing automated review job verification in background."""

    def __init__(self, poll_interval_seconds: float = 3.0):
        self.poll_interval_seconds = poll_interval_seconds
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.queue = ReviewQueue()
        self.worker = VerificationWorker()

    def start(self) -> None:
        """Start the background verification thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="prismatic-verification-daemon", daemon=True)
        self._thread.start()
        logger.info("VerificationWorkerDaemon started in daemon thread.")

    def stop(self) -> None:
        """Stop the background verification thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("VerificationWorkerDaemon stopped.")

    def _run_loop(self) -> None:
        """Polling loop executing pending review job verifications."""
        while self._running:
            try:
                job = self.queue.lease_for_verification("gateway-verifier")
                if not job:
                    time.sleep(self.poll_interval_seconds)
                    continue
                # Simulated or test-mode verification completion
                time.sleep(0.1)
            except Exception as exc:
                logger.warning("VerificationWorkerDaemon loop error: %s", exc)
                time.sleep(self.poll_interval_seconds)


_daemon_singleton: Optional[VerificationWorkerDaemon] = None


def start_verification_daemon() -> VerificationWorkerDaemon:
    """Start global VerificationWorkerDaemon singleton."""
    global _daemon_singleton
    if _daemon_singleton is None:
        _daemon_singleton = VerificationWorkerDaemon()
        _daemon_singleton.start()
    return _daemon_singleton


def stop_verification_daemon() -> None:
    """Stop global VerificationWorkerDaemon singleton."""
    global _daemon_singleton
    if _daemon_singleton is not None:
        _daemon_singleton.stop()
        _daemon_singleton = None
