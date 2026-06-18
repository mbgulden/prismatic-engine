# Prismatic Engine First-User Journey: 5-Minute Demo

This document maps out the end-to-end first-user journey for Prismatic Engine v0.1. It acts as the "Golden Thread" walkthrough, showcasing the five-minute demo flow: installing the engine, getting immediate value, attaching capabilities, running a task, observing from a phone, reviewing the work, and choosing a next step.

Following this narrative is the **Linear-Ready Implementation Trail**, which outlines the exact tasks required to bridge the gap between our current codebase and this seamless user experience.

---

## Part 1: The Onboarding Narrative & User Journey

### Step 1: Install & Bootstrap
The user has a clean shell environment on a local machine with Python 3.10+ and git installed. They bootstrap the entire engine with a single command:

```bash
curl -fsSL https://prismaticengine.com/install.sh | bash
```

**Behind the Scenes / Engine Mechanics:**
1. The script checks for Python 3.10+ and `pip` availability.
2. It clone/downloads the `prismatic-engine` repository.
3. Installs the package in editable mode: `pip install -e .`
4. Creates the global configuration directory `~/.prismatic/`.
5. Runs `prismatic-engine init` to generate `~/.prismatic/config.yaml`.
6. Sets up the unified `prismatic` binary shortcut in `~/.local/bin/prismatic`.

---

### Step 2: First Value - `prismatic status`
Immediately after installation, the user verifies the setup to get quick, local diagnostics:

```bash
prismatic status
```

**Expected CLI Output:**
```text
=========================================================
Prismatic Engine — Status & Diagnostics
=========================================================
[System] Python version: 3.10.12
[System] Git version: git version 2.34.1
[System] gh CLI version: gh version 2.20.0

[Config] PRISMATIC_HOME: ~/work
[Config] User Config: ~/.prismatic/config.yaml (exists)
[Config] Database: ~/.prismatic/event_router.db (exists)

[Capabilities] Verifying registered capabilities...
  linear:missing (LINEAR_API_KEY env var is not set)
  vcs.github:missing (GITHUB_TOKEN / GH_TOKEN is not set)
  agy:missing (AGY_TOKEN is not set)
  jules:ok (Using default local validation)
  telegram:missing (TELEGRAM_BOT_TOKEN is not set)
  schedule:ok (Local SQLite tracking active)
  artifact:ok (Local artifact storage active)

Status Verdict: WARN (Run 'prismatic providers attach' to enable integration capabilities)
```

**Value Delivered:** The user receives verification that the system is functional and is informed which integration keys are currently missing.

---

### Step 3: Attach Capabilities (GitHub & AGY)
The user connects external adapters using the interactive CLI tool:

```bash
prismatic providers attach github --token <github-token>
prismatic providers attach agy --token <agy-token>
```

**Behind the Scenes / Engine Mechanics:**
1. The engine validates the tokens against GitHub and AGY APIs.
2. It writes the credentials securely to `~/.prismatic/config.yaml`.
3. Running `prismatic status` now shows `vcs.github:ok` and `agy:ok`.

---

### Step 4: Start One Task
The user triggers an autonomous coding agent task directly in their repository:

```bash
prismatic task create "Audit this repository for linting errors" --agent agy --workspace .
```

Or, if they are using the automated Linear queue:
```bash
prismatic-engine serve --once
```

**Behind the Scenes / Engine Mechanics:**
1. The engine registers a task entry in the local SQLite database.
2. Resolves write permissions based on agent lanes defined in `PRISMATIC_ENGINE.yaml`.
3. Dispatches the signal to the AGY adapter.
4. Streams execution logs to `~/.prismatic/logs/agy-session-<session_id>.log`.

---

### Step 5: Observe & Review from Phone
The user steps away from their laptop. All monitoring and telemetry are routed to their phone via Telegram:

1. **Bot Notification:** `🤖 Task Audit-Repo started on AGY agent (session_id: agy-782a).`
2. **User Inquiry:** The user types `/agy status` to the bot.
   - **Bot Response:** `Session agy-782a is 45% complete. Working on fixing whitespace and docstring imports in prismatic/doctor.py.`
3. **PR Creation:** Once AGY finishes the code changes, it opens a pull request:
   - **Bot Response:** `🔗 PR opened: https://github.com/mbgulden/prismatic-engine/pull/15`

---

### Step 6: Merge & Publish
The Prismatic Engine automatically dispatches a code review request to the Jules CLI reviewer:

1. **Bot Notification:** `🔍 Jules CLI review started for PR #15.`
2. **Review Result:** Within two minutes, Jules CLI completes the code audit:
   - **Bot Response:** `✓ Jules Review APPROVED: 'Code quality passes standards. No breaking changes detected.'`
