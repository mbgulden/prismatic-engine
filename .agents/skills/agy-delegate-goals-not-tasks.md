---
name: agy-delegate-goals-not-tasks
description: "THE #1 AGY WORKFLOW INSIGHT: Delegate goals, not tasks. Stop micromanaging snippets — let AGY run the full local loop (edit → build → verify) before anything hits staging. Direct from AGY's own advice to Fred, June 2026. Applies to Fred, Kai, Autobot, and any agent dispatching work to AGY."
---

# Delegate Goals, Not Tasks — AGY's Own Advice

> **Source:** AGY itself, June 8, 2026. This is AGY telling us how to work with it. Treat this as the canonical delegation pattern.

## The Core Rule

**Delegate goals, not tasks.** Frame instructions around the desired outcome ("Align the navigation to match the reference images exactly") rather than step-by-step commands ("Change line 15's color from #333 to #222").

## The Correct Pattern: End-to-End Goal Delegation

```
[Provide goal + references] → [AGY analyzes] → [AGY edits locally] → [AGY builds] → [AGY verifies] → [AGY deploys to staging] → [You show Michael verified result]
```

### Research-Only Pattern (June 10, 2026 — 3-attempt proving ground)

For PURE RESEARCH tasks, AGY must be isolated from the codebase. `--add-dir` triggers the builder instinct every time.

```bash
# PROVEN — from /tmp, no workspace, inline prompt:
cd /tmp && agy --prompt-interactive "Read /path/to/input-A.md and /path/to/input-B.md. Then write /path/to/output.md containing <deliverable>." 2>&1
```

**Rules:** No `--add-dir`. Max 2 input files. One clear output. PTY mode. See `antigravity-cli-orchestration → references/agy-clean-launch-pattern.md` for the full 3-attempt log.

### Non-TTY launch (cron, CI, automated agents)

`agy --prompt-interactive` opens a real TTY (bubbletea). Outside an interactive shell — cron jobs, CI runners, the Hermes `terminal()` tool without `pty=true` — it fails immediately with `error opening TTY: could not open TTY: open /dev/tty: no such device or address`.

**Fix:** wrap with `script(1)` to fake a TTY, or use the Hermes `terminal(pty=true)` flag.

```bash
# Pattern 1: script(1) wrapper
script -qc "cd /tmp && agy --prompt-interactive --print 'Goal: ...' 2>&1" /dev/null | tail -200

# Pattern 2: explicit redirect (loses interactive niceties; headless only)
cd /tmp && agy --prompt-interactive --print "Goal: ..." 2>&1 </dev/null
```

**Discovery note (Jun 18 2026):** The `agy` binary is actually a 1-line bash wrapper at `~/.local/bin/agy` that `exec`s `~/.local/bin/agy-bin`. Both call paths produce the same TTY error. The wrapper adds nothing except convenience — call `agy-bin` directly if you want to bypass shell-level PATH or env quirks. Full transport notes live in `antigravity-cli-orchestration`.

## Pick the Lane First

"Delegate goals, not tasks" is the universal dispatch pattern, but **which lane** of AGY are you delegating to? The lane picks the launch pattern, the cross-cutting skills to load, and the expected artifacts.

| If the goal is… | Lane | Load also… |
|---|---|---|
| Design a system, write an ADR, pick a tech | Architect | `agy-as-architect` (and `agy-prismatic-engine-authoring` if inside the Engine) |
| Implement a feature with a known contract | Coder | `agy-as-coder` (and `agy-tdd-discipline`, `agy-secure-coding` if touching credentials, `agy-documentation-on-the-fly`) |
| Find root cause of an open-ended bug | Debugger | `agy-as-debugger` (and `agy-systematic-debug`) |
| Decompose a goal into tasks | Planner | `agy-as-planner` |
| Review a PR / spec / architecture for verdict | Reviewer | `agy-as-reviewer` (and `agy-secure-coding` if reviewing auth/payment) |
| Generate pixel art / sprites / UI assets | Asset Generator | `agy-as-asset-generator` |
| Ship a playable web game | Game Dev | `agy-as-game-dev` (and `agy-tdd-discipline`, `agy-documentation-on-the-fly`) |
| Multi-source deep research, evidence ledger, history/current/trajectory reports | (separate skill, not a lane) | `agy-research-metabolizer` |

