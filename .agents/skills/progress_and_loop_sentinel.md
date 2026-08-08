---
name: progress-and-loop-sentinel
description: "Provides active loop prevention, progress heartbeats, and a 'get unstuck' protocol for Google Antigravity. ACTIVATE this skill to ensure high visibility, active progress updates, and safe loop breaking during long-running tasks."
---

# Antigravity Progress and Loop Sentinel Skill

This skill governs execution self-monitoring, loop prevention, and active feedback delivery to ensure the user always has visibility into Antigravity's state and that the agent never gets stuck in silent execution cycles.

## Core Mandates

### 1. Active Progress Heartbeats
*   **The 3-Step Rule**: You MUST output a short text status update in the chat interface at least once every 3 tool calls. Do not execute a long string of tools in total silence.
*   **The 60-Second Rule**: If a command or research step is expected to take more than 60 seconds (or is running in the background), output a brief note explaining what is running and when you expect it to return.
*   **Visual Checkpoints**: Always start each turn by outputting a small text-based progress indicator (e.g., `[===--] Step 2 of 4`) derived from your `task.md` or plan.

### 2. Loop Detection & Safety Gates
*   **Consecutive Action Cap**: You are strictly capped at **3 consecutive attempts** to solve a specific error or run the same failed command.
*   **Tool Error Limit**: If the same tool fails twice (or returns the same permission/syntax error), you MUST NOT try it a third time in the same way. You must stop and re-evaluate.
*   **Circular Thought Prevention**: If you find yourself formulating the same logic, files, or arguments more than twice, stop and raise a warning.

### 3. The \"Get Unstuck\" Protocol
If you trigger a loop gate, or if you feel you are not making progress after 3 attempts, you must immediately execute the **Get Unstuck Protocol**:
1.  **Halt Execution**: Stop all active loops, background compilations, or repeated command runs.
2.  **Save State**: Write a debug summary to `<appDataDir>\brain\<conversation-id>/scratch/stuck_diagnostics.json` containing:
    *   The goal you are trying to achieve.
    *   The exact errors or blockers you encountered.
    *   The tools you ran and their outputs.
3.  **Generate a Get Unstuck Dashboard**: Present the user with a clean, structured table in the chat:
    *   **Goal**: [What you were trying to do]
    *   **Blocker**: [Why you got stuck]
    *   **Attempted Fixes**: [List of 2-3 commands/edits you tried]
    *   **Diagnostic Details**: [Short explanation of why it failed]
4.  **Present Actionable Options**: Offer 3 clear paths forward:
    *   *Option A (Manual Intervention)*: The user edits a file, runs a command, or enters an API key.
    *   *Option B (Alternative Strategy)*: Try a completely different approach (e.g., swapping library, bypassing a tool, using mock data).
    *   *Option C (Interactive Debugging)*: Request the user to run `/grill-me` or ask a clarifying question.

### 4. Visibility of Background Work
*   Never run a background command or async task without printing its Task ID and a link to its status.
*   If a background task is running, use `schedule` to set a timer to check on it and report back, rather than polling in a busy loop.
