"""Job submission and status endpoints for the Prismatic API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from prismatic.api.auth import verify_api_key
from prismatic.dispatcher import AGENT_LAUNCHERS

router = APIRouter()

# In-memory job store (MVP — replace with Redis for production)
_jobs: dict[str, dict[str, Any]] = {}


class JobSubmission(BaseModel):
    agent: str = Field(..., description="Target agent: fred, jules, codex, agy, kai")
    title: str = Field(default="", description="Human-readable job title")
    description: str = Field(default="", description="Detailed job description / task")
    priority: int = Field(default=3, ge=1, le=5)


class JobStatus(BaseModel):
    job_id: str
    agent: str
    status: str
    issue_id: str | None = None
    message: str = ""


@router.get("/jobs")
async def list_jobs(current_user: dict = Depends(verify_api_key)):
    """List all submitted jobs and their statuses."""
    return {
        "jobs": [
            {"job_id": jid, "agent": j["agent"], "status": j["status"]}
            for jid, j in _jobs.items()
        ]
    }


@router.post("/jobs", status_code=201)
async def submit_job(
    job: JobSubmission,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_api_key),
):
    """Submit a new job for a Prismatic agent."""
    agent = job.agent.lower()

    if agent not in AGENT_LAUNCHERS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid agent '{agent}'. Valid: {', '.join(AGENT_LAUNCHERS.keys())}",
        )

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "agent": agent,
        "title": job.title,
        "description": job.description,
        "priority": job.priority,
        "status": "queued",
        "issue_id": None,
    }

    launcher_fn = AGENT_LAUNCHERS[agent]

    def _dispatch():
        """Run the agent launcher in background."""
        _jobs[job_id]["status"] = "running"
        try:
            # Agent launchers have different signatures
            if agent in ("fred", "kai"):
                launcher_fn(job_id, title=job.title, priority=job.priority)
            else:
                launcher_fn(job_id, task=job.description or job.title)
            _jobs[job_id]["status"] = "completed"
        except Exception as exc:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(exc)

    background_tasks.add_task(_dispatch)

    return JobStatus(
        job_id=job_id,
        agent=agent,
        status="queued",
        message=f"Job queued for agent '{agent}'",
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, current_user: dict = Depends(verify_api_key)):
    """Retrieve the status and result of a submitted job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job
