"""L0 adapter: Hermes agent run record -> ReviewArtifact (plan §4).

Pure translator: no network, no side effects, no judgment. Imports only
``prismatic.review_factory.artifact`` (workstream A) plus the adapter-shared
``._artifact`` helpers and stdlib.

Grounded in the repo's own Hermes harness conventions
(``prismatic/harnesses/hermes/adapter.py``): a run is a task envelope
(``run_id``, ``target``, ``service``, ``created_at``, ``task``) plus a status
record. The documented input fixture — a Hermes run record as JSON::

    {
      "run_id": "hermes-fred-a1b2c3d4e5f6",   # required (matches envelope id)
      "target": "fred",                       # agent name (harness-only)
      "service": "hermes-fred.service",       # systemd unit (harness-only)
      "created_at": "2026-09-23T23:00:00Z",   # fallback for submitted_at
      "finished_at": "2026-09-23T23:14:00Z",  # preferred for submitted_at
      "status": "completed",                  # harness-only; checks carry results
      "task": {                               # the dispatched brief
        "brief": "Triage the inbox",           # -> intent.brief
        "goals": ["no message older than 24h unread"],
        "plan_ref": "plans/inbox.md"
      },
      "base_tree": "<sha256>", "head_tree": "<sha256>",
      "diff": "diff --git ...",               # -> diff.unified
      "files_changed": [                      # -> diff.files
        {"path": "triage.py", "change_type": "modified",
         "lines_added": 5, "lines_removed": 1}
      ],
      "check_results": [                      # -> checks
        {"name": "pytest -q", "exit_code": 0,
         "log": "3 passed", "ran_at": "2026-09-23T23:10:00Z"}
      ],
      "prior_receipts": ["rcpt-..."],
      "submitted_at": "2026-09-23T23:15:00Z"  # else finished_at, else created_at
    }

``task`` may also be a plain string, in which case it is the brief and
goals stay [] (an empty goals list is valid — §3: Jev abstains when goals
are empty).

Strict-schema behavior (workstream A's boundary is fail-closed):
* any field the run record does not carry becomes null plus a derived
  ``explicit_gaps`` entry — never fabricated;
* ``files_changed`` entries outside the closed change-type vocabulary or
  without integer line counts are omitted from ``diff.files`` (paths stay
  in ``novelty_context.first_seen_paths``);
* ``check_results`` entries without an integer ``exit_code`` are omitted;
* a missing ``run_id`` or a non-mapping ``raw`` fails closed with
  ``AdapterError``.
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


class HermesRunAdapter:
    """Harness adapter for Hermes agent run records."""

    harness_id = "hermes"

    def to_artifact(self, raw: Any) -> ReviewArtifact:
        return build_artifact(harness_id=self.harness_id, **self._extract(raw))

    def explicit_gaps(self, raw: Any) -> list[str]:
        kw = self._extract(raw)
        return compute_gaps(kw["intent"], kw["diff"], kw["checks"])

    # ── translation ──────────────────────────────────────────────────

    def _extract(self, raw: Any) -> dict[str, Any]:
        data = require_mapping(raw, "hermes")

        harness_run_id = run_id_from(data, "run_id")

        task = data.get("task")
        brief: str | None = None
        goals: list[str] = []
        plan_ref: str | None = None
        if isinstance(task, str):
            brief = task if task.strip() else None
        elif isinstance(task, dict):
            brief = opt_str(task, "brief") or opt_str(task, "text")
            raw_goals = task.get("goals") or []
            if not isinstance(raw_goals, list) or any(
                not isinstance(g, str) for g in raw_goals
            ):
                raise AdapterError("hermes: task.goals must be a list of strings")
            goals = list(raw_goals)
            plan_ref = opt_str(task, "plan_ref")
        elif task is not None:
            raise AdapterError("hermes: task must be a mapping or a string")
        intent = Intent(plan_ref=plan_ref, brief=brief, goals=goals)

        diff_text = opt_str(data, "diff") or opt_str(data, "diff_text")
        unified = truncate_unified(diff_text) if diff_text is not None else None
        files: list[DiffFile] = []
        if "files_changed" in data:
            raw_files = data["files_changed"]
            if not isinstance(raw_files, list):
                raise AdapterError("hermes: files_changed must be a list")
            for entry in raw_files:
                if not isinstance(entry, dict):
                    raise AdapterError("hermes: files_changed entries must be mappings")
                path = entry.get("path")
                if not isinstance(path, str) or not path:
                    raise AdapterError("hermes: files_changed entry missing path")
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
        raw_checks = data.get("check_results") or data.get("checks") or []
        if not isinstance(raw_checks, list):
            raise AdapterError("hermes: check_results must be a list")
        for chk in raw_checks:
            if not isinstance(chk, dict):
                raise AdapterError("hermes: check_results entries must be mappings")
            name = chk.get("name")
            if not isinstance(name, str) or not name:
                raise AdapterError("hermes: check result missing name")
            check = make_check(
                name, chk.get("exit_code"), opt_str(chk, "log"), opt_str(chk, "ran_at")
            )
            if check is not None:
                checks.append(check)

        prior_receipts = str_list(data.get("prior_receipts"), "hermes: prior_receipts")
        submitted_at = (
            opt_str(data, "submitted_at")
            or opt_str(data, "finished_at")
            or opt_str(data, "created_at")
            or now_iso()
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
