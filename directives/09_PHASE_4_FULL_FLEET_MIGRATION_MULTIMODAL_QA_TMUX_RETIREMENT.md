# Directive 09: Full Fleet Migration, Multimodal Visual QA Gate & Legacy Tmux Retirement (Phase 4)

**Status:** Approved Architecture Directive & Operational Standard
**Owner:** Prismatic Engine Core & Swarm Quality Assurance Maintainers
**Target Linear Issue:** [GRO-4864](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4864)
**Applicable Nodes:** PVE Cluster (PVE1 Server GPU for Fred & Ned, PVE3 for George & Kai), `webtop-hermes` (Central Gateway/Hub), Lightbringer (Operator Satellite Client)
**Prerequisite Commits:** Phase 1 (`[GRO-4861](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4861)`), Phase 2 (`[GRO-4862](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4862)`), Phase 3 (`[GRO-4863](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4863)`)
**System of Record:** `prismatic/fleet/manager.py` + `prismatic/verification/multimodal_qa.py` + OKF Standard

---

## 🎯 Executive Summary & Mission Objective

Phase 4 represents the final milestone of the Antigravity SDK integration:
1. **Default Fleet Runtime Cutover**: Switches the entire multi-profile agent fleet (`orchestrator` / Fred, `george`, `kai`, `ned`, `autobot`) from legacy CLI tmux wrapping to `AGYSDKHarness` by default.
2. **In-Session Multimodal Visual QA Gate**: Integrates `google.antigravity.Image.from_file()` directly into the verification pipeline to inspect Playwright 375px mobile snapshots for WCAG AA contrast, logo clear space, and mobile layout integrity before certifying PR readiness.
3. **Legacy Tmux Scraping Retirement**: Formally deprecates ANSI log parsing, PTY wrappers, and external shell hooks, archiving them into an emergency fallback mode (`--fallback-cli`).

---

## 🏗️ Technical Architecture & Cutover Workflow

```text
                               ┌────────────────────────────────┐
                               │     Prismatic Fleet Core       │
                               │  (Fred, George, Kai, Ned)      │
                               └───────────────┬────────────────┘
                                               │
                                      PRISMATIC_AGY_RUNTIME
                                       (Default: "sdk")
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │         AGYSDKHarness          │
                               │ • In-process Python coroutines │
                               │ • In-process SwarmLock hooks   │
                               │ • Real-time SSE signals stream │
                               │ • Directive 05 state anchor    │
                               └───────────────┬────────────────┘
                                               │
                                 Verification & PR Gate
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │   Multimodal Visual QA Gate    │
                               │   Image.from_file(375px_shot)  │
                               │   Gemini 3.7 Vision Evaluation │
                               │   WCAG AA + Logo Padding Check │
                               └────────────────────────────────┘
```

### 1. Fleet-Wide Default Cutover
File: [`prismatic/fleet/manager.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/fleet/manager.py) and [`prismatic/harnesses/__init__.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/harnesses/__init__.py)

- Update `get_agy_harness()` to default to `runtime="sdk"`.
- Update `FleetManager.sync_profiles()` to configure `runtime: "sdk"` in all agent profile `config.yaml` files.
- Update `hermes-gateway@.service` systemd unit template to run with `Environment="PRISMATIC_AGY_RUNTIME=sdk"`.