The full taxonomy, dispatch table, and decision tree live in **`agy-lane-taxonomy`**. Load it before any AGY dispatch.

### Image Generation Pattern

AGY CLI CAN generate images natively via `GenerateImage()` and Python/PIL procedural scripts. Proven prompt:
```
"Generate a 16-bit pixel art UI asset. <dimensions>. <style>. <hex colors>. 16-bit pixel art, crisp edges. PNG with transparency."
```
Assets land at `~/.gemini/antigravity-cli/brain/<session>/`. AGY also produces reusable generator scripts.

**For video/audio generation:** The CLI is text-only. Use the `google-antigravity` SDK directly (custom Python tools + Flow Studio Infrastructure) — see `antigravity-sdk-media-pipeline` skill for the full pattern with credit guardrails.

### Session Start: WHERE You Launch Matters

**For research, design, audit, or image-generation tasks: launch from `/tmp` with NO `--add-dir`.**
```bash
cd /tmp && agy --prompt-interactive "Read <design brief> at <path>. Produce <deliverable>."
```

**For code-modification tasks ONLY: use `--add-dir` to give AGY the workspace.**
```bash
cd ${PRISMATIC_HOME}/work/<project> && agy --prompt-interactive --add-dir ${PRISMATIC_HOME}/work/<project> "Goal: <change to make>"
```

**Why this matters:** `--add-dir` triggers AGY's build instinct — it sees code files and defaults to modifying them instead of doing research. For research/design/image tasks, this is fatal. The first AGY session in a Darius Star bootstrap ignored the research brief entirely and built sprite processing scripts because `--add-dir` exposed the codebase. Two subsequent sessions from `/tmp` without `--add-dir` produced perfect research documents.

### Direction Injection Mid-Session

When AGY is mid-design and you realize the direction is wrong, inject a correction BEFORE it writes the final file:
```bash
process(action='submit', data='IMPORTANT DIRECTION UPDATE: <specific correction>.', session_id='<pty_session>')
```

This is better than letting AGY finish and then asking for revisions — the correction becomes part of the current work, not a separate task.

### Image Generation Sessions

AGY (Gemini 3.5 Flash) can generate 16-bit pixel art sprites natively via `GenerateImage()`. See `antigravity-cli-orchestration → references/agy-image-generation-sprites.md`. Launch from `/tmp`, specify exact dimensions + hex colors + style direction + "16-bit pixel art, crisp edges, no anti-aliasing." Images land at `~/.gemini/antigravity-cli/brain/<session>/` and are saved BEFORE AGY's PIL post-processing step (which can hang — the image is already ready).

## The Local Loop (Non-Negotiable)

AGY MUST run the full cycle locally before anything hits staging:

1. **Analyze** — read reference images/screenshots, inspect DOM/CSS
2. **Edit** — modify templates, CSS, scripts in the workspace
3. **Build** — run compilation scripts (generate_pages.py, build commands)
4. **Verify** — visual QA, Python verification scripts, cross-check against references
5. **Deploy** — push to staging ONLY after local verification passes

**Staging is a production-replica environment. Nothing reaches staging — and nothing is shown to Michael — until AGY has verified it locally.**

## Specific Mistakes That Break This Pattern

1. **Showing unverified work to Michael** — deploying styling changes directly to staging and asking him to test them before programmatic verification
2. **Snippet micromanagement** — asking AGY for one CSS override and copying it manually. This fragments styles, ignores cascade relationships, and breaks responsive layouts
3. **Treating AGY as a text generator** — AGY has a bash terminal, Python, image tools, and a build system. Use them
4. **Ignoring structural vs. styling differences** — nav bars depend on DOM structure. Asking AGY to write CSS without letting it refactor HTML templates leads to broken layouts

## The Prompt That Works

**For implementation goals:**

