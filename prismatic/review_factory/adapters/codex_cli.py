"""L0 adapter: Codex CLI session output -> ReviewArtifact (plan §4).

Pure translator: no network, no side effects, no judgment. Imports only
``prismatic.review_factory.artifact`` (workstream A) plus the adapter-shared
``._artifact`` helpers and stdlib.

Documented input fixture — a minimal Codex CLI session log as JSON. The
Codex CLI emits an event stream; this adapter reads ``events`` where
``type`` is ``file_change`` or ``command`` and ignores other event types::

    {
      "session_id": "codex-20260923-001",     # required (or "run_id")
      "instructions": "Fix the flaky test",   # -> intent.brief
      "goals": ["test passes 10/10 runs"],    # -> intent.goals
      "plan_ref": "plans/x.md",               # -> intent.plan_ref
      "git_base": "<sha>", "git_head": "<sha>",
      "diff_patch": "diff --git ...",         # -> diff.unified
      "events": [
        {"type": "file_change", "path": "test_x.py", "operation": "modify",
         "lines_added": 4, "lines_removed": 1},
        {"type": "command", "command": "pytest -q", "exit_status": 0,
         "output": "10 passed", "ran_at": "2026-09-23T23:10:00Z"}
      ],
      "prior_receipts": ["rcpt-..."],
      "submitted_at": "2026-09-23T23:15:00Z"  # else now (UTC)
    }

Strict-schema behavior (workstream A's boundary is fail-closed):
* any field the session log does not carry becomes null plus a derived
  ``explicit_gaps`` entry — never fabricated;
* ``file_change`` events whose operation is outside the closed change-type
  vocabulary, or whose line counts are not non-negative ints, are omitted
  from ``diff.files`` (paths stay in ``novelty_context.first_seen_paths``);
* ``command`` events without an integer ``exit_status``/``exit_code`` are
  omitted;
* missing ``session_id``/``run_id`` or a non-mapping ``raw`` fails closed
  with ``AdapterError``.
"""

from __future__ import annotations

from typing import Any

from ..artifact import Diff, DiffFile, Intent, NoveltyContext, ReviewArtifact
from ._artifact import (
    AdapterError,
    build_artifact,
    compute_gaps,
    make_check,
    make_diff_file,
    normalize_change_type,
    now_iso,
    opt_str,
    require_mapping,
    run_id_from,
    str_list,
    truncate_unified,
)


class CodexCliAdapter:
    """Harness adapter for Codex CLI session logs."""

    harness_id = "codex-cli"

    def to_artifact(self, raw: Any) -> ReviewArtifact:
        return build_artifact(harness_id=self.harness_id, **self._extract(raw))

    def explicit_gaps(self, raw: Any) -> list[str]:
        kw = self._extract(raw)
        return compute_gaps(kw["intent"], kw["diff"], kw["checks"])

    # ── translation ──────────────────────────────────────────────────

    def _extract(self, raw: Any) -> dict[str, Any]:
        data = require_mapping(raw, "codex-cli")

        harness_run_id = run_id_from(data, "session_id", "run_id")

        brief = opt_str(data, "instructions") or opt_str(data, "prompt")
        goals = data.get("goals") or []
        if not isinstance(goals, list) or any(not isinstance(g, str) for g in goals):
            raise AdapterError("codex-cli: goals must be a list of strings")
        plan_ref = opt_str(data, "plan_ref")
        intent = Intent(plan_ref=plan_ref, brief=brief, goals=list(goals))

        diff_text = opt_str(data, "diff_patch") or opt_str(data, "diff")
        unified = truncate_unified(diff_text) if diff_text is not None else None
        files: list[DiffFile] = []
        checks = []
        if "events" in data:
            events = data["events"]
            if not isinstance(events, list):
                raise AdapterError("codex-cli: events must be a list")
            for event in events:
                if not isinstance(event, dict):
                    raise AdapterError("codex-cli: events entries must be mappings")
                kind = event.get("type")
                if kind == "file_change":
                    path = event.get("path")
                    if not isinstance(path, str) or not path:
                        raise AdapterError("codex-cli: file_change event missing path")
                    built = make_diff_file(
                        path,
                        normalize_change_type(event.get("operation")),
                        event.get("lines_added"),
                        event.get("lines_removed"),
                    )
                    if built is not None:
                        files.append(built)
                elif kind == "command":
                    name = event.get("command") or event.get("name")
                    if not isinstance(name, str) or not name:
                        raise AdapterError(
                            "codex-cli: command event missing command/name"
                        )
                    exit_code = event.get("exit_status")
                    if exit_code is None:
                        exit_code = event.get("exit_code")
                    check = make_check(
                        name,
                        exit_code,
                        opt_str(event, "output"),
                        opt_str(event, "ran_at"),
                    )
                    if check is not None:
                        checks.append(check)
                # Other event types (reasoning, tool metadata, ...) carry no
                # artifact fields; ignored, not gaps.
        diff = Diff(
            base_tree=opt_str(data, "git_base") or opt_str(data, "base_tree"),
            head_tree=opt_str(data, "git_head") or opt_str(data, "head_tree"),
            unified=unified,
            files=files,
        )

        prior_receipts = str_list(
            data.get("prior_receipts"), "codex-cli: prior_receipts"
        )
        submitted_at = opt_str(data, "submitted_at") or now_iso()

        return {
            "harness_run_id": harness_run_id,
            "submitted_at": submitted_at,
            "intent": intent,
            "diff": diff,
            "checks": checks,
            "prior_receipts": prior_receipts,
            "novelty_context": NoveltyContext(first_seen_paths=[f.path for f in files]),
        }
