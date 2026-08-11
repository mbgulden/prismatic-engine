# Swarm Visibility & Linking Guidelines
To ensure a seamless mobile experience for the user when we mention tasks or files in conversation:
1. **Linear Tasks**: When mentioning a Linear issue identifier (e.g. `GRO-3319`), always format it as a markdown link pointing to:
   `https://prismatic.growthwebdev.com/tab/tasks?issue=IDENTIFIER`
   (Example: `[GRO-3319](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3319)`)
2. **Repository Files**: When mentioning a file path in the workspace (e.g. `prismatic/gateway/server.py`), always format it as a markdown link pointing to:
   `https://prismatic.growthwebdev.com/workspaces?file=PATH`
   (Example: `[server.py](https://prismatic.growthwebdev.com/workspaces?file=prismatic/gateway/server.py)`)

# Brand Logo Sizing & Presentation Guidelines
To maintain respect and design integrity for company logos:
1. **Aspect Ratio Constraints**:
   - **Horizontal Wordmark Logos**: Height range `30px` to `45px` on desktop headers, and `24px` to `32px` on mobile headers (maximum width `400px`).
   - **Vertical/Square Logos**: Height range `40px` to `55px` on desktop headers, and `35px` to `45px` on mobile.
2. **Clear Space**: Always maintain a clear padding boundary around the logo container of at least `1.5 * x-height` (relative to the height of the wordmark letters) or 15-20% of the overall logo size.
3. **Format & Density**: Strongly prefer SVG. If using PNG/raster, provide exactly `@2x` double-density assets with a transparent background.
4. **Contrast**: Ensure contrast of at least `4.5:1` against header background. Toggle light/dark logo classes dynamically on header scroll.

# Standard Navigation & Accessibility Guidelines
Every website navigation implementation must follow one of these 4 rock-solid layout schemas:
1. **Schema 1: Clean Inline Nav** — Left logo, center/right links, right CTA button. Collapses to accessible hamburger button.
2. **Schema 2: Centered Split Nav** — Logo centered horizontally, nav links split evenly to the left and right.
3. **Schema 3: Left-Sidebar Nav** — Sticky left sidebar with collapsible states, ideal for deep content or dashboards.
4. **Schema 4: Floating Capsule Nav** — Minimalist, floating glassmorphism pill capsule at the top or bottom of the screen.

**Accessibility Requirements**:
1. **Focus States**: Never set `outline: none` or remove focus rings. Style focus states with `:focus-visible` to match brand theme with at least `3:1` contrast ratio.
2. **Semantic Elements**: Wrap navigation inside `<nav>` containing native `<ul>` and `<li>` elements.
3. **Hamburger Buttons**: Must be `<button>` elements possessing:
   - `aria-expanded="false"` (toggles to `"true"` when drawer is open).
   - `aria-controls="menu-container-id"`.
   - Explicit text description via `aria-label="Toggle menu"`.
4. **Focus & Keyboard Traps**: When drawer is closed, hidden links must use `display: none` or `visibility: hidden` (or `tabindex="-1"`). Focus must move to the first link on drawer open, and return to the hamburger trigger on close. Escape key (`Esc`) must close drawer immediately.

# Mandatory Verification & Subagent Claim Protocol ("Don't Trust, Verify")
To enforce absolute system reliability, prevent self-deceiving claims, and ensure zero unverified subagent summaries:
1. **Mandatory Skill Activation**: Before completing any task attempt, code feature, bug fix, subagent delegation, or PR handoff, Antigravity MUST invoke and verify compliance against these mandatory verification skills:
   - [subagent-claim-verification-gate](file:///c:/Users/Michael%20Gulden/Github/Hermes/.agents/skills/subagent-claim-verification-gate/SKILL.md): Never accept subagent or background task outputs as verified fact without running independent `Get-FileHash`, `Test-Path`, `view_file`, or process exit code checks.
   - [prismatic-validation-pipeline](file:///c:/Users/Michael%20Gulden/Github/Hermes/.agents/skills/prismatic-validation-pipeline/SKILL.md): Run local TDD unit tests, public launch smoke checks, and peer validation loops.
   - [antigravity-prismatic-pr-evidence](file:///c:/Users/Michael%20Gulden/Github/Hermes/.agents/skills/antigravity-prismatic-pr-evidence/SKILL.md): Bind exact-head commit SHA (`git rev-parse HEAD`), tree SHA, zero `git diff --check` warnings, and 375px Playwright visual audits.
   - [agy-runtime-contract-closure](file:///c:/Users/Michael%20Gulden/Github/Hermes/.agents/skills/agy-runtime-contract-closure/SKILL.md): Enforce the 6 Anti-Deception invariants (observable execution proof, route surface proof, clean wheel distribution testing, boundary fences, and receipt identity truth).
2. **Subagent Claim Verification Invariant**: Subagent output summaries are classified as `PRODUCER_CLAIM_UNVERIFIED` until Antigravity independently verifies the handles (file paths, SHA-256 digests, process exit codes) directly against disk or runtime tools.
3. **Execution Evidence Ledger Requirement**: Every completed work attempt MUST include an explicit **Machine Verification Evidence Ledger** in the final response containing exact commit/tree SHAs, command exit codes, and log digests.

