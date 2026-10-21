"""Kanban handoff adapter: Hermes kanban task -> ReviewArtifact -> verdict.

This is the "Prismatic step A" deterministic floor for kanban review
handoffs (``~/work/kanban-verify/PLAN.md``). It REUSES the
engine's existing validation layer verbatim — it does not reinvent it:

* :mod:`prismatic.review_factory.artifact` (workstream A) supplies
  ``ReviewArtifact`` (boundary validation, canonical hashing, write-once
  store). We add exactly one entry to its ``HARNESS_IDS`` boundary
  (``"hermes-kanban"``) — the documented "one new harness = one entry"
  extension point.
* :mod:`prismatic.review_factory.verdict` (verdict-v1) supplies
  ``Verdict``, ``decide()``, ``register_runner``/the deterministic-runner
  interface, and the write-once verdict store.

What is new here (and only here) is the kanban-specific glue:

1. a **read-only** projection of ``kanban.db`` (task row, latest
   completion result, attachments, comments) — never written;
2. an L0 translation of that projection into a ``ReviewArtifact``
   (``harness_id="hermes-kanban"``);
3. a **kanban deterministic runner** (the four step-A checks) that
   ``decide()`` consumes with ``judgment=None`` (the NullJudge path — L2
   judgment is step B, not this build);
4. a single, idempotent ``VERDICT`` comment posted to the task via the
   Hermes kanban CLI.

Advisory only: nothing here changes a task's status or writes to
``kanban.db``. The only kanban mutation is the one ``hermes kanban
comment`` call.

Frozen-schema notes (we respect them, not work around them):
* ``explicit_gaps`` admits only dotted nullable schema paths — free text is
  rejected by ``artifact.validate``. The "no unified diff — kanban
  deliverables are files, not patches" fact is therefore captured by
  ``diff.unified=None`` (gapped as ``diff.unified``), and the human-facing
  explanation is carried in the ``VERDICT`` comment, not in ``explicit_gaps``.
* ``DiffFile`` has no per-file sha256 slot. Deliverable files are recorded in
  ``diff.files`` by path (with measured line counts); their real sha256 is
  verified by the L1 runner's ``hashes_match`` check against disk, not stored
  in the artifact.
* The kanban L1 runner is **DB-snapshot-bound**: the four checks need the
  live task row / files, which the artifact alone does not carry. In-process
  ``replay()`` works (the runner closure persists); cross-process replay from
  the stored artifact alone is not the step-A kanban contract (an honest gap,
  distinct from the PR pipeline's replayability promise).

CLI::

    python3 -m prismatic.review_factory.adapters.kanban <task_id>

Run from the engine checkout. Unknown task id / unreadable DB -> non-zero
exit, clear stderr, no partial files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from ..artifact import (
    Diff,
    DiffFile,
    Intent,
    NoveltyContext,
    ReviewArtifact,
    validate,
)
from ..verdict import decide, register_runner
from ._artifact import AdapterError, compute_gaps, now_iso


#: The new harness id admitted at the artifact boundary for kanban handoffs.
HARNESS_ID = "hermes-kanban"

#: Deterministic-check names (the step-A floor, plan §Checks v1).
CHECK_RESULT_PRESENT = "result_present"
CHECK_DELIVERABLES_EXIST = "deliverables_exist"
CHECK_HASHES_MATCH = "hashes_match"
CHECK_CRITERIA_PRESENT = "criteria_present"
_CHECK_NAMES = (
    CHECK_RESULT_PRESENT,
    CHECK_DELIVERABLES_EXIST,
    CHECK_HASHES_MATCH,
    CHECK_CRITERIA_PRESENT,
)


#: Canonical, overridable locations (tests point these at temp fixtures).
def _kanban_db_path() -> str:
    return os.environ.get("KANBAN_DB_PATH", str(Path.home() / ".hermes" / "kanban.db"))


def _attachments_root() -> str:
    return os.environ.get(
        "KANBAN_ATTACHMENTS_ROOT",
        str(Path.home() / ".hermes" / "kanban" / "attachments"),
    )


def _verdict_store_path() -> str:
    audit = os.environ.get(
        "PRISMATIC_AUDIT_DIR", os.path.expanduser("~/.prismatic/audit")
    )
    return os.path.join(audit, "review-verdicts.jsonl")


# ─────────────────────────────────────────────────────────────────────
# Read-only kanban projection (never writes)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KanbanTask:
    """Immutable read-only snapshot of one kanban task + its completion."""

    task_id: str
    title: str
    body: str
    status: str
    completed_at: int | None
    #: Full completion handoff text (the reviewer-facing "result").
    result_text: str | None
    #: (filename, stored_path, size) for every attachment on the task.
    attachments: tuple[tuple[str, str, int], ...] = ()
    #: All existing comment bodies (used for the idempotency marker).
    comments: tuple[str, ...] = ()


def load_task(task_id: str) -> KanbanTask:
    """Project one task from ``kanban.db`` in READ-ONLY mode.

    Raises ``AdapterError`` on a missing task or an unreadable DB.
    """
    db = _kanban_db_path()
    if not os.path.exists(db):
        raise AdapterError(f"kanban DB not found: {db}")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # unreadable DB / not a sqlite file
        raise AdapterError(f"cannot open kanban DB read-only: {exc}") from exc

    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, title, body, status, completed_at FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise AdapterError(f"unknown task id: {task_id!r}")
        tid, title, body, status, completed_at = row

        # Full completion text: prefer tasks.result; fall back to the latest
        # run summary, then the latest 'completed' event payload summary.
        result_text = _completion_text(cur, tid)

        cur.execute(
            "SELECT filename, stored_path, size FROM task_attachments "
            "WHERE task_id = ? ORDER BY id",
            (tid,),
        )
        attachments = tuple((f, sp, sz or 0) for f, sp, sz in cur.fetchall())

        cur.execute(
            "SELECT body FROM task_comments WHERE task_id = ? ORDER BY id", (tid,)
        )
        comments = tuple(b for (b,) in cur.fetchall())
    finally:
        con.close()

    return KanbanTask(
        task_id=tid,
        title=title or "",
        body=body or "",
        status=status or "",
        completed_at=completed_at,
        result_text=result_text,
        attachments=attachments,
        comments=comments,
    )


def _completion_text(cur: Any, tid: str) -> str | None:
    """Best available full completion text for a task (read-only)."""
    cur.execute("SELECT result FROM tasks WHERE id = ?", (tid,))
    r = cur.fetchone()
    if r and r[0] and str(r[0]).strip():
        return str(r[0])
    cur.execute(
        "SELECT summary FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (tid,),
    )
    run = cur.fetchone()
    if run and run[0] and str(run[0]).strip():
        return str(run[0])
    cur.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'completed' "
        "ORDER BY id DESC LIMIT 1",
        (tid,),
    )
    ev = cur.fetchone()
    if ev and ev[0]:
        try:
            payload = json.loads(ev[0])
        except (ValueError, TypeError):
            payload = {}
        s = payload.get("summary") if isinstance(payload, dict) else None
        if s and str(s).strip():
            return str(s)
    return None


# ─────────────────────────────────────────────────────────────────────
# Text extraction helpers (pure)
# ─────────────────────────────────────────────────────────────────────

_AC_HEADING = re.compile(r"^#{0,6}\s*ACCEPTANCE\s+CRITERIA\s*[:\-]?\s*$", re.I | re.M)
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")


def parse_acceptance_goals(body: str) -> list[str]:
    """Bullets under the first ACCEPTANCE CRITERIA heading (else []).

    A kanban body with no AC section yields [] — that is data, and the
    ``criteria_present`` check is what flags it, not a parsing error.
    """
    if not body:
        return []
    m = _AC_HEADING.search(body)
    if m is None:
        return []
    tail = body[m.end() :]
    goals: list[str] = []
    for line in tail.splitlines():
        if not line.strip():
            continue
        # A new top-level heading ends the AC section.
        if re.match(r"^#{1,6}\s+\S", line):
            break
        if re.match(r"^\S", line) and not line.lstrip().startswith(("-", "*", "+")):
            # Non-bullet prose line after the heading ends the list.
            break
        bm = _BULLET.match(line)
        if bm:
            text = bm.group(1).strip()
            if text:
                goals.append(text)
    return goals


#: Absolute path: leading '/', at least two segments, path-ish chars only.
_ABS_PATH = re.compile(r"(?<![\w./-])(?:/[\w.@+-]+(?:/[\w.@+-]+)+)")


def extract_absolute_paths(text: str | None) -> list[str]:
    """Absolute file paths named in ``text`` (order-preserving, deduped).

    Conservative by design (plan §Risks): a path must be absolute with at
    least two segments so prose tokens do not read as deliverables. Misses
    degrade to gaps, never fabrications.
    """
    if not text:
        return []
    seen: dict[str, None] = {}
    for m in _ABS_PATH.finditer(text):
        seen.setdefault(m.group(0), None)
    return list(seen)


#: A stated sha256: a 64-hex token, optionally near the word "sha256".
_SHA256_TOKEN = re.compile(r"\b[0-9a-f]{64}\b")


@dataclass(frozen=True)
class StatedHash:
    digest: str
    path: str | None


def extract_stated_hashes(text: str | None) -> list[StatedHash]:
    """Stated sha256 values in ``text`` with a resolvable path, if any.

    A hash "resolves to a manifest file" when an absolute path appears on
    the same line as the hash (the common "… sha256 <hex> … <path>" or
    "<path> … <hex>" handoff shape). A hash with no resolvable path is
    returned with ``path=None`` — the ``hashes_match`` check then skips it
    (it only recomputes hashes that resolve to a file), so an unverifiable
    hash degrades to a gap, never a false REJECT.
    """
    if not text:
        return []
    out: list[StatedHash] = []
    seen: set[str] = set()
    for line in text.splitlines():
        digests = _SHA256_TOKEN.findall(line)
        if not digests:
            continue
        paths = extract_absolute_paths(line)
        for d in digests:
            if d in seen:
                continue
            seen.add(d)
            out.append(StatedHash(digest=d, path=paths[0] if paths else None))
    return out


def _line_count(path: str) -> int:
    """Real line count of a readable regular file, else 0 (never guessed)."""
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except (OSError, IsADirectoryError):
        return 0


def _sha256_file(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, IsADirectoryError):
        return None


# ─────────────────────────────────────────────────────────────────────
# L0 translation: KanbanTask -> ReviewArtifact (reuses workstream A)
# ─────────────────────────────────────────────────────────────────────


def to_artifact(task: KanbanTask) -> ReviewArtifact:
    """Build the kanban ``ReviewArtifact`` through the existing module.

    * ``harness_run_id`` = task id (deterministic).
    * ``submitted_at``   = completion time (deterministic — using ``now``
      would break the artifact_id idempotency the comment marker relies on).
    * ``intent.brief``   = task body; ``intent.goals`` = AC bullets.
    * ``diff.files``     = every attachment + every absolute path named in
      the result text, each with its measured line count (``added``).
    * ``checks``         = [] (the L1 runner supplies the step-A checks).
    """
    submitted_at = _epoch_to_iso(task.completed_at) if task.completed_at else now_iso()

    goals = parse_acceptance_goals(task.body)

    # Deliverable paths: attachments (always) + result-named absolute paths.
    paths: list[str] = [sp for _, sp, _ in task.attachments]
    for p in extract_absolute_paths(task.result_text):
        if p not in paths:
            paths.append(p)

    files = [
        DiffFile(
            path=p,
            change_type="added",
            lines_added=_line_count(p) if _is_file(p) else 0,
            lines_removed=0,
        )
        for p in paths
    ]

    intent = Intent(plan_ref=None, brief=(task.body or None), goals=goals)
    diff = Diff(base_tree=None, head_tree=None, unified=None, files=files)
    novelty = NoveltyContext(first_seen_paths=list(paths))

    artifact = ReviewArtifact.create(
        harness_id=HARNESS_ID,
        harness_run_id=task.task_id,
        submitted_at=submitted_at,
        intent=intent,
        diff=diff,
        checks=[],
        prior_receipts=[],
        novelty_context=novelty,
        explicit_gaps=compute_gaps(intent, diff, []),
    )
    validate(artifact.to_dict())  # belt-and-braces on the boundary
    return artifact


def _is_file(path: str) -> bool:
    try:
        return os.path.isfile(path)
    except OSError:
        return False


def _epoch_to_iso(epoch: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# L1 deterministic runner: the four step-A checks
# ─────────────────────────────────────────────────────────────────────


@dataclass
class CheckOutcome:
    name: str
    passed: bool
    detail: str


def run_checks(task: KanbanTask) -> list[CheckOutcome]:
    """Run the four deterministic step-A checks (no LLM, no judgment)."""
    results: list[CheckOutcome] = []

    # (a) result_present — completion result/summary exists and is non-empty.
    has_result = bool(task.result_text and task.result_text.strip())
    results.append(
        CheckOutcome(
            CHECK_RESULT_PRESENT,
            has_result,
            "completion result present"
            if has_result
            else "no completion result/summary text found",
        )
    )

    # (b) deliverables_exist — every attachment on disk + every absolute path
    #     named in the result text exists.
    missing: list[str] = []
    for _name, sp, _sz in task.attachments:
        if not _is_file(sp):
            missing.append(sp)
    for p in extract_absolute_paths(task.result_text):
        if not _is_file(p):
            missing.append(p)
    results.append(
        CheckOutcome(
            CHECK_DELIVERABLES_EXIST,
            not missing,
            "all deliverables present"
            if not missing
            else f"missing deliverable(s): {', '.join(missing)}",
        )
    )

    # (c) hashes_match — every stated sha256 that resolves to a file is
    #     recomputed and compared. Any mismatch is a hard fail (-> REJECT).
    hash_fail = False
    hash_detail = "no stated sha256 to verify"
    stated = extract_stated_hashes(task.result_text)
    verified = 0
    for sh in stated:
        if sh.path is None or not _is_file(sh.path):
            # Unverifiable (no resolvable file) — a gap, not a failure.
            continue
        actual = _sha256_file(sh.path)
        if actual is None:
            continue
        verified += 1
        if actual != sh.digest:
            hash_fail = True
            hash_detail = f"sha256 mismatch for {sh.path} (stated {sh.digest[:12]}…)"
    if verified and not hash_fail:
        hash_detail = f"{verified} stated sha256 value(s) verified against disk"
    results.append(CheckOutcome(CHECK_HASHES_MATCH, not hash_fail, hash_detail))

    # (d) criteria_present — body has an ACCEPTANCE CRITERIA section with >=1
    #     bullet.
    ac_goals = parse_acceptance_goals(task.body)
    results.append(
        CheckOutcome(
            CHECK_CRITERIA_PRESENT,
            len(ac_goals) >= 1,
            f"{len(ac_goals)} acceptance bullet(s)"
            if ac_goals
            else "no ACCEPTANCE CRITERIA section with >=1 bullet",
        )
    )

    return results


def deterministic_outcome(checks: Sequence[CheckOutcome]) -> dict[str, Any]:
    """Map the four check outcomes to the L1 verdict.

    * any ``hashes_match`` failure -> REJECT (a handoff claiming a hash its
      file disproves must never reach CLEAN);
    * else any failed check -> REPAIR;
    * else CLEAN.
    ``receipt_ids`` are the check names (stable, honest identifiers).
    """
    by_name = {c.name: c for c in checks}
    if (
        not by_name.get(CHECK_HASHES_MATCH, None)
        or not by_name[CHECK_HASHES_MATCH].passed
    ):
        verdict = "REJECT"
    elif any(not c.passed for c in checks):
        verdict = "REPAIR"
    else:
        verdict = "CLEAN"
    return {"verdict": verdict, "receipt_ids": list(_CHECK_NAMES)}


#: The verdict-module deterministic-runner interface: artifact -> outcome.
KanbanRunner = Callable[[ReviewArtifact], dict[str, Any]]


def make_kanban_runner(task: KanbanTask) -> KanbanRunner:
    """A runner closure over the task snapshot (DB-snapshot-bound).

    Kept for callers that want a snapshot-bound runner (e.g. tests, or
    :func:`decide` with an explicit ``deterministic``). The process-global
    registered for :func:`replay` is :data:`_process_runner` instead, which
    re-derives from the stored artifact rather than a captured snapshot.
    """

    def _runner(_artifact: ReviewArtifact) -> dict[str, Any]:
        return deterministic_outcome(run_checks(task))

    return _runner


def _process_runner(artifact: ReviewArtifact) -> dict[str, Any]:
    """Task-agnostic kanban runner for the process-global ``replay`` fallback.

    Rebuilds the task from the artifact's ``harness_run_id`` (= task id) and
    re-derives the deterministic outcome from the live DB snapshot, so a
    :func:`replay` of a stored kanban artifact reproduces the verdict exactly
    as :func:`build_and_store` computed it.
    """
    task = load_task(artifact.harness_run_id or "")
    return deterministic_outcome(run_checks(task))


# ─────────────────────────────────────────────────────────────────────
# Verdict composition + persistence (reuses verdict-v1 decide())
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KanbanVerdict:
    artifact: ReviewArtifact
    verdict: Any  # verdict.Verdict
    checks: tuple[CheckOutcome, ...]
    store_path: str


def build_and_store(task: KanbanTask) -> KanbanVerdict:
    """Compose the verdict via ``decide()`` (NullJudge path) and persist it.

    ``decide()`` stores the artifact (write-once) and the verdict
    (write-once) in the engine's audit store. Judgment is ``None`` — step A
    is the deterministic floor only.
    """
    artifact = to_artifact(task)
    checks = run_checks(task)
    det = deterministic_outcome(checks)

    # ``decide()`` receives the L1 outcome explicitly, so the default
    # (checks-exit-code) floor is not what decides. The kanban runner is NOT
    # registered here: ``register_runner`` mutates a process-global in
    # verdict.py, and the library path must stay side-effect-free or the
    # registration would leak into other suites/tests in the same process
    # (a leaked kanban runner would be picked up by ``replay()``'s
    # global-fallback and break byte-identity). One-time wiring happens in
    # :func:`main` (CLI entry = workstream C wiring point).
    verdict = decide(
        artifact,
        deterministic=det,
        judgment=None,
        judgment_skipped="step A: deterministic floor only (NullJudge; L2 is step B)",
    )
    # decide() already persisted artifact + verdict (write-once, idempotent).
    return KanbanVerdict(
        artifact=artifact,
        verdict=verdict,
        checks=tuple(checks),
        store_path=_verdict_store_path(),
    )


def format_verdict_line(kv: KanbanVerdict) -> str:
    """The single-line VERDICT receipt (printed + posted)."""
    passed = sum(1 for c in kv.checks if c.passed)
    total = len(kv.checks)
    final = kv.verdict.final
    aid8 = kv.artifact.artifact_id[:8]
    return (
        f"VERDICT {final} — artifact {aid8} — "
        f"checks: {passed}/{total} pass — receipt: {kv.store_path}"
    )


# ─────────────────────────────────────────────────────────────────────
# Idempotent comment posting (the only kanban mutation)
# ─────────────────────────────────────────────────────────────────────


def _marker(kv: KanbanVerdict) -> str:
    """Idempotency marker: the exact leading segment of the posted line.

    Must be a guaranteed substring of :func:`format_verdict_line` output, or
    re-runs would never detect their own comment and would post duplicates.
    """
    return f"VERDICT {kv.verdict.final} — artifact {kv.artifact.artifact_id[:8]}"


def already_posted(task: KanbanTask, kv: KanbanVerdict) -> bool:
    """True if a comment already carries this task's VERDICT marker."""
    marker = _marker(kv)
    return any(marker in c for c in task.comments)


