"""Pytest conftest for tests at the repository root.

RF-M3 (see GRO-4504) intentionally removed the auto-wrap pattern that
previously installed a test-only wrapper on ``ReviewQueue.enqueue_completed_work``
at conftest-load time. Tests that need to enqueue completed work now use
``enqueue_with_defaults`` from ``prismatic.review_factory.testing`` explicitly.

Why this conftest still exists (intentionally empty)
----------------------------------------------------
The file is preserved so that any future root-level pytest fixture
declarations have a place to live. It is currently a no-op.

Historical context (RF-R2, prior to RF-M3)
------------------------------------------
Before RF-M3, this conftest installed a wrapper at module-load time
that filled in ``result_packet_path`` and ``result_packet_sha256``
defaults for any test that called
``ReviewQueue.enqueue_completed_work`` without supplying them. That
auto-wrap was brittle (see commit ``5e334c1``: install-once sentinel
fix) and is now removed.
"""
