---
name: agy-long-running-tasks
description: "Guidelines and equivalents for running long-running or unattended tasks (>30 mins) in AGY, detailing interactive TUI commands (/goal, /yolo) and their headless CLI equivalents."
version: 1.0.0
---

# AGY Long Running Tasks

Establish guidelines, configuration, and patterns for executing long-running tasks (>30 minutes) and unattended execution in Google Antigravity (AGY).

> [!NOTE]
> **Memory Note:** `/goal` and `/yolo` are TUI-only interactive commands; their headless CLI equivalents (`--print-timeout`, `--dangerously-skip-permissions`, and the Mandatory Finish Protocol) must be used in automation and non-interactive workflows.

## Trigger Conditions

Use this skill when:
- Configuring or launching tasks expected to run longer than 30 minutes.
- Running headless batch operations or automated scripts (e.g., cron jobs, CI/CD pipelines, supervisor loops).
- Migrating interactive TUI command workflows (`/goal`, `/yolo`) to headless CLI environments.

## TUI Slash Commands vs. Headless Equivalents

Interactive TUI mode supports slash commands to control execution behavior:
- `/goal`: Tells the agent to keep working and checking state dynamically until a desired goal state is reached.
- `/yolo`: Toggles auto-approval of tool calls to run without manual confirmation prompts (equivalent to `"toolPermission": "always-proceed"` in `settings.json`).

These slash commands do **not** function in headless mode (e.g., using `--print` or non-interactive terminals). Headless workflows must use their headless equivalents instead:

| TUI Command | Headless CLI / Prompt Equivalent |
|---|---|
| `/yolo` | `--dangerously-skip-permissions` CLI flag |
| `/goal <text>` | `--print-timeout 24h0m0s` CLI flag + MANDATORY FINISH PROTOCOL in prompt |
| Stuck detection | Inactivity watchdog (kills task if no sandbox file modifications for `AGY_INACTIVITY_KILL_SEC`) |

---

## Numbered Steps for Headless Execution

1. **Invoke the AGY Binary with Unbounded Timeout and Sandbox Options**:
   When launching tasks >30 minutes, invoke the binary `agy-bin` directly and pass the required CLI options: `--print-timeout 24h0m0s`, `--dangerously-skip-permissions`, `--sandbox`, and `--add-dir <path>`.
   ```bash
   /home/ubuntu/.local/bin/agy-bin --print-timeout 24h0m0s --dangerously-skip-permissions --sandbox --add-dir <path> --print "your prompt here"
   ```
   - `--print-timeout 24h0m0s`: Overrides default execution limits to allow unbounded task time.
   - `--dangerously-skip-permissions`: Disables confirmation prompts, allowing full auto-approval of tools.
   - `--sandbox`: Runs the task inside the sandbox environment.
   - `--add-dir <path>`: Mounts or includes the target workspace directory inside the sandbox.

2. **Inject the Mandatory Finish Protocol into the Prompt**:
   To emulate the `/goal` behavior (ensuring the agent completes the work, runs self-validation, and registers verification before exiting), you MUST append the **Mandatory Finish Protocol** verbatim to the end of the prompt:
   ```text
   MANDATORY FINISH PROTOCOL:
   1. Write a complete summary to RESULT.md (use Write tool). RESULT.md must include: what you did, files changed, commit hashes, test results (if any), follow-ups.
   2. Run self-review: python3 ~/.hermes/profiles/orchestrator/scripts/agy_self_review.py <ISSUE_ID>
   3. After self-review posts, output DONE: <ISSUE_ID> <one-line summary> as the LAST line.
   4. If you cannot save RESULT.md, output ERROR: <ISSUE_ID> <reason> instead.
   ```

3. **Verify Execution Progress and Logs**:
   Monitor the task logs or system processes. As the task progresses, verify that the supervisor and quality gates handle the completion flow correctly.

---

## Pitfalls

- **TUI Commands in Headless Prompts**: Prompts passed to `--print` must never include raw `/goal` or `/yolo` slash commands. These commands are interactive-only and will be ignored or treated as plain text.
- **Model Selection naming**: Running `agy models` displays human-friendly names (e.g., `Gemini 3.5 Pro` or `Claude 3.5 Sonnet`). However, when passing the model to the CLI, you must use the exact model ID/string (e.g., `gemini-3.1-pro-high` or `claude-sonnet-4.6-thinking`).
- **Sandbox `~` Path Trap**: Within the sandbox runtime, the home directory `~` resolves to `/root` or `/sandbox` instead of the host's `/home/ubuntu`. Always use absolute paths `/home/ubuntu/` or dynamic environment variables (`$HOME`) when referencing resources outside the sandbox.
- **Silent Terminal Hangs**: Headless execution without a PTY allocation can cause output streams to starve and block progress. Wrap headless CLI calls with PTY emulators (e.g., `script -qc`) if running in backgrounds.

---

## Verification Steps

To verify that the headless execution system, timeout override, auto-approval, and finish validation function correctly:

1. **Verify Log Lifecycle Output**:
   Check the supervisor and task execution logs. A successful headless completion sequence must show the following progression:
   - When the agent finishes writing its summary:
     ```text
     [<ISSUE_ID>] 🟢 RESULT.md detected — waiting for self-review/DONE or natural exit
     ```
   - When the self-review completes and the task quality gates are validated:
     ```text
     [<ISSUE_ID>] ✅ quality-gate fired
     ```

2. **Run a Local Smoke Test**:
   Execute a test run bypassing approval prompts to write a verified file:
   ```bash
   /home/ubuntu/.local/bin/agy-bin --print-timeout 24h0m0s --dangerously-skip-permissions --sandbox --add-dir /tmp --print "Write a one-sentence summary of the workspace to /tmp/verify-long-running.txt."
   ```
   Ensure:
   - The command exits successfully with code 0.
   - The file `/tmp/verify-long-running.txt` is created containing the summary, without prompting for manual confirmation.
