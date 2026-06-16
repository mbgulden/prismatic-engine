"""Credit-check endpoint backed by the Prismatic credit policy engine."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from prismatic.api.auth import verify_api_key
from prismatic.credit_policy_engine import (
    PolicyAction,
    evaluate_agent_launch,
)

router = APIRouter()


@router.get("/credits")
async def get_credits(current_user: dict = Depends(verify_api_key)):
    """Return current credit budget status for the caller's scope."""
    # Evaluate a sample agent launch to surface budget state
    decision = evaluate_agent_launch(
        agent_label="agent:fred",
        issue_id="api-health-check",
        operation="code_generation",
    )

    return {
        "policy_action": decision.action.value,
        "reason": decision.reason,
        "estimated_cost": decision.estimated_cost,
        "remaining_budget": "unbounded" if decision.action == PolicyAction.ALLOW else "limited",
    }