### 2. In-Session Multimodal Visual QA Gate
File: [`prismatic/verification/multimodal_qa.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/verification/multimodal_qa.py)

**Audit Interface**:
```python
from google.antigravity import Agent, Image

class MultimodalVisualQAGate:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent

    async def audit_mobile_viewport(self, screenshot_path: str) -> dict:
        """Inspect Playwright 375px screenshot using Gemini multimodal vision."""
        image = Image.from_file(screenshot_path)
        prompt = """
        Perform a strict compliance audit on this 375px mobile viewport screenshot:
        1. Navigation: Is the hamburger menu button present, distinct, and clickable?
        2. Logo Clear Space: Is the logo preserved with sufficient padding (>=1.5x x-height)?
        3. Content Clipping: Are there any horizontally clipped headers, tables, or text?
        4. Text Contrast: Does all text appear readable with >= 4.5:1 contrast against its background?

        Respond ONLY with a JSON object:
        {
          "passed": bool,
          "hamburger_visible": bool,
          "logo_clear_space_ok": bool,
          "no_horizontal_scroll": bool,
          "contrast_ok": bool,
          "findings": list[str]
        }
        """
        response = await self.agent.run(prompt, attachments=[image])
        return response.json()
```

### 3. Retirement of Legacy Subprocess Hooks
- Archive `~/.antigravity/hooks/prismatic_hook.py` as legacy.
- In [`prismatic/harnesses/agy_cli.py`](https://prismatic.growthwebdev.com/workspaces?file=prismatic/harnesses/agy_cli.py), mark `AGYCLIHarness` with `@deprecated("Use AGYSDKHarness via PRISMATIC_AGY_RUNTIME=sdk")`.
- Remove ANSI scraping regexes from active production monitoring loops.

---

## 📋 Mandatory OKF Directives for Phase 4

To keep all AI agents aligned, the executing agent MUST perform the following OKF documentation updates:

### OKF Directive 4.1: Finalize Canonical OKF Standard
File: [`docs/okf-antigravity-sdk-dual-instance-integration.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/okf-antigravity-sdk-dual-instance-integration.md)
- Update Section `🎯 OKF Objectives & Key Results` to status **Fully Operational**.
- Complete Section `📊 Verification Receipts & Cross-References` with all test suite digests.
- Update **Fence 6** (Multimodal Visual QA & Layout Compliance).

### OKF Directive 4.2: Update Canonical OKF Evidence Map
File: [`docs/okf-evidence-map.md`](https://prismatic.growthwebdev.com/workspaces?file=docs/okf-evidence-map.md)
- Add verification evidence links for SDK dual-instance execution and multimodal audit receipts.

### OKF Directive 4.3: Validate Documentation Parity
- Run `python3 -m pytest tests/test_okf_docs.py`
- Run `python3 scripts/validate_okf_docs.py` to confirm zero violations across the documentation plane.

---

## 🔒 Invariant & Failure-Mode Fencing

1. **Visual Regression Gate Invariant**: Any frontend pull request that causes `no_horizontal_scroll: false` or `hamburger_visible: false` in `audit_mobile_viewport()` MUST fail the verification gate and block merge authorization.
2. **Emergency Fallback Invariant**: If a catastrophic regression occurs in the SDK runtime, operators can execute `prismatic fleet sync --runtime cli` to instantly revert the fleet to `AGYCLIHarness` within 10 seconds.
3. **Receipt Authenticity Invariant**: Visual QA audit receipts must include the SHA-256 digest of the inspected screenshot file.

---

## 🧪 Acceptance Criteria & Test Plan

1. **Unit & Integration Tests (`tests/test_multimodal_visual_qa.py`)**:
   - Test `audit_mobile_viewport()` with mock passing mobile screenshot.
   - Test detection of horizontal clipping defect.
   - Test detection of obscured hamburger navigation button.
   - Test generation of structured audit receipt JSON.
2. **Fleet Cutover Verification**:
   - Run `prismatic fleet status --dynamic` verifying all agents show `runtime: sdk`.
   - Run `prismatic fleet sync --dynamic` with zero errors.
3. **Full Fleet Barrage**:
   - Execute sequential audit (`scripts/run_unified_streaming_swarm_audit.py`) with `AGYSDKHarness` active across Fred, George, Kai, and Ned.
   - Assert 100% test pass across all unit, concurrency, and OKF test suites.

---

## 🤖 Peer-Agent Handoff Instructions

- **For Kai (UI/Mobile Specialist)**: Use `MultimodalVisualQAGate` on all mobile viewport artifacts before submitting pull requests.
- **For George (Concurrency Specialist)**: Monitor SwarmLock deflection metrics to ensure zero lock contention spikes post-cutover.
- **For Ned (Verification Guard)**: Verify that all PR handoffs include exact-head commit SHA, tree SHA, and visual QA audit receipts.
