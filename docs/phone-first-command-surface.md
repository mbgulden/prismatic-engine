# Phone-First Command Surface Strategy

> **Core Objective:** Provide Michael and engine operators with immediate, low-friction phone-based visibility and control over running Prismatic / AGY agent instances without requiring custom mobile app stores or heavy client development.

This document details the strategy, roadmap, and interaction designs for the phone-first Prismatic command surface. The guiding principle is to bridge the product gap by prioritizing **Telegram as the primary interface** (Telegram-First) and establishing a **mobile Progressive Web App as the secondary interface** (PWA-Second).

---

## 1. Telegram-First Decision & Current State

### Why Telegram-First?

Building native iOS/Android apps or a complex custom mobile web interface first introduces significant development friction and distribution delays. Telegram is chosen as the immediate, primary control surface due to several structural advantages:

*   **Zero App Store Friction:** Bypasses Apple App Store and Google Play Store review times, guidelines, and enterprise provisioning.
*   **Built-in Push Notifications:** Delivers high-priority alerts (e.g., human-in-the-loop approvals, critical test failures) natively to the lock screen with zero custom infrastructure.
*   **Immediate Authenticated Session Context:** Leverages Telegram's secure account ID mapped to a Prismatic developer/operator key, allowing session persistence without custom session token management on mobile.
*   **Rich Interactive Controls:** Supports custom keyboard grids (`ReplyKeyboardMarkup`) for primary navigation and inline keyboards (`InlineKeyboardMarkup`) with callback payloads for context-specific actions (e.g., approving a git push).
*   **Zero Mobile Compile/Refresh Cycle:** Adapters can be modified on the server and instantly tested on any phone running Telegram.

### What Lives There Now (Gateway Engine Architecture)

The Telegram adapter acts as an asynchronous, webhook-driven gateway that bridges the Telegram Bot API to the Prismatic Engine core.

```text
  Telegram Client (Phone)
            │
            ▼ (Secure HTTPS Webhook)
  Prismatic Telegram Gateway (Adapter)
            │
            ▼ (Command Router / Validator)
  Prismatic RPC Engine Core (prismatic.commands.*)
            │
            ▼ (Subprocess / API Invocation)
    AGY / Provider Instances
```

Currently, the gateway exposes:
1.  **Chat Routing:** Direct mapping from a Telegram Chat ID to a Prismatic run session, enabling interactive text steering.
2.  **Command Prefixing:** Routing of `/status`, `/runs`, `/approve`, `/halt` directly to the `prismatic.commands` namespace.
3.  **Active Session Binding:** The ability to attach/detach a chat session to specific workspace lanes.

---

## 2. PWA-Second Roadmap & Why Not First

### Why PWA-Second?

While a Progressive Web App (PWA) offers visual rich-dashboard capabilities, it is deferred to Phase 2 for the following reasons:
*   **High Development Overhead:** Creating a fully responsive, touch-optimized, premium dashboard interface (wow aesthetics) with custom visual state graphs takes significantly longer than implementing Telegram interactive UI layouts.
*   **Notification Hurdles:** iOS PWA push notifications require explicit user permission, a secure context (HTTPS/SSL), and are historically less reliable than native Telegram message delivery.
*   **Offline/Sync Burden:** Mobile web views require service-worker caching architectures to prevent white-screen load errors when the phone loses cellular signal.

### PWA Roadmap & Milestones

The transition from Telegram-First to PWA-Second is structured in four distinct phases:

```text
┌──────────────────────────┐     ┌──────────────────────────┐
│   Phase 1: Read-Only     │ ──> │  Phase 2: Live Command   │
│   Mobile Dashboard       │     │  Websocket Controls      │
└──────────────────────────┘     └──────────────────────────┘
             │                                │
             ▼                                ▼
┌──────────────────────────┐     ┌──────────────────────────┐
│   Phase 3: Web Push      │ ──> │   Phase 4: Offline       │
│   Notifications Setup    │     │   State & Sync Cache     │
└──────────────────────────┘     └──────────────────────────┘
```

*   **Phase 1: Read-Only Mobile Dashboard (Target: Q3 2026)**
    *   Optimize the standalone `prismatic-command-center` web interface using CSS Flexbox/Grid media queries to scale down seamlessly on viewports under 768px.
    *   Expose real-time read-only status widgets: active task list, VRAM observability, and running agent health.
*   **Phase 2: Live Command WebSocket Controls (Target: Q3 2026)**
    *   Enable remote command execution (pause, resume, abort, approve) from the mobile browser using a secure token-based web socket connection.
*   **Phase 3: Web Push Notifications Setup (Target: Q4 2026)**
    *   Implement Web Push API integration in the PWA service worker.
    *   Register notification subscriptions to forward high-priority engine events (like `human.intervention`) to the device OS.