3. **Completion:** The PR is auto-merged, and the corresponding Linear issue transitions to `Done`.

---

### Step 7: Next Steps: Contribution or Hosted-Offering
The terminal walkthrough offers two clear branches for the user:

1. **Prismatic Cloud (Hosted Offering):** Transition local daemon workloads to our hosted infrastructure for 24/7 autonomous polling:
   ```bash
   prismatic cloud attach
   ```
2. **Extend & Contribute:** Build new dashboard widgets under `plugins/` or customize agent write lanes in `PRISMATIC_ENGINE.yaml`.

---

## Part 2: Linear-Ready Implementation Trail

This section lists the exact subtasks required to build the gaps separating our current code from this first-user journey. These issues are ready to be imported into Linear under the `GRO` project.

### GRO-1980: Setup Unified CLI Wrapper (`prismatic`)
* **Title:** Setup Unified CLI Wrapper (`prismatic`)
* **Priority:** High
* **Estimate:** 3 Story Points
* **Description:** 
  Create a global python wrapper script/entrypoint named `prismatic` that unifies all command-line operations (e.g. `prismatic doctor`, `prismatic init`, `prismatic journal snapshot`) to replace the fragmented `prismatic-*` scripts in user-facing environments.
* **Acceptance Criteria:**
  - [ ] Add `prismatic` to entry points in `pyproject.toml`.
  - [ ] Support redirecting subcommands: `prismatic status` maps to `prismatic-engine doctor`.
  - [ ] Ensure arguments and flags are forwarded transparently.
  - [ ] Write a smoke test validating command mapping.

### GRO-1981: Implement Interactive `prismatic providers attach` Command
* **Title:** Implement Interactive `prismatic providers attach` Command
* **Priority:** High
* **Estimate:** 5 Story Points
* **Description:**
  Add a command to dynamically configure integration tokens (GitHub, AGY, Linear, Telegram) via CLI inputs. This command must validate the credentials against their respective APIs before writing them to the YAML configuration.
* **Acceptance Criteria:**
  - [ ] Implement `prismatic providers attach <provider_name> --token <token>`.
  - [ ] Perform connection health check using credential registry validator.
  - [ ] Save validated config keys securely to `~/.prismatic/config.yaml`.
  - [ ] Fail gracefully with clear remediation steps if validation fails.

### GRO-1982: Support Local Tasks without Linear Integration
* **Title:** Support Local Tasks without Linear Integration
* **Priority:** Medium
* **Estimate:** 5 Story Points
* **Description:**
  Introduce a local task database provider so developers can test agent execution cycles without connecting to a remote Linear project.
* **Acceptance Criteria:**
  - [ ] Create `prismatic task create "task text" --agent <agent_name> --workspace <path>` command.
  - [ ] Insert local tasks into SQLite event table.
  - [ ] Modify the coordinator/dispatcher polling cycle to check for local queued tasks before polling remote trackers.

### GRO-1983: Telegram Bot Interactive State Commands
* **Title:** Telegram Bot Interactive State Commands
* **Priority:** Medium
* **Estimate:** 8 Story Points
* **Description:**
  Add command handler logic to the Telegram adapter to receive user signals from their phones and communicate execution control back to the engine.
* **Acceptance Criteria:**
  - [ ] Implement commands `/agy status`, `/agy pause`, `/agy resume`, `/agy kill`.
  - [ ] Safely send Unix signals or write control files to active agent workspaces.
  - [ ] Return real-time progress percentages and latest active log snippets.

### GRO-1984: Jules CLI PR Review Handoff Integration
* **Title:** Jules CLI PR Review Handoff Integration
* **Priority:** Medium
* **Estimate:** 5 Story Points
* **Description:**
  Wire the `jules` capability adaptor to execute `jules review` or dispatch a review request to `jules.google.com` as soon as an agent opens a pull request.
* **Acceptance Criteria:**
  - [ ] Listen for `vcs.pr_opened` event.
  - [ ] Trigger Jules CLI review subprocess on the PR diff.
  - [ ] Capture the stdout verdict and translate it into a `jules.review_completed` event.

### GRO-1985: Cloud Onboarding & Local Detach Path
* **Title:** Cloud Onboarding & Local Detach Path
* **Priority:** Low
* **Estimate:** 8 Story Points
* **Description:**
  Build a migration subcommand that allows local users to upload their SQLite journal history and config values to the Prismatic Cloud server and stop the local engine process.
* **Acceptance Criteria:**
  - [ ] Implement `prismatic cloud attach` CLI command.
  - [ ] Compress `~/.prismatic/` configs, DB, and logs.
  - [ ] Upload payload to remote REST api.
  - [ ] Deactivate local engine cron schedules.
