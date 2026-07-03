from fastapi import APIRouter, Depends
from prismatic.api.auth import get_current_user
from prismatic.credit_tracker import AIUltraCreditTracker

router = APIRouter()

@router.get("/credits")
async def get_credits(current_user: dict = Depends(get_current_user)):
    tracker = AIUltraCreditTracker()
    remaining = tracker.get_remaining_credits()
    spent = tracker.calculate_monthly_spent()
    velocity = tracker.calculate_burn_velocity()

    return {
        "remaining_credits": remaining,
        "monthly_spent": spent,
        "burn_velocity": velocity,
        "provider": "google-antigravity",
    }
