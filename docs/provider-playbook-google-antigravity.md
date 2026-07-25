# Provider Playbook: Google Antigravity (AGY)

**Status:** Canonical provider projection
**Owner:** Prismatic Engine orchestration maintainers
**Last verified:** 2026-07-25
**Normative workflow:** [`contracts/canonical-agy-cli-workflow.md`](contracts/canonical-agy-cli-workflow.md)

AGY is a provider/runtime behind Prismatic's canonical execution contract. This playbook cannot weaken task admission, tmux transport, executable identity, result durability, independent verification, or operator-action gates.

## Front-door commands

```bash
prismatic agy contract
prismatic agy render <hash-bound specification arguments>
prismatic agy launch <hash-bound specification arguments> \
  --admission-receipt <PRISMATIC_AGY_ADMISSION_V1.json> \
  --runtime-dir <dir> --execute
prismatic agy wait --receipt <launch-receipt.json>
```

Use `contract` before building integrations. Use `render` to bind the task, binary, model, paths, prompt, and timeout without launching. `launch --execute` is a local execution gate, not a substitute for event admission or human authorization.

## Canonical unattended mode

| Control | Required value |
|---|---|
| Transport | unique tmux durable anchor |
| Prompt | starts `/goal `; maximum 1,200 characters |
| Context | detailed frozen task file, not an oversized inline prompt |
| Mode | `--print` |
| Permissions | `--dangerously-skip-permissions` only after admission |
| Runtime duration | no PE wall-clock cap; AGY maximum duration is passed only as a protocol bridge |
| Model | live canonical ID such as `gemini-3.6-flash-high` |
| Filesystem | bounded `--add-dir`; `--sandbox` by default |
| Runtime | regular, non-symlink, non-group/world-writable, reviewed SHA-256 |
| Outputs | separate stdout, stderr, and internal `--log-file` diagnostics |
| Book end | plan → execute → summary/walkthrough → durable result → exit |
| Acceptance | independent exact-artifact verification |

## Prompt contract

The inline goal identifies the task file, workspace, plan path, result path, evidence expectations, and prohibited external actions. Detailed requirements stay in the hash-bound task file.

Every code-edit task requires an implementation plan before edits. Every result includes:

- `PRISMATIC_AGY_RESULT_V1`;
- files changed;
- exact verification commands, outcomes, and log paths;
- commit identity when applicable;
- boundaries, non-claims, and follow-ups.

AGY must not post Linear/GitHub updates, create or approve PRs, merge, deploy, restart services, or increase concurrency. Governed PE adapters own those transitions.

## Routing guidance

| Work | AGY fit | Conditions |
|---|---|---|
| bounded implementation | yes | exact workspace/scope, plan-first, tests, independent review |
| read-only research/review | yes | explicit no-code task file and artifact output |
| visual QA | yes | local screenshots/paths, bounded comparison, rendered evidence |
| broad long-form research | chunk first | keep each goal bounded and artifact-specific |
| merge/deploy/financial/public send | no direct action | explicit operator/governed adapter gate |

Prefer a different provider or verifier identity for independent review. Same-model self-review is producer evidence, not independence.

## Failure classification

| Signal | Classification | Action |
|---|---|---|
| binary/task/manifest digest drift | trust failure | fail closed before launch |
| tmux session vanishes without process result | transport failure | block and preserve logs |
| backend timeout text | provider transport failure | preserve partial artifacts; retry only through governed attempt cap |
| no filesystem progress while CPU/I/O/log progress continues | still working | retain run; dashboard shows the active signals |
| permission prompt in headless mode | invocation defect | stop and repair command contract |
| result without marker/evidence | incomplete producer output | do not accept |
| no observable CPU/I/O/log/artifact progress | activity becomes `quiet`, then `suspect` | show in dashboard; do not auto-kill; operator/governed policy investigates or cancels exact run |
| pane or any exact PID/start-tick descendant survives explicit cleanup | containment failure | keep slot occupied; block retry until repaired |

Historical swarm evidence used 15-minute warnings, 30-minute kills, and a three-retry ceiling after earlier five-minute watchdogs killed valid work. PE retains the useful activity signals but rejects wall-clock and inactivity-based automatic termination. The dashboard projects exact-run activity; cancellation is explicit.

## Binary updates

Do not run admitted work from an auto-updating mutable executable path. Update workflow:

1. acquire candidate bytes;
2. identify reported version and SHA-256;
3. review changelog and run a bounded timeout/tool smoke;
4. copy into an owner-controlled version path;
5. bind the reviewed digest in the launch specification;
6. independently verify before making it available to admitted work.

## Historical note

Older examples in this repository used direct raw `Popen`, interactive PTY, five-minute review timeouts, or broad supervisors. They are historical evidence unless they call `prismatic.agy_cli` or demonstrate conformance to the canonical contract. See [`research/agentic-swarm-ops-agy-workflow-deep-dive.md`](research/agentic-swarm-ops-agy-workflow-deep-dive.md).
