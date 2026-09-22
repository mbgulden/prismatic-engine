"""Deploy-time Linear transition tests (mock-only, no live Linear calls).

Covers the transition path that was silently dead: ``LinearDeployTransitioner``
now calls ``LinearTaskProvider.update_issue_state`` (GraphQL) instead of
importing the ``linear_helpers`` module that never existed.
"""

from __future__ import annotations

import pytest

from pe.deploy.linear_transition import (
    LinearDeployTransitioner,
    LinearTransitionsStore,
)


class FakeLinearProvider:
    """In-memory stand-in for LinearTaskProvider (never touches the network)."""

    def __init__(self, response=None):
        self.response = (
            {"ok": True, "issue": "GRO-5105", "state": "Done"}
            if response is None
            else dict(response)
        )
        self.calls: list[tuple[str, str]] = []

    def update_issue_state(self, issue_id, state_name="Done"):
        self.calls.append((issue_id, state_name))
        return dict(self.response)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PRISMATIC_LINEAR_TRANSITIONS_DB", str(tmp_path / "linear_transitions.json")
    )
    return LinearTransitionsStore()


def _patch_provider(monkeypatch, fake):
    monkeypatch.setattr(
        "prismatic.providers.tasks.linear.LinearTaskProvider", lambda: fake
    )


def test_transition_success_records_receipt(isolated_store, monkeypatch):
    fake = FakeLinearProvider()
    _patch_provider(monkeypatch, fake)
    transitioner = LinearDeployTransitioner(store=isolated_store)
    receipts = transitioner.transition_issues_for_deploy(
        deploy_id="deploy-1", pr_sha="abc123", pr_title="Fix thing (GRO-5105)"
    )
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.success is True
    assert receipt.issue_id == "GRO-5105"
    assert receipt.to_state == "Done"
    assert receipt.linear_response.get("ok") is True
    assert fake.calls == [("GRO-5105", "Done")]


def test_transition_error_is_loud_not_silent(isolated_store, monkeypatch):
    fake = FakeLinearProvider(response={"error": "boom"})
    _patch_provider(monkeypatch, fake)
    transitioner = LinearDeployTransitioner(store=isolated_store)
    (receipt,) = transitioner.transition_issues_for_deploy(
        deploy_id="deploy-1", pr_sha="abc123", pr_title="Fix thing (GRO-5105)"
    )
    assert receipt.success is False
    assert receipt.linear_response == {"error": "boom"}


def test_unconfigured_key_is_a_loud_skip(isolated_store, monkeypatch):
    fake = FakeLinearProvider(
        response={"error": "LINEAR_API_KEY not set; transition skipped"}
    )
    _patch_provider(monkeypatch, fake)
    transitioner = LinearDeployTransitioner(store=isolated_store)
    (receipt,) = transitioner.transition_issues_for_deploy(
        deploy_id="deploy-1", pr_sha="abc123", pr_title="Fix thing (GRO-5105)"
    )
    assert receipt.success is False
    assert "LINEAR_API_KEY" in receipt.linear_response["error"]


def test_idempotency_dedupes_repeat_deploys(isolated_store, monkeypatch):
    fake = FakeLinearProvider()
    _patch_provider(monkeypatch, fake)
    transitioner = LinearDeployTransitioner(store=isolated_store)
    kwargs = dict(deploy_id="deploy-1", pr_sha="abc123", pr_title="Fix (GRO-5105)")
    first = transitioner.transition_issues_for_deploy(**kwargs)
    second = transitioner.transition_issues_for_deploy(**kwargs)
    assert first[0].success and second[0].success
    assert second[0].linear_response["status"] == "idempotent_dedupe"
    assert len(fake.calls) == 1


def test_rate_limit_batches_and_queues_remainder(isolated_store, monkeypatch):
    fake = FakeLinearProvider()
    _patch_provider(monkeypatch, fake)
    transitioner = LinearDeployTransitioner(store=isolated_store)
    title = " ".join(f"GRO-{5000 + i}" for i in range(12))
    receipts = transitioner.transition_issues_for_deploy(
        deploy_id="deploy-1", pr_sha="abc123", pr_title=title
    )
    assert len(receipts) == 10
    assert len(fake.calls) == 10
    assert len(isolated_store.get_queued()) == 2
    # Next deploy drains the queue.
    receipts2 = transitioner.transition_issues_for_deploy(
        deploy_id="deploy-2", pr_sha="def456", pr_title="unrelated"
    )
    assert len(receipts2) == 2
    assert isolated_store.get_queued() == []


def test_dry_run_never_calls_linear(isolated_store):
    fake = FakeLinearProvider()
    transitioner = LinearDeployTransitioner(store=isolated_store, dry_run=True)
    (receipt,) = transitioner.transition_issues_for_deploy(
        deploy_id="deploy-1", pr_sha="abc123", pr_title="Fix (GRO-5105)"
    )
    assert receipt.success is True
    assert receipt.linear_response == {"status": "dry_run"}
    assert fake.calls == []


def test_extract_issue_ids_dedupes_and_normalizes():
    ids = LinearDeployTransitioner.extract_issue_ids(
        "gro-5105 and GRO-5105 plus GRO-5106"
    )
    assert ids == ["GRO-5105", "GRO-5106"]