*   **Phase 4: Offline State & Sync Cache (Target: Q4 2026)**
    *   Implement IndexedDB storage in the PWA client to store cached logs and workspace statuses.
    *   Allow users to queue steering suggestions offline, syncing them back to the engine upon network reconnection.

---

## 3. Phone Screen & Interaction Flows

The following interactive patterns are implemented via Telegram to streamline mobile operations:

### Flow 1: Agent Intervention & Approval Flow

This flow triggers when an agent hits a safety boundary (e.g., git push or database migration) and yields.

```text
┌────────────────────────────────────────────────────────┐
│ 🔔 [ALERT] Run 'run_981' Needs Approval               │
│                                                        │
│ Task: GRO-1975 Document Strategy                       │
│ Agent: AGY-01                                          │
│ Step: 12 (Git Push)                                    │
│ Diff Summary: +15 lines, -2 lines in docs/             │
│                                                        │
│ ┌──────────────────────┐  ┌──────────────────────────┐ │
│ │  [ Approve & Push ]   │  │   [ Reject & Comment ]   │ │
│ └──────────────────────┘  └──────────────────────────┘ │
│ ┌────────────────────────────────────────────────────┐ │
│ │                  [ View Full Diff ]                │ │
│ └────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

1.  **Alert:** Prismatic engine hits a yield point and fires a `human.intervention` event.
2.  **Notification:** The Telegram Bot sends the card above with inline buttons.
3.  **Interaction:**
    *   Tapping `[View Full Diff]` replies with a formatted patch snippet.
    *   Tapping `[Approve & Push]` sends a callback query to the gateway, resuming the agent.
    *   Tapping `[Reject & Comment]` prompts the user to type feedback which is appended to the agent's run context as a rejection comment.

### Flow 2: Swarm Multi-Instance Control Flow

This flow allows listing, monitoring, and tweaking resource consumption on the go.

```text
┌────────────────────────────────────────────────────────┐
│ 🟢 Active AGY Instances (2/3)                          │
│                                                        │
│ 1. gro-1975-dev (Active)                               │
│    VRAM: 4.2GB / Core: Gemini 3.5 Flash                │
│    [Pause]  [Logs]                                     │
│                                                        │
│ 2. docs-audit (Yielded/Waiting)                        │
│    VRAM: 0.1GB / Core: Gemini 3.5 Pro                  │
│    [Resume]  [View Approval]                           │
│                                                        │
│ 3. test-runner (Stopped)                               │
│    VRAM: 0.0GB                                         │
│    [Restart]  [Logs]                                   │
└────────────────────────────────────────────────────────┘
```

1.  **Trigger:** User sends `/instances` to the bot.
2.  **Display:** Bot returns a real-time list of running agents with active VRAM footprint and core model.
3.  **Actions:** Inline buttons trigger instant session manipulations (`pause`, `resume`, `restart`, `logs`).

### Flow 3: Session Chat & Steering Flow

This flow enables interactive pair programming/steering from the phone.

```text
┌────────────────────────────────────────────────────────┐
│ User:                                                  │
│ Check the commit convention docs before pushing.       │
│                                                        │
│ Bot (gro-1975-dev):                                    │
│ ⚙️ Instruction received and appended to workspace path. │
│ Analyzing COMMIT_CONVENTION.md...                      │
│ Formatting commit message: "docs: add phone-first      │
│ command surface strategy".                             │
│                                                        │
│ [View Run Summary]     [Pause Session]                 │
└────────────────────────────────────────────────────────┘
```

1.  **Context:** The chat is bound to an active session.
2.  **Message:** User types a regular message (e.g., "Check the commit convention...").
3.  **Routing:** The message is inserted as a steering instruction into the running workspace queue.
4.  **Acknowledge:** The bot replies with the agent's response stream.

### Flow 4: Active Run Telemetry & Summary Flow

This flow displays progress checkpoints without polluting the chat.

```text
┌────────────────────────────────────────────────────────┐
│ 📋 Run telemetry: run_981                              │
│                                                        │
│ * Status: Running (Step 8/10)                          │
│ * Duration: 4m 32s                                     │
│ * Task: "Create phone-first strategy docs"             │
│                                                        │
│ Checkpoint Log:                                        │
│ ✅ Read GRO-1975 task specifications                   │
│ ✅ Listed documentation workspace directory            │
│ 🔄 Writing docs/phone-first-command-surface.md         │
│ 🔲 Update docs/standalone-command-center-...           │
│                                                        │
│ [Fetch Latest Logs]    [Halt Process]                  │
└────────────────────────────────────────────────────────┘
```

1.  **Trigger:** User sends `/telemetry` or clicks `[View Run Summary]`.
2.  **Telemetry Display:** Concise vertical checkpoint view showing completed, current, and pending checklist items.

---

## 4. Gateway to Engine Command Mapping

When a user taps an interactive button on their phone, the Telegram Gateway translates the UI event into a structured RPC payload routed to the core `prismatic.commands.*` registry.

| Mobile UI Action | Callback Data Payload | Prismatic Core CLI / RPC Map |
| :--- | :--- | :--- |
| **Approve Run** | `cmd:approve:<run_id>` | `prismatic.commands.run.approve(run_id)` |
| **Reject & Terminate** | `cmd:reject:<run_id>` | `prismatic.commands.run.reject(run_id, reason)` |
| **Pause Agent Session** | `cmd:pause:<session_id>` | `prismatic.commands.session.pause(session_id)` |
| **Resume Agent Session**| `cmd:resume:<session_id>`| `prismatic.commands.session.resume(session_id)`|
| **Halt/Kill Session** | `cmd:halt:<session_id>` | `prismatic.commands.session.halt(session_id)` |
| **Request Workspace Diff**| `cmd:diff:<run_id>` | `prismatic.commands.run.diff(run_id)` |
| **Request Text Summary**| `cmd:summary:<run_id>` | `prismatic.commands.run.summary(run_id)` |

### Command API Specifications (Python Draft)

```python
namespace prismatic.commands:

    def approve_run(run_id: str, decision_id: str = None) -> dict:
        """Resolves a pending human-intervention yield lock on a workflow run."""
        pass

    def reject_run(run_id: str, decision_id: str = None, reason: str = None) -> dict:
        """Terminates or rolls back a run, attaching user feedback to the run log."""
        pass

    def pause_session(session_id: str) -> dict:
        """Sends SIGTSTP to the underlying runner process and updates registry state."""
        pass

    def resume_session(session_id: str) -> dict:
        """Sends SIGCONT to the runner process, resuming task execution loops."""
        pass
