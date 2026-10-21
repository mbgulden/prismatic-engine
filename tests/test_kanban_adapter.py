"""Tests for the kanban handoff verification adapter (Prismatic step A).

Synthetic fixtures (a temp ``kanban.db`` with the real schema plus temp
attachment files) prove the three step-A verdict outcomes and idempotency:

* complete handoff         -> CLEAN
* missing deliverable      -> REPAIR
* stated sha256 mismatch   -> REJECT   (the adversarial case: a hash its own
                                        file disproves must never reach CLEAN)
* second run               -> no second comment (idempotent)

The adapter reads ``kanban.db`` read-only and the only kanban mutation is the
single VERDICT comment, so these tests point ``KANBAN_DB_PATH`` /
``KANBAN_ATTACHMENTS_ROOT`` / ``PRISMATIC_AUDIT_DIR`` at temp dirs and stub
``post_comment`` (the one side-effecting call) so the test can count invocations.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from prismatic.review_factory.adapters import kanban as kb
from prismatic.review_factory import verdict as _verdict_mod

# Real schema (mirrors ~/.hermes/kanban.db). Only the tables the
# adapter reads, with their real columns.
_SCHEMA = """
CREATE TABLE task_attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    filename     TEXT NOT NULL,
    stored_path  TEXT NOT NULL,
    content_type TEXT,
    size         INTEGER NOT NULL DEFAULT 0,
    uploaded_by  TEXT,
    created_at   INTEGER NOT NULL
);
CREATE TABLE task_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    run_id     INTEGER,
    kind       TEXT NOT NULL,
    payload    TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE task_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL,
    profile             TEXT,
    step_key            TEXT,
    status              TEXT NOT NULL,
    claim_lock          TEXT,
    claim_expires       INTEGER,
    worker_pid          INTEGER,
    max_runtime_seconds INTEGER,
    last_heartbeat_at   INTEGER,
    started_at          INTEGER NOT NULL,
    ended_at            INTEGER,
    outcome             TEXT,
    summary             TEXT,
    metadata            TEXT,
    error               TEXT
);
CREATE TABLE tasks (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    body                 TEXT,
    assignee             TEXT,
    status               TEXT NOT NULL,
    priority             INTEGER DEFAULT 0,
    created_by           TEXT,
    created_at           INTEGER NOT NULL,
    started_at           INTEGER,
    completed_at         INTEGER,
    workspace_kind       TEXT NOT NULL DEFAULT 'scratch',
    workspace_path       TEXT,
    claim_lock           TEXT,
    claim_expires        INTEGER,
    tenant               TEXT,
    result               TEXT,
    idempotency_key      TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    worker_pid           INTEGER,
    last_failure_error   TEXT,
    max_runtime_seconds  INTEGER,
    last_heartbeat_at    INTEGER,
    current_run_id       INTEGER,
    workflow_template_id TEXT,
    current_step_key     TEXT,
    skills               TEXT,
    max_retries          INTEGER
);
"""


def _make_db(
    tmp_path: Path,
    *,
    task_id: str,
    body: str,
    result: str,
    attachments: list[tuple[str, str, int]] | None = None,
    comments: list[str] | None = None,
) -> str:
    """Create a synthetic kanban.db (real schema) and return its path.

    ``attachments`` is a list of (filename, stored_path, size); the caller is
    responsible for ensuring ``stored_path`` exists on disk (or not, to test a
    missing deliverable).
    """
    db = tmp_path / f"kanban-{task_id}.db"
    con = sqlite3.connect(str(db))
    con.executescript(_SCHEMA)
    now = int(time.time())
    con.execute(
        "INSERT INTO tasks"
        " (id, title, body, status, created_at, completed_at, result)"
        " VALUES (?,?,?,?,?,?,?)",
        (task_id, "t", body, "done", now, now, result),
    )
    for fn, sp, sz in attachments or []:
        con.execute(
            "INSERT INTO task_attachments"
            " (task_id, filename, stored_path, size, created_at)"
            " VALUES (?,?,?,?,?)",
            (task_id, fn, sp, sz, now),
        )
    for c in comments or []:
        con.execute(
            "INSERT INTO task_comments (task_id, author, body, created_at)"
            " VALUES (?,?,?,?)",
            (task_id, "test", c, now),
        )
    con.commit()
    con.close()
    return str(db)


def _write_file(parent: Path, name: str, content: str) -> str:
    p = parent / name
    p.write_text(content, encoding="utf-8")
    return str(p)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """Point the adapter's store/attachment env at temp dirs.

    Returns the temp root. Each test sets ``KANBAN_DB_PATH`` to its own DB.
    """
    audit = tmp_path / "audit"
    audit.mkdir()
    monkeypatch.setenv("PRISMATIC_AUDIT_DIR", str(audit))
    monkeypatch.setenv("KANBAN_ATTACHMENTS_ROOT", str(tmp_path / "attachments"))
    return tmp_path


def test_complete_handoff_is_clean(env: Path, monkeypatch) -> None:
    body = (
        "Do the thing.\n\n## ACCEPTANCE CRITERIA\n- report exists\n- hashes verified\n"
    )
    report = _write_file(env, "report.md", "the report\n")
    result = f"Wrote the report to {report} and it is complete."
    db = _make_db(
        env,
        task_id="t_clean",
        body=body,
        result=result,
        attachments=[("report.md", report, 11)],
    )
    monkeypatch.setenv("KANBAN_DB_PATH", db)

    kv = kb.build_and_store(kb.load_task("t_clean"))
    assert kv.verdict.final == "CLEAN"
    line = kb.format_verdict_line(kv)
    assert line.startswith("VERDICT CLEAN")
    assert "checks: 4/4 pass" in line


def test_missing_deliverable_is_repair(env: Path, monkeypatch) -> None:
    body = "Do the thing.\n\n## ACCEPTANCE CRITERIA\n- deliverable present\n"
    missing = str(env / "nope" / "missing.md")  # intentionally not created
    # SOAK-1: a path is a claimed deliverable only with framing; the
    # original fixture ("Report written to …") no longer claims, so the
    # fixture states the framing the check now requires.
    result = f"Deliverable report saved to {missing}."
    db = _make_db(env, task_id="t_repair", body=body, result=result)
    monkeypatch.setenv("KANBAN_DB_PATH", db)

    kv = kb.build_and_store(kb.load_task("t_repair"))
    assert kv.verdict.final == "REPAIR"
    failed = {c.name for c in kv.checks if not c.passed}
    assert failed == {"deliverables_exist"}


def test_hash_mismatch_is_reject(env: Path, monkeypatch) -> None:
    body = "Do the thing.\n\n## ACCEPTANCE CRITERIA\n- report exists\n"
    report = _write_file(env, "report.md", "hello\n")
    bad = "0" * 64  # a stated sha256 the file disproves
    result = f"Wrote the report.\nsha256 {bad} {report}\n"
    db = _make_db(env, task_id="t_reject", body=body, result=result)
    monkeypatch.setenv("KANBAN_DB_PATH", db)

    kv = kb.build_and_store(kb.load_task("t_reject"))
    assert kv.verdict.final == "REJECT"
    failed = {c.name for c in kv.checks if not c.passed}
    assert failed == {"hashes_match"}


def test_second_run_is_idempotent(env: Path, monkeypatch) -> None:
    body = "Do the thing.\n\n## ACCEPTANCE CRITERIA\n- report exists\n"
    report = _write_file(env, "report.md", "hello\n")
    result = f"Report at {report}."
    db = _make_db(
        env,
        task_id="t_idem",
        body=body,
        result=result,
        attachments=[("report.md", report, 6)],
    )
    monkeypatch.setenv("KANBAN_DB_PATH", db)

    calls: list[str] = []

    def stub(task_id: str, line: str) -> None:
        # Faithful to `hermes kanban comment`: append to task_comments.
        calls.append(line)
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO task_comments (task_id, author, body, created_at)"
            " VALUES (?,?,?,?)",
            (task_id, "test", line, int(time.time())),
        )
        con.commit()
        con.close()

    monkeypatch.setattr(kb, "post_comment", stub)

    rc1 = kb.run("t_idem")
    rc2 = kb.run("t_idem")
    assert rc1 == 0 and rc2 == 0
    assert len(calls) == 1, "second run must not post a second comment"


def test_import_does_not_register_global_runner() -> None:
    """Importing the adapter must not mutate verdict's process-global runner.

    Regression test: a prior draft called ``register_runner`` from the
    library path (``build_and_store``), which leaked the kanban runner into
    every other suite in the same pytest process and broke the JEV
    ``replay()`` byte-identity test. A fresh interpreter proves the import is
    side-effect-free; ``register_runner`` is only called from the CLI entry
    point (:func:`kanban.main`).
    """
    import subprocess
    import sys

    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import prismatic.review_factory.verdict as v; "
            "import prismatic.review_factory.adapters.kanban; "
            "print(v._runner is None)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert r.stdout.strip() == "True", (
        "importing the kanban adapter registered a global runner — "
        "the library path must stay side-effect-free; see "
        "register_runner (wiring happens in main) "
        f"(stderr: {r.stderr[-200:]})"
    )


def test_build_and_store_does_not_register_global_runner(
    env: Path, monkeypatch
) -> None:
    """``build_and_store`` must not register the process-global runner either.

    The runner is passed to ``decide()`` explicitly; the global is reserved
    for the CLI wiring point so the library path stays side-effect-free.
    """
    body = "Do the thing.\n\n## ACCEPTANCE CRITERIA\n- report exists\n"
    report = _write_file(env, "report.md", "the report\n")
    result = f"Wrote the report to {report}."
    db = _make_db(env, task_id="t_glob", body=body, result=result)
    monkeypatch.setenv("KANBAN_DB_PATH", db)
    # make the leak observable if it regresses: sentinel that is not the default
    monkeypatch.setattr(_verdict_mod, "_runner", object())
    before = _verdict_mod._runner
    kb.build_and_store(kb.load_task("t_glob"))
    assert _verdict_mod._runner is before, (
        "build_and_store() mutated the verdict process-global runner"
    )


def test_hermes_home_overrides_profile_scoped_home(monkeypatch) -> None:
    """``HERMES_HOME`` must win over a profile-scoped ``$HOME``.

    Agent profiles run with ``$HOME`` pointed at the profile dir (e.g.
    ``~/.hermes/profiles/<name>/home``); the kanban DB and attachments
    live under the Hermes home, so the default paths must derive from
    ``HERMES_HOME`` when it is set, not from ``Path.home()``.
    """
    fake = "/tmp/hermes-home-e2e"
    monkeypatch.setenv("HERMES_HOME", fake)
    assert kb._hermes_home() == fake
    assert kb._kanban_db_path() == fake + "/kanban.db"
    assert kb._attachments_root() == fake + "/kanban/attachments"


# ─────────────────────────────────────────────────────────────────────
# SOAK-1 false-positive mirrors (t_b0e62049 fix pass)
# ─────────────────────────────────────────────────────────────────────


def test_parse_acceptance_goals_header_variants() -> None:
    """Fix 1: header variants real bodies use (SOAK-1 DISAGREE analysis)."""
    # Original exact form still works.
    assert kb.parse_acceptance_goals("## ACCEPTANCE CRITERIA:\n- a\n- b\n") == [
        "a",
        "b",
    ]
    # Bare ACCEPTANCE: label (t_3a7f4b91 shape).
    assert kb.parse_acceptance_goals("ACCEPTANCE:\n- a\n- b\n") == ["a", "b"]
    # Inline ACCEPTANCE = clause (t_8b2dd86a shape): the clause is the goal.
    assert kb.parse_acceptance_goals("ACCEPTANCE = items 1-5 pass\n") == [
        "items 1-5 pass"
    ]
    # Numbered SUGGESTED VERIFICATIONS FOR <role> (t_c554a68f shape).
    assert kb.parse_acceptance_goals(
        "## SUGGESTED VERIFICATIONS FOR MUSE\n1. one\n2) two\n3. three\n"
    ) == ["one", "two", "three"]
    # No AC section -> [] (data, not a parse error).
    assert kb.parse_acceptance_goals("Do the thing.\n") == []


def test_extract_claimed_deliverables_only_framed_paths() -> None:
    """Fix 3: only explicitly framed paths are claimed deliverables."""
    text = (
        "Deliverables saved to /tmp/build/out.zip — done.\n"
        "Checked /data/today.json and it was fine.\n"
    )
    assert kb.extract_claimed_deliverables(text) == ["/tmp/build/out.zip"]
    assert kb.extract_claimed_deliverables(None) == []


def test_soak_t3a7f4b91_directory_is_present_deliverable(
    env: Path, monkeypatch
) -> None:
    """SOAK-1 t_3a7f4b91 mirror: an existing DIRECTORY is a present
    deliverable (fix 2) and the bare ``ACCEPTANCE:`` header carries the
    goals (fix 1) -> CLEAN, not REPAIR.
    """
    kit = env / "sales-kit"
    kit.mkdir()
    (kit / "one-pager.md").write_text("# one-pager\n", encoding="utf-8")
    body = (
        "Build the 2026 print kit.\n\n"
        "COPY RULES — 2026 brand.\n\n"
        "ACCEPTANCE:\n"
        "- kit directory exists with all assets\n"
        "- one-pager renders to PDF\n"
    )
    result = f"Deliverables saved to {kit}/ — one-pager + pricing card."
    db = _make_db(env, task_id="t_soak_dir", body=body, result=result)
    monkeypatch.setenv("KANBAN_DB_PATH", db)

    kv = kb.build_and_store(kb.load_task("t_soak_dir"))
    assert kv.verdict.final == "CLEAN"
    assert {c.name for c in kv.checks if not c.passed} == set()


def test_soak_t8b2dd86a_bare_data_ref_not_a_deliverable(env: Path, monkeypatch) -> None:
    """SOAK-1 t_8b2dd86a mirror: a bare server-relative data reference in
    the result prose (``/data/today.json``) is not a claimed deliverable
    (fix 3) and the inline ``ACCEPTANCE = …`` clause satisfies
    criteria_present (fix 1) -> CLEAN, not REPAIR.
    """
    body = (
        "Verify the Access gate on the console.\n\n"
        "ACCEPTANCE = items 1-5 pass (item 6 optional spot-check). "
        "Report findings back here; flag any gate hole loudly.\n"
    )
    result = (
        "ACCEPTANCE PASS: Access gate 302 live on / and /data/today.json "
        "(no leak); local nginx all-200. No gate holes found."
    )
    db = _make_db(env, task_id="t_soak_data", body=body, result=result)
    monkeypatch.setenv("KANBAN_DB_PATH", db)

    kv = kb.build_and_store(kb.load_task("t_soak_data"))
    assert kv.verdict.final == "CLEAN"
    assert {c.name for c in kv.checks if not c.passed} == set()


def test_soak_tc554a68f_suggested_verifications_list(env: Path, monkeypatch) -> None:
    """SOAK-1 t_c554a68f mirror: a numbered ``SUGGESTED VERIFICATIONS FOR
    <role>`` list satisfies criteria_present (fix 1) -> CLEAN, not REPAIR.
    """
    body = (
        "Review the Sentinel Console nightly roll.\n\n"
        "NO CHANGES MADE TO THE LIVE CONSOLE.\n\n"
        "## SUGGESTED VERIFICATIONS FOR MUSE\n"
        "1. job active in cron list\n"
        "2. script logic matches brief\n"
        "3. stale path confirmed\n"
    )
    result = (
        "Reviewed Sentinel Console nightly roll. All static verifications "
        "PASS; dry-run evidence on the parent task."
    )
    db = _make_db(env, task_id="t_soak_verify", body=body, result=result)
    monkeypatch.setenv("KANBAN_DB_PATH", db)

    kv = kb.build_and_store(kb.load_task("t_soak_verify"))
    assert kv.verdict.final == "CLEAN"
    assert {c.name for c in kv.checks if not c.passed} == set()