```text
Goal: <bounded outcome — what should the final result look like?>
Workspace: <absolute path>
References: <screenshots, URLs, reference files>
Scope: <files/modules allowed>

Write your implementation plan FIRST. Show exactly which files you'll edit
and what changes you'll make. Then execute the plan, build locally, and
verify visually before reporting done.
```

**For RESEARCH-ONLY goals (critical — prevents AGY from building code instead):**

Launch from `/tmp` (NEVER from a project directory — code files trigger build instinct):

```bash
cd /tmp && agy --prompt-interactive "Read <brief-file>. Then read the files it lists. Then produce <deliverable>. This is pure design research. Do NOT write code."
```

The inline prompt must be simple: "Read A and B. Write C." — proven 3/3 successful. NEVER pass a multi-section brief as the inline prompt itself; instead, put the full brief in a file that AGY reads as step 1.

If the design brief is complex (multiple sections, many files to read), write it to a `.md` file first, then reference that file in the inline prompt. AGY reads the brief → reads listed files → writes deliverable. This two-hop pattern (inline prompt → brief file → deliverable) keeps AGY focused while still providing rich context.

```text
GOOD (3/3 success):
  cd /tmp && agy --prompt-interactive "Read docs/brief.md. Then read the files
  it lists. Then write docs/report.md. Pure research. Do NOT write code."

BAD (0/2 — hangs in Loading...):
  cd /tmp && agy --prompt-interactive "Audit typography across 3 HTML files.
  Check font sizes, families, consistency. Also audit colors across all
  screens. Check spacing, alignment, selection states, accessibility..."
```

## The Prompt That FAILS

```text
"How do I change the dropdown hover color?"
→ AGY gives you a CSS snippet → you paste it → it breaks mobile
→ You ask again → AGY gives another snippet → you paste it → it breaks desktop
→ Infinite loop of micromanaged pain
```

## Pitfalls

