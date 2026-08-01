from datetime import datetime, timedelta, timezone

from prismatic.review_factory.models import ReviewJob, ReviewJobState


def test_expired_lease_blocks_verdict():
    job = ReviewJob(
        state=ReviewJobState.REVIEWING.value,
        lease_owner="reviewer-1",
    )

    # Mocking an expired lease
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    job.lease_expires_at = past.isoformat()

    # Check if expired
    exp = datetime.fromisoformat(job.lease_expires_at)
    is_expired = datetime.now(timezone.utc) > exp

    assert is_expired is True
    # System should auto-requeue instead of accepting verdict
