# Agentic Swarm Ops → Prismatic Engine AGY Workflow Deep Dive

**Status:** Research evidence supporting the canonical contract
**Owner:** Prismatic Engine orchestration maintainers
**Last verified:** 2026-07-25
**Decision target:** `docs/contracts/canonical-agy-cli-workflow.md`

## Scope and method

This review inspected the live `agentic-swarm-ops` checkout and the live orchestrator/runtime files it names. Dated prose was treated as evidence; current executable behavior took precedence when documentation conflicted.

Primary sources:

| Source | Relevant behavior |
|---|---|
| `$HOME/work/agentic-swarm-ops/docs/agy-delegation-best-practices.md:87-123` | Prompt sizing and task chunking |
| same, `:210-225` | Plan-first and six-step Book End protocol |
| same, `:253-317` | `--add-dir`, permissions, canonical invocation |
| same, `:330-349` | Never/always dispatch rules |
| `$HOME/work/agentic-swarm-ops/docs/agy-comprehensive-reference.md:28-37` | CLI flag contract |
| same, `:67-90` | Headless print protocol and `/goal` |
| same, `:450-520` | Deadlocks, background/PTY failures, cleanup, output separation |
| `$HOME/work/agentic-swarm-ops/docs/agy-vs-jules-routing-guide.md:43-54` | Capability/risk routing |
| `$HOME/work/agentic-swarm-ops/ops/agy_watchdog.py:47-63` | 15-minute warning, 30-minute kill, three retries |
| `$HOME/.hermes/profiles/orchestrator/scripts/agent_output_validator.py:13-21,70-95` | Zero-trust transcript/artifact/error validation |
| `$HOME/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py:850-932` | Task file, model resolution, current wrapper invocation |
| same, `:934-959` | Detached wrapper with durable downstream anchor |
| `$HOME/.local/bin/launch_agy_with_artifact.py:1-30` | tmux/artifact/checkpoint design |
| same, `:196-229` | unique tmux launch and bounded wait |
| same, `:232-263` | brain-result selection/copy |
| same, `:266-319` | wrapper lifecycle |

## What the swarm learned correctly

### 1. AGY needs a durable terminal anchor

The current dispatcher routes AGY through `launch_agy_with_artifact.py`, whose current design launches a uniquely named tmux session. This prevents dispatcher/cron lifecycle exit from directly owning AGY's terminal. The lesson is the tmux anchor, not the workstation wrapper itself.

### 2. Goal framing changes reliability

Scheduled and reference paths use `/goal` for unattended work. Goal text should be short and outcome-oriented. Detailed context belongs in a file AGY reads first. The observed reliable range is 500–1,200 prompt characters; broad 3,000-character prompts commonly time out.

### 3. Plan-first and book-end artifacts reduce false completion

Code-edit prompts require an implementation plan before changes. The Book End protocol preserves plan, execution, summary, walkthrough, handoff, and termination. PE retains these as local artifacts while removing agent-authored Linear transitions.

### 4. stdout is not the diagnostic log

AGY's final response is stdout. stderr carries startup/auth/tool noise. `--log-file` is an internal server diagnostic stream. Combining these makes result parsing and failure attribution unreliable.

### 5. Completion signals are not proof

The output validator scans timeout/auth/crash/truncation markers and verifies referenced artifacts. PE strengthens this: neither `DONE`, `RESULT.md`, a commit, nor a tmux exit authorizes acceptance. Exact-artifact independent verification remains mandatory.

### 6. Historical timeout policy is evidence, not PE policy

The swarm previously moved from five-minute assumptions to 15-minute warnings and 30-minute kills because real AGY build/test loops routinely exceeded five minutes. That history demonstrates why elapsed-time termination is unsafe; it is not inherited by PE. Canonical PE AGY runs have no wall-clock deadline. CPU/I/O/process/log/artifact signals are retained as dashboard classifications only, and cancellation is explicit.

## Conflicts and unsafe inheritance

### Direct print versus tmux

Older reference prose says direct foreground `--print` without PTY is the only reliable pattern and warns that `terminal(background=true)` or `pty=true` can produce SIGTERM. Newer live dispatcher code says tmux is the only reliable durable transport. These are not silently interchangeable:

- Direct foreground is appropriate for a human/Hermes-owned one-off command.
- Event-driven unattended work outlives the dispatcher and needs a durable terminal anchor.
- PE therefore standardizes unattended workflow on a controlled tmux session and tests that exact transport.

### Generic launcher security gaps

The live wrapper is evidence, not a package-ready component:

