"""Swappable stdin/stdout-JSON CLI harness adapter (C3 spike).

The portability claim under test: "one engine, any harness." This module is the
minimal interface for it — ANY command-line agent that can read a JSON task
envelope on stdin and print a JSON result on stdout can be driven as a
Prismatic ``AgentHarness`` with zero per-harness Python code. The harness is
never trusted: the execution-evidence receipt is built by running the task's
``verifier`` independently, so a ``verified`` verdict is earned, not claimed.

Protocol (``CLI_JSON_PROTOCOL_VERSION``):
  stdin  (task envelope, canonical JSON):
      {"protocol": "prismatic.cli-json/1", "task_id": "<sha256>",
       "prompt": "...", "workdir": "/path", "timeout_s": 600,
       "verifier": ["argv", "..."] | null}
  stdout (result — best effort, never trusted):
      {"verdict": "done" | "failed", "summary": "...", "artifacts": [...]}

The adapter maps the process outcome to ``HarnessStatus`` and the independent
verifier outcome to ``VerificationStatus``. ``SELF_REPORTED`` is never emitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from prismatic.execution_evidence import (
    CommandEvidence,
    ExecutionEvidence,
    FailureCategory,
    VerificationScope,
    VerificationStatus,
)
from prismatic.harnesses.base import AgentHarness, HarnessCapabilities, HarnessStatus

CLI_JSON_PROTOCOL_VERSION = "prismatic.cli-json/1"

_DEFAULT_TIMEOUT_S = 600
_MAX_OUTPUT_BYTES = 256 * 1024


def canonical_task_id(prompt: str, workdir: str, verifier: list[str] | None) -> str:
    """Stable sha256 id for a task (prompt + workdir + verifier)."""
    canonical = json.dumps(
        {"prompt": prompt, "workdir": workdir, "verifier": verifier},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_task_envelope(
    prompt: str,
    workdir: str,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    verifier: list[str] | None = None,
) -> dict[str, Any]:
    """Build the frozen JSON envelope fed to the CLI on stdin."""
    return {
        "protocol": CLI_JSON_PROTOCOL_VERSION,
        "task_id": canonical_task_id(prompt, workdir, verifier),
        "prompt": prompt,
        "workdir": workdir,
        "timeout_s": timeout_s,
        "verifier": verifier,
    }


def _truncate(text: str, limit: int = _MAX_OUTPUT_BYTES) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n…[truncated {len(text) - limit} bytes]"
    return text


class SubprocessJSONHarness(AgentHarness):
    """Drive any stdin/stdout-JSON CLI as an ``AgentHarness``.

    Config keys:
      argv:       command line to spawn (required), e.g.
                  ``["hermes", "run", "--format", "json"]`` or
                  ``["codex", "exec", "--json", "-"]``.
      run_dir:    sidecar directory for run records (default
                  ``$PRISMATIC_CLIJSON_RUNS`` or ``/tmp/prismatic/cli-json-runs``).
      timeout_s:  default per-run timeout (task envelope may override).
      env:        extra environment variables for the child.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        argv = self._config.get("argv")
        if not argv:
            raise ValueError("SubprocessJSONHarness requires config['argv']")
        self._argv: list[str] = [str(a) for a in argv]
        self._run_dir = Path(
            self._config.get(
                "run_dir",
                os.environ.get(
                    "PRISMATIC_CLIJSON_RUNS", "/tmp/prismatic/cli-json-runs"
                ),
            )
        )
        self._default_timeout = int(self._config.get("timeout_s", _DEFAULT_TIMEOUT_S))
        self._extra_env: dict[str, str] = {
            str(k): str(v) for k, v in dict(self._config.get("env", {})).items()
        }
        self._procs: dict[str, subprocess.Popen[str]] = {}

    # ── AgentHarness contract ──────────────────────────────────────

    @property
    def name(self) -> str:
        return str(self._config.get("name", "cli-json"))

    @property
    def models(self) -> list[str]:
        return [str(m) for m in self._config.get("models", [])]

    def capabilities(self) -> HarnessCapabilities:
        return HarnessCapabilities(
            streaming_logs=False,
            cost_tracking=False,
            concurrent_runs=4,
            supports_cancel=True,
            supports_timeout=True,
            extra={"protocol": CLI_JSON_PROTOCOL_VERSION},
        )

    def dispatch(self, task: dict[str, Any]) -> str:
        """Spawn the CLI, feed the envelope on stdin, wait, record the run."""
        prompt = task.get("prompt")
        if not prompt:
            raise ValueError("task requires 'prompt'")
        workdir = str(task.get("workdir") or os.getcwd())
        timeout_s = int(task.get("timeout_s") or self._default_timeout)
        verifier = task.get("verifier")
        verifier_argv = [str(a) for a in verifier] if verifier else None

        envelope = build_task_envelope(str(prompt), workdir, timeout_s, verifier_argv)
        run_id = f"clijson-{envelope['task_id']}-{uuid.uuid4().hex[:8]}"
        started_at = time.time()

        record: dict[str, Any] = {
            "run_id": run_id,
            "harness": self.name,
            "argv": self._argv,
            "envelope": envelope,
            "started_at": started_at,
            "completed_at": None,
            "status": HarnessStatus.RUNNING.value,
            "returncode": None,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "result_json": None,
            "protocol_ok": False,
            "error": "",
        }
        self._write_record(record)

        env = dict(os.environ)
        env.update(self._extra_env)
        try:
            proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=workdir,
                env=env,
                start_new_session=True,
            )
        except (OSError, FileNotFoundError) as exc:
            record.update(
                status=HarnessStatus.FAILED.value,
                completed_at=time.time(),
                error=f"spawn failed: {exc}",
            )
            self._write_record(record)
            return run_id

        self._procs[run_id] = proc
        try:
            stdout, stderr = proc.communicate(
                input=json.dumps(envelope), timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            stdout, stderr = proc.communicate()
            record.update(
                status=HarnessStatus.TIMEOUT.value,
                timed_out=True,
                error=f"exceeded timeout_s={timeout_s}",
            )
        else:
            record["status"] = (
                HarnessStatus.COMPLETED.value
                if proc.returncode == 0
                else HarnessStatus.FAILED.value
            )
            if proc.returncode != 0:
                record["error"] = f"exit code {proc.returncode}"
        finally:
            self._procs.pop(run_id, None)

        record.update(
            completed_at=time.time(),
            returncode=proc.returncode,
            stdout=_truncate(stdout or ""),
            stderr=_truncate(stderr or ""),
        )
        result_json, protocol_ok = self._parse_result(record["stdout"])
        record["result_json"] = result_json
        record["protocol_ok"] = protocol_ok
        if not protocol_ok and record["status"] == HarnessStatus.COMPLETED.value:
            # CLI exited 0 but didn't speak the protocol: don't trust it.
            record["status"] = HarnessStatus.FAILED.value
            record["error"] = "exit 0 but stdout was not protocol JSON"
        self._write_record(record)
        return run_id

    def status(self, run_id: str) -> dict[str, Any]:
        record = self._read_record(run_id)
        return {
            "status": record.get("status", HarnessStatus.UNKNOWN.value),
            "started_at": record.get("started_at"),
            "completed_at": record.get("completed_at"),
            "error": record.get("error", ""),
        }

    def cancel(self, run_id: str) -> bool:
        proc = self._procs.get(run_id)
        if proc is None:
            return False
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return False
        record = self._read_record(run_id)
        record.update(
            status=HarnessStatus.CANCELLED.value,
            completed_at=time.time(),
            error="cancelled by operator",
        )
        self._write_record(record)
        return True

    def logs(self, run_id: str, tail: int = 100) -> list[str]:
        record = self._read_record(run_id)
        lines = (
            f"$ {' '.join(record.get('argv', []))}\n"
            f"--- stdout ---\n{record.get('stdout', '')}\n"
            f"--- stderr ---\n{record.get('stderr', '')}\n"
        ).splitlines()
        return lines[-tail:]

    def cost(self, run_id: str) -> dict[str, Any]:
        # Honest nulls: a bare CLI rarely reports usage. Never fabricate zeros.
        self._read_record(run_id)  # validates the run exists
        return {"tokens_in": None, "tokens_out": None, "dollars": None}

    def health(self) -> dict[str, str]:
        return {"status": "ok", "harness": self.name}

    # ── Evidence receipts (the point of the spike) ────────────────

    def receipt(self, run_id: str) -> ExecutionEvidence:
        """Build the execution-evidence receipt for a run.

        The CLI's own verdict is recorded but NEVER trusted. The final
        ``VerificationStatus`` comes from running the task's ``verifier``
        independently. ``SELF_REPORTED`` is never emitted.
        """
        record = self._read_record(run_id)
        envelope = record.get("envelope", {})
        task_id = str(envelope.get("task_id", ""))
        workdir = str(envelope.get("workdir", ""))
        verifier = envelope.get("verifier")

        commands = [
            CommandEvidence(
                command=" ".join(record.get("argv", [])),
                exit_code=record.get("returncode"),
                scope=VerificationScope.AD_HOC_TARGETED,
                output_excerpt=_truncate(record.get("stdout", ""), 2000),
            )
        ]
        result_json = record.get("result_json") or {}
        artifacts = [str(a) for a in result_json.get("artifacts", [])]
        cli_verdict = str(result_json.get("verdict", ""))
        summary = (
            f"[{self.name}] cli_verdict={cli_verdict or 'none'} "
            f"protocol_ok={record.get('protocol_ok')} "
            f"harness_status={record.get('status')}"
        )

        harness_status = record.get("status")
        if record.get("timed_out"):
            return ExecutionEvidence(
                task_id=task_id,
                run_id=run_id,
                status=VerificationStatus.FAILED,
                scope=VerificationScope.AD_HOC_TARGETED,
                summary=summary + " — harness timed out",
                commands=commands,
                artifacts=artifacts,
                failure_category=FailureCategory.TIMEOUT,
                blocker=f"CLI exceeded timeout_s={envelope.get('timeout_s')}",
            )
        if harness_status != HarnessStatus.COMPLETED.value:
            return ExecutionEvidence(
                task_id=task_id,
                run_id=run_id,
                status=VerificationStatus.FAILED,
                scope=VerificationScope.AD_HOC_TARGETED,
                summary=summary + f" — harness {harness_status}",
                commands=commands,
                artifacts=artifacts,
                failure_category=FailureCategory.TOOLING_ERROR,
                blocker=record.get("error", ""),
            )
        if not verifier:
            return ExecutionEvidence(
                task_id=task_id,
                run_id=run_id,
                status=VerificationStatus.PARTIALLY_VERIFIED,
                scope=VerificationScope.NOT_RUN,
                summary=summary + " — no verifier configured; claim not checked",
                commands=commands,
                artifacts=artifacts,
            )

        verifier_argv = [str(a) for a in verifier]
        try:
            proc = subprocess.run(
                verifier_argv,
                cwd=workdir or None,
                capture_output=True,
                text=True,
                timeout=min(int(envelope.get("timeout_s", 60)), 300),
            )
            verifier_ok = proc.returncode == 0
            verifier_out = _truncate((proc.stdout or "") + (proc.stderr or ""), 2000)
            verifier_code: int | None = proc.returncode
        except (subprocess.TimeoutExpired, OSError) as exc:
            verifier_ok = False
            verifier_out = f"verifier error: {exc}"
            verifier_code = None

        commands.append(
            CommandEvidence(
                command=" ".join(verifier_argv),
                exit_code=verifier_code,
                scope=VerificationScope.AD_HOC_TARGETED,
                output_excerpt=verifier_out,
            )
        )
        if verifier_ok:
            return ExecutionEvidence(
                task_id=task_id,
                run_id=run_id,
                status=VerificationStatus.VERIFIED,
                scope=VerificationScope.AD_HOC_TARGETED,
                summary=summary + " — independent verifier PASS",
                commands=commands,
                artifacts=artifacts,
            )
        return ExecutionEvidence(
            task_id=task_id,
            run_id=run_id,
            status=VerificationStatus.FAILED,
            scope=VerificationScope.AD_HOC_TARGETED,
            summary=summary + " — independent verifier FAIL",
            commands=commands,
            artifacts=artifacts,
            failure_category=FailureCategory.VERIFICATION_FAILED,
            blocker=verifier_out,
        )

    # ── internals ──────────────────────────────────────────────────

    def _record_path(self, run_id: str) -> Path:
        return self._run_dir / f"{run_id}.json"

    def _write_record(self, record: dict[str, Any]) -> None:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._record_path(record["run_id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _read_record(self, run_id: str) -> dict[str, Any]:
        path = self._record_path(run_id)
        if not path.exists():
            raise KeyError(f"unknown run_id: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _parse_result(stdout: str) -> tuple[dict[str, Any] | None, bool]:
        try:
            payload = json.loads(stdout.strip())
        except (json.JSONDecodeError, AttributeError):
            return None, False
        if not isinstance(payload, dict):
            return None, False
        if payload.get("verdict") not in ("done", "failed"):
            return None, False
        return payload, True
