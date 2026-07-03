from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from prismatic.api.auth import get_current_user
from prismatic.dispatcher import AGENT_LAUNCHERS

router = APIRouter()

class JobSubmission(BaseModel):
    agent: str
    issue_id: str
    title: Optional[str] = None
    priority: int = 3
    task: Optional[str] = None

@router.post("/jobs")
async def submit_job(
    job: JobSubmission,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    if job.agent not in AGENT_LAUNCHERS:
        raise HTTPException(status_code=400, detail=f"Invalid agent: {job.agent}")

    launcher = AGENT_LAUNCHERS[job.agent]

    # We use BackgroundTasks to launch the agent so we can return quickly
    def launch_agent_task():
        try:
            # Different agents have different launcher signatures in dispatcher.py
            if job.agent in ["fred", "kai"]:
                # signal_fred(issue_id, title="", priority=3)
                # signal_kai(issue_id, title="", priority=3, signal_type="")
                launcher(job.issue_id, title=job.title or "", priority=job.priority)
            else:
                # launch_agy(issue_id, task="")
                # launch_jules(issue_id, task="")
                # launch_codex(issue_id, task="")
                launcher(job.issue_id, task=job.task or "")
        except Exception as e:
            print(f"[api] Error launching agent {job.agent} in background: {e}")

    background_tasks.add_task(launch_agent_task)

    return {
        "status": "submitted",
        "agent": job.agent,
        "issue_id": job.issue_id,
        "message": f"Job for agent {job.agent} on issue {job.issue_id} has been queued."
    }