```

---

## 5. Explicit Out-of-Scope Boundaries

To maintain focus and avoid architectural bloat, the following capabilities are explicitly out of scope:

1.  **Mobile Code Editing:** No code editors, file patch generators, or syntax highlighting editing textboxes on the phone client. Code creation/modification remains 100% autonomous or local to desktop IDE environments.
2.  **Native App Codebases:** No React Native, Swift (iOS), or Kotlin (Android) codebases will be built or maintained. All mobile surfaces are strictly HTML5/CSS3 PWAs or standard Telegram Bot API client wrappers.
3.  **Raw Shell Emulation:** No interactive SSH terminal emulator or raw bash execution from the phone. All interactions must proceed through validated API/RPC command endpoints.
4.  **Complex Graph Visualization:** Multi-lane Git trees, node-heavy task DAGs, or full interactive terminal scrolls will not be rendered on the phone screen. These remain exclusive to desktop widescreen command hubs.

---

## 6. Implementation Backlog (Next 3 Linear Issues)

These issues are structured for immediate assignment and execution.

### GRO-1976: Implement Telegram Gateway Adapter with Inline Keyboard Callback Router

*   **Description:** Build the Python Telegram Bot interface adapter. Implement webhook routing, session parsing, and mapping of `InlineKeyboardMarkup` callback queries to internal dispatcher commands.
*   **Acceptance Criteria:**
    *   Bot correctly registers webhooks with Telegram APIs using environment keys.
    *   Inline keyboard callback payloads map to correct target schemas.
    *   A test runner script verifies `/status` and `/runs` commands return structured markdown to the client.
    *   Proper security check ensures only authorized Telegram IDs can issue callback requests.

### GRO-1977: Implement Core Gateway Commands for Session & Run Steering

*   **Description:** Implement the backend controller command layer in `prismatic.commands` supporting remote session lifecycle operations: `pause`, `resume`, `halt`, `approve`, and `reject`.
*   **Acceptance Criteria:**
    *   Commands accurately suspend and resume the OS process running the target runner.
    *   The engine yields database locks properly during a paused state.
    *   Calling `approve` or `reject` modifies the state of running task graphs dynamically.
    *   Commands are fully unit-tested with mocked runners.

### GRO-1978: Build Mobile-Friendly Responsive Views for Standalone Command Center (PWA Phase 1)

*   **Description:** Write responsive layout rules in `prismatic-command-center` frontend assets (CSS, templates) to optimize dashboard grids for viewports under 768px.
*   **Acceptance Criteria:**
    *   Dashboard navigation bar collapses into a touch-friendly slide-out menu.
    *   Active task tables collapse to readable card-based list layouts.
    *   Buttons are optimized for mobile touch targets (minimum 44x44px padding).
    *   Verify responsive scaling across simulated Chrome Mobile DevTools configurations.
