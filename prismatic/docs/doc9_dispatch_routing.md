# Dispatch Routing Fallbacks and Non-Dispatchable States

**Linear issue:** GRO-3489  
**Owner lane:** Prismatic Engine / Ned  
**Status:** canonical dispatch contract

This document defines what the Prismatic Engine dispatcher does when a Linear issue is unlabeled, mislabeled, or in a state that must not launch an agent. The core rule is simple: **no issue is silently dropped**. Every observed issue is either dispatched, parked with an explicit reason, handed to a fallback lane, or surfaced in a recovery queue.

## 1. Dispatch eligibility

An issue is dispatchable only when all of these conditions are true:

1. It is in a dispatchable Linear state: `Todo` or `Backlog`.
2. It has exactly one active worker-routing label, such as `agent:ned`, `agent:agy`, `agent:kai-content`, or `agent:jules`.
3. It is not already terminal, review-only, blocked, duplicate, canceled, or explicitly held by a human comment.
4. The routing label maps to a known lane configuration and launcher.
5. The dispatcher can acquire a dedup/lease token for the `(issue, agent)` pair.

If any gate fails, the issue must be recorded with a disposition. The dispatcher should prefer a durable Linear comment or label/state correction when it can do so safely; otherwise it should write a local audit entry for the safety-net cron to reprocess.

## 2. Fallback behavior

### 2.1 Unlabeled issue

**Input:** Open issue in `Todo` or `Backlog` with no `agent:*` label.

**Behavior:** Do not dispatch. Classify as `unrouted` and leave it visible for triage.

**Required disposition:**

- Add or preserve a triage/dispatch-needed signal when the owning workflow supports one, for example `dispatch:ready` without an `agent:*` label.
- Record the issue identifier, title, state, and reason: `missing agent label`.
- If an automatic classifier is available and confidence is high, add the correct `agent:*` label and dispatch on the next pass; otherwise do not guess.

### 2.2 Mislabeled issue: unknown agent label

**Input:** Issue has an `agent:*` label that is not present in the lane registry.

**Behavior:** Do not dispatch to a default worker. Unknown labels are configuration drift, not work.

**Required disposition:**

- Record `unknown agent label: <label>` in the dispatcher audit log.
- Keep the issue open and visible.
- If a safe owner is known from the label family, re-label to that owner; otherwise add a comment requesting lane configuration or manual re-labeling.

### 2.3 Mislabeled issue: lane mismatch

**Input:** Issue has a known agent label, but the task body or recent comments make it clear that the work belongs to another lane.

**Behavior:** Do not execute outside the current lane. Re-route cleanly rather than stripping the current label and orphaning the task.

**Required disposition:**

- Remove the wrong worker label.
- Add the receiving worker label.
- Preserve or add `dispatch:ready` when the task should be picked up.
- Move to `Todo` unless comments explicitly say to hold in `Backlog`.
- Post a concise handoff comment: from-label, to-label, state, reason, and `No work discarded; clean handoff.`

### 2.4 Multiple active worker labels

**Input:** Issue has more than one active worker label, for example `agent:ned` and `agent:agy`.

**Behavior:** Do not launch multiple agents. Pick a deterministic owner only when the issue has an explicit handoff comment or one label is terminal/review-only.

**Required disposition:**

- If one label is `agent:peer-review` and the issue is `In Review`, preserve review and remove active worker labels.
- If one label is `agent:done`, keep it only for Done-state terminal cleanup.
- Otherwise park as `ambiguous routing`, add a comment or audit entry, and require triage.

### 2.5 Fallback label configured for a scanner

Some scanner jobs include a configured `fallback_label` to keep older cron paths from returning an empty queue when environment variables are missing. That fallback is not permission to execute cross-lane work.

**Behavior:** Treat fallback-fed issues as candidates only. Before dispatch, verify that the live Linear issue still carries the current agent's real label and satisfies the lane guard.

**Required disposition:**

- If the issue only matched through fallback, skip execution and record `fallback-only match`.
- If a mixed scanner batch contains both real current-agent work and fallback-only work, execute only the real current-agent items and report the refused fallback items in the local cron output.

## 3. Non-dispatchable states

These states must not start a worker automatically:

| State | Reason | Required disposition |
|---|---|---|
| `In Progress` | A worker is already or recently was active. | Recover only if there is explicit stale/abandoned evidence; otherwise skip. |
| `In Review` | Work is awaiting review, not execution. | Remove stale active labels if needed; keep/add `agent:peer-review`; do not relaunch. |
| `Done` | Terminal successful state. | Ensure terminal labels are clean; never dispatch. |
| `Canceled` | Terminal stopped state. | Never dispatch unless a human reopens or relabels. |
| `Duplicate` | Terminal duplicate state. | Never dispatch; preserve duplicate relation/comment. |
| `Triage`, `Blocked`, `Deferred`, or equivalent hold states | Human/system hold. | Do not dispatch; surface blocker if ownership is unclear. |

`Todo` and `Backlog` are the only default dispatchable states. Even there, recent comments can override dispatch when they contain explicit hold/dequeue language such as `out-of-lane`, `dequeued`, `wrong-agent`, `lane violation`, `do not redispatch`, or `blocked until`.

## 4. No-silent-drop ledger

Every scanned issue should end a dispatcher pass with one of these dispositions:

- `DISPATCHED`: launcher started or was deduped because it is already within the lease window.
- `READY_BUT_DEDUPED`: dispatch was intentionally suppressed by a recent identical launch.
- `UNROUTED`: missing worker label.
- `UNKNOWN_LABEL`: worker label is not registered.
- `LANE_MISMATCH`: clean handoff required or completed.
- `AMBIGUOUS_ROUTING`: multiple active labels need triage.
- `NON_DISPATCHABLE_STATE`: state is not `Todo` or `Backlog`.
- `HUMAN_HOLD`: comments or labels explicitly require a human decision.
- `BLOCKED`: credential, access, physical, or dependency blocker.

The local scanner output, dispatcher audit table, or Linear comment must include the issue identifier and disposition. A pass with zero dispatches is healthy only when every observed issue has a disposition.

## 5. Operator checklist

When debugging a queue that appears to be piling up:

1. Query the live Linear issue, including state, labels, and recent comments.
2. Confirm whether the issue matched through a real worker label or scanner fallback.
3. Check the state against the non-dispatchable table above.
4. If the label is wrong, perform a clean handoff instead of removing labels only.
5. If no safe mutation is possible, leave the issue visible and write a local audit record; do not drop it from the queue silently.

## 6. Implementation notes

Dispatcher implementations should keep routing decisions as data, not scattered conditionals. A practical shape is a `RoutingDisposition` object with fields:

- `issue_id`
- `identifier`
- `state`
- `agent_labels`
- `dispatchable: bool`
- `disposition`
- `reason`
- `recommended_mutation`

That object can drive logs, metrics, Linear comments, and tests from the same source. The acceptance test for this contract should assert that unlabeled, unknown-label, multi-label, and non-dispatchable-state fixtures all produce explicit dispositions rather than disappearing from output.
