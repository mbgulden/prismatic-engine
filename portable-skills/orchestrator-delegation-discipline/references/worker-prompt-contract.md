# Worker prompt contract

Use this when delegating to a specialist lane.

## Required fields
- Identity: who the worker is in this task (e.g. implementation specialist, reviewer, researcher)
- Role: the lane's responsibility boundary
- Persona: operating style expected (careful reviewer, fast implementer, synthesis-oriented analyst)
- Objective: the exact deliverable
- Constraints: files, systems, or behaviors to avoid
- Completion criteria: what must be true for the work to count as done
- Verification: how the orchestrator will confirm the claim

## Good pattern
"You are the implementation specialist for repo X. Your role is to make the smallest correct change to Y. Persona: precise, proactive, no unnecessary commentary. Deliverable: a concise patch plus verification notes. Constraints: do not touch Z. Done means A, B, and C are true. Return exact file paths and any commands used for verification."

## Orchestrator verification checklist
- The worker returned a concrete artifact, not only a narrative.
- The artifact is inspectable: file path, diff, URL, issue ID, or command output.
- The result was verified externally before acceptance.
- If the worker claimed success without evidence, rerun or reassign the task.