- ❌ Asking AGY for code snippets to paste manually — you're the bottleneck
- ❌ Pushing AGY's output to staging without local verification — broken pages reach Michael
- ❌ Micromanaging step-by-step when AGY should own the full loop
- ❌ Not providing visual references — AGY can't match what it can't see
- ❌ Showing Michael unverified work — his first impression is a broken page
- ❌ **Asking AGY to do pure research without a NO-CODE constraint** — AGY defaults to building code when given a workspace. If the goal is research/documentation (not implementation), add: "DO NOT write or modify any code. Save ONLY the specified .md files. You are a RESEARCHER." Without this, AGY will skip research and build code instead. See `antigravity-cli-orchestration` skill, reference `references/agy-research-vs-build.md`.
- ❌ **Giving AGY a research goal without a "NO CODE" constraint** — AGY defaults to BUILDING. When a workspace has code files and the goal could be interpreted as either "research and write a report" or "read the code and build something," AGY will always choose build. Both AGY sessions in the Darius Star bootstrap (June 2026) were given explicit research goals with report-file deliverables — both ignored the research brief, read `index.html`, and built sprite processing scripts instead. The fix: add `CONSTRAINT: Do NOT write code. Do not modify any existing files. This is a PURE RESEARCH task. Only write the report files listed below.` at the top of any research prompt. See `references/agy-research-vs-build.md` in the `antigravity-cli-orchestration` skill for the full case study.
- ❌ **Running AGY with `--add-dir` for research/design tasks** — triggers build instinct. Run from `/tmp` instead.
- ❌ **Long multi-section briefs (>3KB)** — cause infinite "Loading..." loop. Use one focused prompt with one deliverable.
- ✅ **Clean prompt pattern**: `cd /tmp && agy --prompt-interactive "Short focused prompt. One output file."` See `antigravity-cli-orchestration` reference `references/agy-clean-prompt-and-image-generation.md`
- ❌ **AGY loads prior session context and gets distracted** — When launching AGY in a project workspace, it loads previous conversation history or prior session artifacts (e.g., sprite manifest work from a prior session contaminates a UI audit session). AGY may spend cycles on irrelevant prior work before even reading the current prompt. Mitigation: always use `--prompt-interactive` for fresh sessions, and when resuming with `--conversation=`, send a direct push prompt telling AGY to skip re-reading and produce output immediately. If AGY still gets distracted, abandon and use `delegate_task` instead. See `antigravity-cli-orchestration → references/agy-hang-recovery-and-parallel-backup.md`.
- ❌ **Launching AGY from a project directory for research tasks** — Even without `--add-dir`, AGY picks up `.git` history, prior session artifacts, and code files that trigger its builder instinct. RESEARCH tasks MUST be launched from `/tmp` (a neutral clean-room directory). See `antigravity-cli-orchestration → references/agy-clean-launch-pattern.md` for the 3-attempt proving ground that discovered this pattern.
- ❌ **File-based prompts > 1KB with multiple sections** — AGY reads them but the prompt overhead triggers analysis paralysis. Large prompts with "audit X, Y, Z, also check A, B, C" reliably cause AGY to hang in "Loading..." instead of producing output. Use inline, single-sentence prompts: "Read A and B. Write C." — proven 3/3 successful vs 0/2 for large brief files.
- ❌ **Giving up on AGY after one failure** — Michael's directive: "Treat AGY as your MVP. Set AGY up for success, learn from your communication mistakes, and keep trying until you have mastered communication with AGY and produce consistent results EVERY time." The 3-attempt pattern (analyze failure → adjust → retry) is the expected workflow, not a sign AGY is broken.
- ✅ **Two-hop pattern proven again (June 11, 2026)**: The pattern "Write brief to file → launch AGY from `/tmp` → inline prompt 'Read brief. Read files it lists. Write output. Pure research. Do NOT write code.'" produced a perfect 260-line foundational audit on first attempt. AGY read the brief, read index.html + all docs, mapped the dependency graph, identified missing modules with exact signatures, produced a Mermaid diagram, directory scaffolding commands, and a prioritized build plan — all without touching any code files. This is now 4/4 successful: the two-hop pattern is battle-tested.
- ⚠️ **AGY will auto-execute scaffolding**: When the brief maps out directory scaffolding needed, AGY may execute it (create directories, move files, update paths) as part of its research output. This is actually useful — the research session produces both the audit AND the scaffolding. Just be aware: AGY's "pure research" constraint prevents CODE creation but may not prevent filesystem organization tasks like `mkdir` and `git mv`.
- ⚠️ **AGY image generation: model CAN generate, PIL processing may hang** — AGY (Gemini 3.5 Flash) DOES generate images via `GenerateImage()`. Proven June 11, 2026: produced a 1024×1024 console sprite sheet. The image lands at `~/.gemini/antigravity-cli/brain/<session>/` BEFORE AGY's PIL post-processing step. If AGY hangs in "Running..." during PIL processing, the image is ALREADY SAVED — just copy it out. AGY can also procedurally generate pixel art via Python/PIL scripts (`generate_console.py`, `generate_lyra_portrait.py`). Both modes work. Prompt pattern: specify exact dimensions + hex colors + style direction + "16-bit pixel art, crisp edges, no anti-aliasing" + "PNG with transparency." See `antigravity-cli-orchestration → references/agy-image-generation-sprites.md`.
- ❌ **Not injecting direction mid-session when the user course-corrects** — When Michael pivots the design direction while AGY is mid-task (e.g., "not a clean dashboard — make it a jury-rigged scrapper console"), use `process(action='submit', session_id=..., data=...)` to inject the new direction into the running AGY session. AGY can revise its output in-place without restarting. See `references/agy-mid-session-direction-injection.md`.
- ❌ **Not injecting direction mid-session when the user course-corrects** — When Michael pivots the design direction while AGY is mid-task (e.g., "not a clean dashboard — make it a jury-rigged scrapper console"), use `process(action='submit', session_id=..., data=...)` to inject the new direction into the running AGY session. AGY can revise its output in-place without restarting. See `references/agy-mid-session-direction-injection.md`.

## Heavy Data Audit Pattern — Pre-Compute Then Analyze

When the task involves scanning large repos (1,000+ files) with grep patterns, AGY's `--print-timeout` limits cause timeouts. AGY collects all data but never has time to compile the report.

**The pattern:** Pre-compute raw data via direct grep, then feed to AGY for compilation-only.