def post_comment(task_id: str, line: str) -> None:
    """Post the VERDICT line as ONE comment via the kanban CLI.

    This is the single, sanctioned kanban mutation. Uses a lazy ``subprocess``
    import so the module's translation/check path stays free of side effects
    at import time.
    """
    import subprocess  # lazy: the only side-effecting import in this module

    env = dict(os.environ)
    env["HERMES_HOME"] = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
    subprocess.run(
        ["hermes", "kanban", "comment", task_id, line],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


# ─────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────


def run(task_id: str, *, post: bool = True) -> int:
    """End-to-end: load -> build -> decide -> print (+post). Returns exit code."""
    try:
        task = load_task(task_id)
    except AdapterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    kv = build_and_store(task)
    line = format_verdict_line(kv)
    print(line)

    if post:
        if already_posted(task, kv):
            print(f"(idempotent: VERDICT marker already on {task_id}; not re-posting)")
        else:
            try:
                post_comment(task_id, line)
            except Exception as exc:  # noqa: BLE001 — report, don't crash the run
                print(f"warning: failed to post comment: {exc}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or not argv[0]:
        print(
            "usage: python3 -m prismatic.review_factory.adapters.kanban <task_id>",
            file=sys.stderr,
        )
        return 2
    # One-time wiring point (workstream C): register the kanban deterministic
    # runner for in-process ``replay()``. Doing it here — not at import and
    # not in ``build_and_store`` — keeps the library path side-effect-free.
    # The runner is task-agnostic (it re-derives the outcome from the stored
    # artifact via the deterministic check floor), so a single closure over
    # no task is the correct process-global.
    register_runner(_process_runner)
    return run(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