- mode `0777` permits mutation;
- absolute workstation paths prevent portable installation;
- the invoked binary can auto-update in place;
- `pgrep -f` is not an exact process-identity contract;
- newest-result-by-mtime can cross-bind parallel tasks;
- broad checkpoint daemons can make uncontracted commits;
- timeout/session exit does not always preserve the actual AGY exit code.

PE reimplements the useful behavior with explicit manifests, hash-bound inputs, owner-only receipts, exact session/pane identity, separate result records, and no generic checkpoint commits.

## Highest-priority live lifecycle defect

The live swarm dispatcher treats an eight-second launcher health check as task completion and immediately advances the issue to the next agent label. The launcher has only acknowledged that tmux started; AGY, artifact collection, and validation are still pending. This contradicts comments claiming a later tick waits for the result. It also removes the task from the validator's AGY-label query before output exists.

PE therefore makes this a hard invariant: `RUNNING` and producer `COMPLETED` are distinct, producer completion retains `verification_status=pending`, and no downstream state mutation may occur before exact artifact collection and independent validation.

The live timeout layers also conflict: dispatcher metadata says 600 seconds, AGY receives 24 hours, the launcher kills at 1,800 seconds, and stale-process cleanup actually kills generic `agy-bin` processes after 300 seconds despite a 30-minute docstring. Stall detection watches Linear comments even though the AGY prompt forbids Linear comments. PE does not inherit any of those signals as run truth.

## Attempt-1 incident evidence

The GRO-4210 attempt used raw detached `Popen` rather than the canonical tmux path. AGY 1.1.6 started an updater, the path was replaced with 1.1.7, and the old process received SIGTERM near five minutes while a terminal command escaped and continued. A controlled AGY 1.1.7 probe with `--print-timeout=10m` and a 330-second workload completed in 336 seconds with no descendants. This falsifies a universal five-minute ceiling and supports both immutable runtime identity and canonical transport.

The swarm stall-recovery script was executed directly: 20 assertions passed and 4 failed (exit 1). The failures concern retry comments, immediate relaunch, escalation comments, and Fred transition expectations; pytest collection is itself invalid because the script exits at import time. This is recorded as policy/test drift, not canonical green.

Security-sensitive findings retained in PE's rejection list include untrusted Linear text flowing into an unrestricted prompt, live omission of `--sandbox`, broad `pgrep`/global process kills, predictable `/tmp` paths, global-latest transcript attribution, mtime-selected result files, and `git add -A` checkpoint commits.

## Port decisions

| Swarm behavior | PE decision |
|---|---|
| tmux durable anchor | Port and test |
| `/goal` prefix | Mandatory |
| 500–1,200-char goal | Mandatory maximum 1,200 |
| detailed task file | Port with SHA binding |
| plan-first | Mandatory artifact |
| Book End | Port as local result contract |
| `--dangerously-skip-permissions` | Required for admitted unattended work |
| AGY-required print duration | Maximum parseable whole-second duration as protocol bridge; PE runtime deadline is `null` |
| watchdog warning/kill/retry | Port CPU/I/O/log/artifact signals into durable dashboard activity; reject automatic time/inactivity kills |
| result copy from AGY brain by mtime | Reject |
| generic WIP auto-commits | Reject in exact-scope lane |
| Linear comments/relabel by AGY | Reject; governed adapter only |
| mutable `$HOME/.local/bin/agy-bin` | Reject; reviewed digest required |
| producer output validator | Port concepts; bind to exact artifact and independent receipt |

## Acceptance matrix

1. Contract JSON exposes tmux, `/goal`, timeout, result marker, requirements, and forbidden paths.
2. Rendered manifest binds task and binary SHA-256.
3. Invalid digest/path/model/timeout/identifier fails closed.
4. Launch requires explicit `--execute` in addition to upstream admission.
5. Fake AGY receives canonical argv inside tmux.
6. Plan and result are created before terminal receipt.
7. stdout/stderr/diagnostics remain separate.
8. Launch receipt binds tmux session, pane PID/start ticks, task digest, and manifest digest.
9. Normal completion or explicit cancellation adopts daemonizing descendants through a child subreaper, terminates and reaps the exact observed PID/start-tick tree, then tears down only the exact session after full-tree cleanup proof.
10. A live AGY proof and GRO-4210 retry remain separately authorized operations.

## Boundary

This report records source findings and the PE port decision. It does not claim that the historical swarm wrapper is safe, that every old AGY document is current, that a live AGY task was launched, or that the PE change is merged/deployed.