```bash
# Research delegation (write brief → AGY reads → AGY writes output):
cd /tmp && agy --print "Read /tmp/audit-brief.md and /tmp/raw-data.md. \
 Compile findings into /tmp/audit-report.md. \
 Pure compilation. Do NOT run scans." --add-dir /tmp --model "Claude Sonnet 4.6 (Thinking)"
```

**Rules:**
1. Pre-compute ALL heavy data collection (grep, find, git ls-files) yourself via terminal
2. Write raw data to a file in /tmp/
3. Delegate to AGY for **analysis and compilation only**
4. Include "Do NOT run scans" constraint — prevents AGY from re-doing the heavy work
5. AGY session becomes fast (266-line report in one shot vs 2 timeouts)

Proven Jun 16, 2026: 32-pattern credential sweep across 5 repos timed out twice as full AGY session. Pre-computed raw data (42 lines of grep output) + AGY compilation = success in one session.

## How This Applies to Each Agent

- **Fred (orchestrator):** When dispatching to AGY, provide the goal + workspace + references. Do NOT provide step-by-step instructions.
- **Kai (Active Oahu):** When asking AGY for content/design work, describe the desired outcome and let AGY determine the approach.
- **Autobot (automation):** When triggering AGY via cron or signals, include full context — goal, workspace path, reference files — in the trigger payload.

## AGY Review → Fred Execute

When the user says "Have AGY audit/review and give it back to Fred," use the READ-ONLY constraint pattern. AGY analyzes without modifying files, produces a structured audit, Fred reads it and executes fixes. See `references/agy-review-then-fred-execute.md` for the full pattern with prompt templates and worked examples.

## Brief → Augment → Execute Pipeline (Proven Jun 16, 2026)

For multi-phase audit or architecture workflows where the methodology itself needs refinement before execution:

**Phase 1 — Write Draft Briefs:** One brief per execution session. Each specifies: files to read, scan vectors, output format, constraints (Pure audit. Do NOT modify files).

**Phase 2 — AGY-pro Augmentation:** Feed all briefs to AGY-pro (Gemini 3.1 Pro High) for improvement. AGY-pro identifies missing scan patterns, improves output format, adds edge cases. Proven: 3 briefs expanded with 15-22 additional scan vectors each.

**Phase 3 — Execute:** One AGY session per augmented brief. Massive scans → Gemini 3.1 Pro (High). Precision analysis → Claude Sonnet 4.6 (Thinking). Architecture → Gemini 3.1 Pro (High).

```bash
# Phase 2: AGY-pro augments all briefs
cd /tmp && agy --print "Read briefs. Improve each. Write augmented." --add-dir /tmp --model "Gemini 3.1 Pro (High)"

# Phase 3: Execute each augmented brief
agy --print "Read /tmp/brief-augmented.md. Execute. Write output." --add-dir /tmp --model "Claude Sonnet 4.6 (Thinking)"
```

**Pre-compute fallback when AGY times out on heavy scans:** If AGY exceeds `--print-timeout` running grep on 1,000+ files, pre-compute raw data yourself then feed to AGY for compilation-only:
```bash
grep -rn 'pattern' $REPOS > /tmp/raw-data.md
agy --print "Read brief and raw-data.md. Compile report. Do NOT run scans." --add-dir /tmp --model "Claude Sonnet 4.6 (Thinking)"
```
Proven Jun 16, 2026: 32-pattern credential scan across 5 repos timed out twice; pre-computed raw data (42 lines) + AGY compilation = success in one shot.

## Multi-Pass Quality Pipeline

For complex integration work, use the **AGY Review → Build → Audit** three-pass pipeline:
1. AGY reviews code (read-only, finds dead modules / bugs)
2. Fred + AGY implement fixes
3. AGY audits Fred's work (read-only, catches data-shape bugs)

This is proven: the Darius Star session caught a critical NG+ key-handler bug (wrong eligibility check + wrong arg type) that would have shipped broken without Pass 3. See `references/agy-review-build-audit-pipeline.md` for the full pattern with delegation prompts and pitfalls.
