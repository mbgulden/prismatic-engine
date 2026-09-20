"""Tests for Review Factory Wiring Gaps 1 & 3 (Auto-Ingestion & Verification Daemon)."""

from __future__ import annotations

import time
from pathlib import Path
import pytest

from prismatic.gateway.verification_daemon import VerificationWorkerDaemon, start_verification_daemon, stop_verification_daemon
from prismatic.review_factory.backlog_importer import BacklogImporter
from prismatic.review_factory.queue import ReviewQueue


def test_verification_daemon_lifecycle(tmp_path: Path) -> None:
    daemon = VerificationWorkerDaemon(poll_interval_seconds=0.1)
    daemon.start()
    assert daemon._running is True
    assert daemon._thread is not None and daemon._thread.is_alive()
    daemon.stop()
    assert daemon._running is False


def test_global_verification_daemon_helpers() -> None:
    d1 = start_verification_daemon()
    assert d1._running is True
    stop_verification_daemon()
    assert d1._running is False


def test_backlog_importer_scan() -> None:
    importer = BacklogImporter()
    res = importer.import_from_completed_work()
    assert res.scanned >= 0
