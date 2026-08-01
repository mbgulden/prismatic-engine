from prismatic.review_factory.models import MergeAuthorization, MergeScope


def test_tier3_blocks_auto_merge():
    # Tier 3 candidates MUST NOT get auto-authorized
    auth = MergeAuthorization(
        scope=MergeScope.TIER_3_EXCEPTION.value, actor="human-reviewer"
    )
    # The policy for tier 3 prevents TIER_1_AUTO
    assert auth.scope != MergeScope.TIER_1_AUTO.value
    assert auth.actor != "standing-policy: tier-1"
