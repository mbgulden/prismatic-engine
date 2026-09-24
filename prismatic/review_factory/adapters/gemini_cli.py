"""L0 adapter: Gemini CLI session output -> ReviewArtifact (plan §4).

Pure translator: no network, no side effects, no judgment. Imports only
``prismatic.review_factory.artifact`` (workstream A) plus the adapter-shared
``._artifact`` helpers and stdlib.

Documented input fixture — a minimal Gemini CLI session log as JSON::

    {
      "session_id": "gem-20260923-001",       # required (or "run_id")
      "prompt": "Refactor the retry helper",  # -> intent.brief
      "model": "gemini-2.5-pro",              # harness-only; dropped (no artifact field)
      "goals": ["helper is pure"],            # -> intent.goals
      "plan_ref": "plans/x.md",               # -> intent.plan_ref
      "base_commit": "<sha>", "head_commit": "<sha>",
      "diff_text": "diff --git ...",          # -> diff.unified
      "file_operations": [                    # -> diff.files
        {"path": "retry.py", "operation": "modify",
         "lines_added": 8, "lines_removed": 2}
      ],
      "shell_commands": [                     # -> checks
        {"cmd": "pytest -q", "exit_code": 0,
         "output": "3 passed", "ran_at": "2026-09-23T23:10:00Z"}
      ],
      "prior_receipts": ["rcpt-..."],
      "submitted_at": "2026-09-23T23:15:00Z"  # else now (UTC)
    }

Strict-schema behavior (workstream A's boundary is fail-closed):
* any field the session log does not carry becomes null plus a derived
  ``explicit_gaps`` entry — never fabricated;
* ``file_operations`` entries whose operation is outside the closed
  change-type vocabulary, or whose line counts are not non-negative ints,
  are omitted from ``diff.files`` (paths stay in
  ``novelty_context.first_seen_paths``);
* ``shell_commands`` entries without an integer ``exit_code`` are omitted;
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


class GeminiCliAdapter:
    """Harness adapter for Gemini CLI session logs."""

    harness_id = "gemini-cli"

    def to_artifact(self, raw: Any) -> ReviewArtifact:
        return build_artifact(harness_id=self.harness_id, **self._extract(raw))

    def explicit_gaps(self, raw: Any) -> list[str]:
        kw = self._extract(raw)
        return compute_gaps(kw["intent"], kw["diff"], kw["checks"])

    # ── translation ──────────────────────────────────────────────────

    def _extract(self, raw: Any) -> dict[str, Any]:
        data = require_mapping(raw, "gemini-cli")

        harness_run_id = run_id_from(data, "session_id", "run_id")

        brief = opt_str(data, "prompt") or opt_str(data, "user_prompt")
        goals = data.get("goals") or data.get("acceptance_criteria") or []
        if not isinstance(goals, list) or any(not isinstance(g, str) for g in goals):
            raise AdapterError("gemini-cli: goals must be a list of strings")
        plan_ref = opt_str(data, "plan_ref") or opt_str(data, "plan_path")
        intent = Intent(plan_ref=plan_ref, brief=brief, goals=list(goals))

        diff_text = opt_str(data, "diff_text") or opt_str(data, "diff")
        unified = truncate_unified(diff_text) if diff_text is not None else None
        files: list[DiffFile] = []
        if "file_operations" in data:
            raw_files = data["file_operations"]
            if not isinstance(raw_files, list):
                raise AdapterError("gemini-cli: file_operations must be a list")
            for entry in raw_files:
                if not isinstance(entry, dict):
                    raise AdapterError(
                        "gemini-cli: file_operations entries must be mappings"
                    )
                path = entry.get("path")
                if not isinstance(path, str) or not path:
                    raise AdapterError("gemini-cli: file_operations entry missing path")
                built = make_diff_file(
                    path,
                    normalize_change_type(entry.get("operation")),
                    entry.get("lines_added"),
                    entry.get("lines_removed"),
                )
                if built is not None:
                    files.append(built)
        diff = Diff(
            base_tree=opt_str(data, "base_commit") or opt_str(data, "base_tree"),
            head_tree=opt_str(data, "head_commit") or opt_str(data, "head_tree"),
            unified=unified,
            files=files,
        )

        checks = []
        raw_cmds = data.get("shell_commands") or []
        if not isinstance(raw_cmds, list):
            raise AdapterError("gemini-cli: shell_commands must be a list")
        for cmd in raw_cmds:
            if not isinstance(cmd, dict):
                raise AdapterError(
                    "gemini-cli: shell_commands entries must be mappings"
                )
            name = cmd.get("cmd") or cmd.get("command") or cmd.get("name")
            if not isinstance(name, str) or not name:
                raise AdapterError(
                    "gemini-cli: shell command entry missing cmd/command/name"
                )
            check = make_check(
                name,
                cmd.get("exit_code"),
                opt_str(cmd, "output"),
                opt_str(cmd, "ran_at"),
            )
            if check is not None:
                checks.append(check)

        prior_receipts = str_list(
            data.get("prior_receipts"), "gemini-cli: prior_receipts"
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
