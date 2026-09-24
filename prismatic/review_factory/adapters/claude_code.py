"""L0 adapter: Claude Code CLI session output -> ReviewArtifact (plan §4).

Pure translator: no network, no side effects, no judgment. Imports only
``prismatic.review_factory.artifact`` (workstream A) plus the adapter-shared
``._artifact`` helpers and stdlib.

Documented input fixture — a minimal Claude Code CLI session log as JSON::

    {
      "session_id": "cc-20260923-001",        # required (or "run_id")
      "started_at": "2026-09-23T23:00:00Z",
      "ended_at": "2026-09-23T23:14:00Z",     # fallback for submitted_at
      "user_prompt": "Add retry logic ...",   # -> intent.brief ("prompt" also read)
      "acceptance_criteria": ["retries 3x"],  # -> intent.goals ("goals" also read)
      "plan_path": "plans/x.md",              # -> intent.plan_ref ("plan_ref" also read)
      "base_tree": "<sha256>", "head_tree": "<sha256>",
      "diff": "diff --git ...",               # -> diff.unified ("diff_text" also read)
      "files_changed": [                      # -> diff.files
        {"path": "hook.py", "change_type": "modified",
         "lines_added": 12, "lines_removed": 3}
      ],
      "commands_run": [                       # -> checks
        {"command": "pytest -q", "exit_code": 0,
         "log": "3 passed", "ran_at": "2026-09-23T23:10:00Z"}
      ],
      "prior_receipts": ["rcpt-..."],
      "submitted_at": "2026-09-23T23:15:00Z"  # else ended_at, else now (UTC)
    }

Strict-schema behavior (workstream A's boundary is fail-closed):
* any field the session log does not carry becomes null plus a derived
  ``explicit_gaps`` entry — never fabricated;
* ``files_changed`` entries whose change kind is outside the closed
  vocabulary, or whose line counts are not non-negative ints, are omitted
  from ``diff.files`` (paths stay in ``novelty_context.first_seen_paths``);
* ``commands_run`` entries without an integer ``exit_code`` are omitted —
  a check without a result is not a deterministic result;
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


class ClaudeCodeAdapter:
    """Harness adapter for Claude Code CLI session logs."""

    harness_id = "claude-code-cli"

    def to_artifact(self, raw: Any) -> ReviewArtifact:
        return build_artifact(harness_id=self.harness_id, **self._extract(raw))

    def explicit_gaps(self, raw: Any) -> list[str]:
        kw = self._extract(raw)
        return compute_gaps(kw["intent"], kw["diff"], kw["checks"])

    # ── translation ──────────────────────────────────────────────────

    def _extract(self, raw: Any) -> dict[str, Any]:
        data = require_mapping(raw, "claude-code-cli")

        harness_run_id = run_id_from(data, "session_id", "run_id")

        brief = opt_str(data, "user_prompt") or opt_str(data, "prompt")
        goals = data.get("acceptance_criteria") or data.get("goals") or []
        if not isinstance(goals, list) or any(not isinstance(g, str) for g in goals):
            raise AdapterError(
                "claude-code-cli: acceptance_criteria/goals must be a list of strings"
            )
        plan_ref = opt_str(data, "plan_path") or opt_str(data, "plan_ref")
        intent = Intent(plan_ref=plan_ref, brief=brief, goals=list(goals))

        diff_text = opt_str(data, "diff") or opt_str(data, "diff_text")
        unified = truncate_unified(diff_text) if diff_text is not None else None
        files: list[DiffFile] = []
        if "files_changed" in data:
            raw_files = data["files_changed"]
            if not isinstance(raw_files, list):
                raise AdapterError("claude-code-cli: files_changed must be a list")
            for entry in raw_files:
                if not isinstance(entry, dict):
                    raise AdapterError(
                        "claude-code-cli: files_changed entries must be mappings"
                    )
                path = entry.get("path")
                if not isinstance(path, str) or not path:
                    raise AdapterError(
                        "claude-code-cli: files_changed entry missing path"
                    )
                built = make_diff_file(
                    path,
                    normalize_change_type(entry.get("change_type")),
                    entry.get("lines_added"),
                    entry.get("lines_removed"),
                )
                if built is not None:
                    files.append(built)
        diff = Diff(
            base_tree=opt_str(data, "base_tree"),
            head_tree=opt_str(data, "head_tree"),
            unified=unified,
            files=files,
        )

        checks = []
        raw_cmds = data.get("commands_run") or []
        if not isinstance(raw_cmds, list):
            raise AdapterError("claude-code-cli: commands_run must be a list")
        for cmd in raw_cmds:
            if not isinstance(cmd, dict):
                raise AdapterError(
                    "claude-code-cli: commands_run entries must be mappings"
                )
            name = cmd.get("command") or cmd.get("name")
            if not isinstance(name, str) or not name:
                raise AdapterError(
                    "claude-code-cli: command entry missing command/name"
                )
            check = make_check(
                name, cmd.get("exit_code"), opt_str(cmd, "log"), opt_str(cmd, "ran_at")
            )
            if check is not None:
                checks.append(check)

        prior_receipts = str_list(
            data.get("prior_receipts"), "claude-code-cli: prior_receipts"
        )
        submitted_at = (
            opt_str(data, "submitted_at") or opt_str(data, "ended_at") or now_iso()
        )

        return {
            "harness_run_id": harness_run_id,
            "submitted_at": submitted_at,
            "intent": intent,
            "diff": diff,
            "checks": checks,
            "prior_receipts": prior_receipts,
            "novelty_context": NoveltyContext(first_seen_paths=[f.path for f in files]),
        }
