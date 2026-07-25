"""Canonical Google Antigravity (AGY) CLI workflow for Prismatic Engine.

This module is the single transport and prompt contract for unattended AGY work.
It ports the durable tmux anchor proven in ``agentic-swarm-ops`` while keeping
Prismatic's admission, exact-artifact, and external-action gates outside AGY.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

CANONICAL_AGY_WORKFLOW_VERSION = "1.1.1"
CANONICAL_TRANSPORT = "tmux-durable-anchor"
# AGY requires a Go duration for print mode. This is the maximum whole-second
# duration accepted by time.ParseDuration (~292 years), used only as a protocol
# bridge. PE applies no wall-clock runtime deadline.
AGY_UNBOUNDED_PRINT_TIMEOUT = "2562047h47m16s"
CANONICAL_MAX_ATTEMPTS = 3
CANONICAL_MAX_PROMPT_CHARS = 1200
ACTIVITY_POLL_SECONDS = 1.0
ACTIVITY_QUIET_SECONDS = 60
ACTIVITY_SUSPECT_SECONDS = 900
CANONICAL_RESULT_MARKER = "PRISMATIC_AGY_RESULT_V1"
CANONICAL_ADMISSION_MARKER = "PRISMATIC_AGY_ADMISSION_V1"
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")


class AgyWorkflowError(RuntimeError):
    """A fail-closed canonical workflow validation error."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_absolute_regular(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise AgyWorkflowError(f"{label} must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AgyWorkflowError(f"{label} is unavailable: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AgyWorkflowError(f"{label} must be a regular non-symlink file")
    if metadata.st_mode & 0o022:
        raise AgyWorkflowError(f"{label} must not be group/world writable")
    return path


def _require_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise AgyWorkflowError(f"{label} must be an absolute non-symlink directory")
    return path


@dataclass(frozen=True)
class AgyLaunchSpec:
    identifier: str
    task_file: str
    workspace: str
    artifact_root: str
    result_path: str
    plan_path: str
    stdout_path: str
    stderr_path: str
    diagnostics_path: str
    agy_binary: str
    agy_binary_sha256: str
    agy_home: str
    model: str = "gemini-3.6-flash-high"
    sandbox: bool = True

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.identifier):
            raise AgyWorkflowError("identifier contains unsafe characters")
        _require_absolute_regular(Path(self.task_file), "task_file")
        workspace = _require_directory(Path(self.workspace), "workspace")
        artifact_root = _require_directory(Path(self.artifact_root), "artifact_root")
        binary = _require_absolute_regular(Path(self.agy_binary), "agy_binary")
        _require_directory(Path(self.agy_home), "agy_home")
        if _sha256_file(binary) != self.agy_binary_sha256:
            raise AgyWorkflowError("agy_binary SHA-256 mismatch")
        if not self.model or any(ch.isspace() for ch in self.model):
            raise AgyWorkflowError("model must be a non-empty canonical model id")
        for label, raw in (
            ("result_path", self.result_path),
            ("plan_path", self.plan_path),
            ("stdout_path", self.stdout_path),
            ("stderr_path", self.stderr_path),
            ("diagnostics_path", self.diagnostics_path),
        ):
            path = Path(raw)
            if not path.is_absolute():
                raise AgyWorkflowError(f"{label} must be absolute")
            _require_directory(path.parent, f"{label} parent")
        allowed_roots = {workspace.resolve(), artifact_root.resolve()}
        for raw in (
            self.result_path,
            self.plan_path,
            self.stdout_path,
            self.stderr_path,
            self.diagnostics_path,
        ):
            parent = Path(raw).parent.resolve()
            if not any(
                parent == root or root in parent.parents for root in allowed_roots
            ):
                raise AgyWorkflowError(
                    "all output parents must be under workspace or task directory"
                )
        if len(self.goal_prompt()) > CANONICAL_MAX_PROMPT_CHARS:
            raise AgyWorkflowError("canonical goal prompt exceeds 1200 characters")

    def goal_prompt(self) -> str:
        return (
            f"/goal Read the frozen task at {self.task_file} first. "
            f"Work only in {self.workspace} and only within its declared scope. "
            f"Before editing, write the implementation plan to {self.plan_path}. "
            "Then execute, self-validate, repair failures, and independently re-read the diff. "
            f"Write the final result to {self.result_path} with marker {CANONICAL_RESULT_MARKER}, "
            "including files changed, exact verification commands/results/logs, commit identity, "
            "boundaries, and follow-ups. Do not write Linear/GitHub, merge, deploy, restart, "
            "increase concurrency, or claim external proof. Exit after the result is durable."
        )

    def argv(self) -> list[str]:
        args = [
            self.agy_binary,
            "--print",
            self.goal_prompt(),
            "--dangerously-skip-permissions",
            "--print-timeout",
            AGY_UNBOUNDED_PRINT_TIMEOUT,
            "--model",
            self.model,
            "--add-dir",
            self.workspace,
            "--add-dir",
            str(Path(self.task_file).parent),
            "--add-dir",
            self.artifact_root,
        ]
        if self.sandbox:
            args.append("--sandbox")
        args.extend(["--log-file", self.diagnostics_path])
        return args

    def manifest(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload.update(
            {
                "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
                "transport": CANONICAL_TRANSPORT,
                "task_sha256": _sha256_file(Path(self.task_file)),
                "goal_prompt": self.goal_prompt(),
                "argv": self.argv(),
                "external_writes_allowed": False,
                "checkpoint_commits_allowed": False,
            }
        )
        return payload


def canonical_contract() -> dict[str, Any]:
    return {
        "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
        "transport": CANONICAL_TRANSPORT,
        "prompt_prefix": "/goal ",
        "prompt_max_chars": CANONICAL_MAX_PROMPT_CHARS,
        "runtime_deadline": None,
        "runtime_policy": "no-wall-clock-cap-progress-supervised",
        "agy_print_timeout_protocol_bridge": AGY_UNBOUNDED_PRINT_TIMEOUT,
        "activity_quiet_seconds": ACTIVITY_QUIET_SECONDS,
        "activity_suspect_seconds": ACTIVITY_SUSPECT_SECONDS,
        "maximum_attempts": CANONICAL_MAX_ATTEMPTS,
        "headless_mode": "--print",
        "permission_mode": "--dangerously-skip-permissions",
        "result_marker": CANONICAL_RESULT_MARKER,
        "requirements": [
            "immutable hash-bound AGY binary",
            "frozen task file",
            "implementation plan before edits",
            "unique tmux session and exact pane identity",
            "separate stdout and diagnostic logs",
            "durable result packet before completion",
            "exact-session cleanup and no surviving descendants",
            "producer output remains untrusted pending independent verification",
        ],
        "forbidden": [
            "raw detached AGY Popen",
            "mutable auto-updating binary",
            "PTY-less background AGY launch",
            "generic checkpoint auto-commits",
            "agent-authored Linear/GitHub/merge/deploy actions",
            "producer self-acceptance",
        ],
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _proc_start_ticks(pid: int) -> str:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return raw[raw.rfind(")") + 2 :].split()[19]


def _tmux_session_name(identifier: str, task_sha256: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", identifier)[:48]
    return f"prismatic-agy-{slug}-{task_sha256[:10]}"


def validate_admission_receipt(path: Path, spec: AgyLaunchSpec) -> dict[str, Any]:
    _require_absolute_regular(path, "admission_receipt")
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "marker": CANONICAL_ADMISSION_MARKER,
        "authorized": True,
        "task_sha256": _sha256_file(Path(spec.task_file)),
        "agy_binary_sha256": spec.agy_binary_sha256,
    }
    for key, expected in required.items():
        if payload.get(key) != expected:
            raise AgyWorkflowError(f"admission receipt mismatch: {key}")
    if not isinstance(payload.get("event_id"), str) or not payload["event_id"]:
        raise AgyWorkflowError("admission receipt requires event_id")
    token = payload.get("attempt_token")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise AgyWorkflowError("admission receipt requires a SHA-256 attempt token")
    attempt = payload.get("attempt")
    if type(attempt) is not int or not 1 <= attempt <= CANONICAL_MAX_ATTEMPTS:
        raise AgyWorkflowError("admission attempt is outside the canonical cap")
    return payload


def launch_tmux(
    spec: AgyLaunchSpec,
    runtime_dir: Path,
    *,
    admission_receipt: Path,
    tmux: str = "/usr/bin/tmux",
) -> dict[str, Any]:
    manifest = spec.manifest()
    admission = validate_admission_receipt(admission_receipt, spec)
    runtime_dir = _require_directory(runtime_dir, "runtime_dir")
    _require_absolute_regular(Path(tmux), "tmux")
    launch_dir = runtime_dir / spec.identifier
    launch_dir.mkdir(mode=0o700)
    manifest_path = launch_dir / "manifest.json"
    result_record = launch_dir / "process-result.json"
    activity_path = launch_dir / "activity.json"
    receipt_path = launch_dir / "launch-receipt.json"
    manifest["process_result_path"] = str(result_record)
    manifest["activity_path"] = str(activity_path)
    manifest["runtime_deadline"] = None
    manifest["admission"] = {
        "marker": CANONICAL_ADMISSION_MARKER,
        "event_id": admission["event_id"],
        "attempt": admission["attempt"],
        "attempt_token": admission["attempt_token"],
        "receipt_path": str(admission_receipt),
        "receipt_sha256": _sha256_file(admission_receipt),
    }
    _atomic_json(manifest_path, manifest)
    session = _tmux_session_name(spec.identifier, str(manifest["task_sha256"]))
    exists = subprocess.run(
        [tmux, "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if exists.returncode == 0:
        raise AgyWorkflowError(f"tmux session already exists: {session}")
    runner = shlex.join(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_run-manifest",
            str(manifest_path),
        ]
    )
    subprocess.run(
        [tmux, "new-session", "-d", "-s", session, "-c", spec.workspace, runner],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        [tmux, "set-option", "-t", session, "remain-on-exit", "on"], check=True
    )
    pane_pid = int(
        subprocess.check_output(
            [tmux, "display-message", "-p", "-t", f"{session}:0.0", "#{pane_pid}"],
            text=True,
        ).strip()
    )
    receipt = {
        "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
        "transport": CANONICAL_TRANSPORT,
        "identifier": spec.identifier,
        "session": session,
        "pane_pid": pane_pid,
        "pane_start_ticks": _proc_start_ticks(pane_pid),
        "task_sha256": manifest["task_sha256"],
        "event_id": admission["event_id"],
        "attempt": admission["attempt"],
        "attempt_token": admission["attempt_token"],
        "admission_receipt_sha256": _sha256_file(admission_receipt),
        "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "process_result_path": str(result_record),
        "activity_path": str(activity_path),
        "runtime_deadline": None,
        "started_at_unix": time.time(),
        "accepted": True,
    }
    _atomic_json(receipt_path, receipt)
    return receipt


def wait_tmux(
    receipt_path: Path,
    *,
    tmux: str = "/usr/bin/tmux",
) -> dict[str, Any]:
    _require_absolute_regular(receipt_path, "receipt_path")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    session = str(receipt.get("session") or "")
    if not session.startswith("prismatic-agy-") or not _ID_RE.fullmatch(
        session.removeprefix("prismatic-agy-")
    ):
        raise AgyWorkflowError("receipt has a noncanonical tmux session")
    manifest_path = _require_absolute_regular(
        Path(str(receipt.get("manifest_path") or "")), "manifest_path"
    )
    if _sha256_file(manifest_path) != receipt.get("manifest_sha256"):
        raise AgyWorkflowError("receipt manifest digest mismatch")
    result_path = Path(str(receipt.get("process_result_path") or ""))
    while True:
        if result_path.is_file():
            break
        has_session = subprocess.run(
            [tmux, "has-session", "-t", session],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if has_session.returncode != 0:
            raise AgyWorkflowError("tmux session disappeared before result receipt")
        time.sleep(0.2)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("process_tree_cleanup_verified") is not True or result.get(
        "surviving_process_identities"
    ):
        raise AgyWorkflowError("exact AGY process tree cleanup was not verified")
    subprocess.run(
        [tmux, "kill-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    pane_pid = int(receipt["pane_pid"])
    pane_start_ticks = str(receipt["pane_start_ticks"])
    cleanup_verified = False
    for _ in range(50):
        try:
            cleanup_verified = _proc_start_ticks(pane_pid) != pane_start_ticks
        except (FileNotFoundError, ProcessLookupError):
            cleanup_verified = True
        if cleanup_verified:
            break
        time.sleep(0.1)
    result["session"] = session
    result["cleanup_verified"] = cleanup_verified
    if not cleanup_verified:
        raise AgyWorkflowError("tmux pane identity survived cleanup")
    return result


def _canonical_child_env(spec: AgyLaunchSpec) -> dict[str, str]:
    env = {
        "HOME": spec.agy_home,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "TERM": os.environ.get("TERM", "xterm-256color"),
    }
    return env


def _enable_child_subreaper() -> None:
    """Keep daemonizing descendants attached to the exact AGY supervisor."""
    pr_set_child_subreaper = 36
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(pr_set_child_subreaper, 1, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise AgyWorkflowError(
            f"failed to enable exact-run child subreaper: errno={error_number}"
        )


def _identity_alive(pid: int, start_ticks: str) -> bool:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw[raw.rfind(")") + 2 :].split()
        return fields[19] == start_ticks and fields[0] != "Z"
    except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError):
        return False


def _process_tree_snapshot(
    root_pid: int,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    parents: dict[int, int] = {}
    starts: dict[int, str] = {}
    stats: dict[int, tuple[int, int]] = {}
    io_totals: dict[int, tuple[int, int]] = {}
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw_stat = (entry / "stat").read_text()
            fields = raw_stat[raw_stat.rfind(")") + 2 :].split()
            parents[pid] = int(fields[1])
            starts[pid] = fields[19]
            stats[pid] = (int(fields[11]), int(fields[12]))
            io_values: dict[str, int] = {}
            for line in (entry / "io").read_text().splitlines():
                key, value = line.split(":", 1)
                if key in {"read_bytes", "write_bytes"}:
                    io_values[key] = int(value.strip())
            io_totals[pid] = (
                io_values.get("read_bytes", 0),
                io_values.get("write_bytes", 0),
            )
        except (OSError, IndexError, ValueError):
            continue
    tree = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in tree and pid not in tree:
                tree.add(pid)
                changed = True
    descendants = tree - {root_pid}
    identities = [
        {"pid": pid, "start_ticks": starts[pid]}
        for pid in sorted(descendants)
        if pid in starts
    ]
    metrics = {
        "process_count": len(descendants & stats.keys()),
        "cpu_ticks": sum(sum(stats.get(pid, (0, 0))) for pid in descendants),
        "read_bytes": sum(io_totals.get(pid, (0, 0))[0] for pid in descendants),
        "write_bytes": sum(io_totals.get(pid, (0, 0))[1] for pid in descendants),
    }
    return metrics, identities


def _signal_exact_identities(
    identities: dict[tuple[int, str], None], signum: int
) -> None:
    for pid, start_ticks in list(identities):
        if not _identity_alive(pid, start_ticks):
            continue
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            continue


def _reap_children() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _contain_exact_process_tree(
    supervisor_pid: int,
    observed: dict[tuple[int, str], None],
) -> list[dict[str, Any]]:
    """Terminate and verify all exact descendants after exit or explicit cancel."""
    _metrics, current = _process_tree_snapshot(supervisor_pid)
    for identity in current:
        observed[(int(identity["pid"]), str(identity["start_ticks"]))] = None
    _signal_exact_identities(observed, signal.SIGTERM)
    term_deadline = time.monotonic() + 2.0
    while time.monotonic() < term_deadline:
        _metrics, current = _process_tree_snapshot(supervisor_pid)
        for identity in current:
            observed[(int(identity["pid"]), str(identity["start_ticks"]))] = None
        if not any(_identity_alive(pid, ticks) for pid, ticks in observed):
            return []
        time.sleep(0.05)
    _signal_exact_identities(observed, signal.SIGKILL)
    kill_deadline = time.monotonic() + 3.0
    survivors: list[dict[str, Any]] = []
    while time.monotonic() < kill_deadline:
        _metrics, current = _process_tree_snapshot(supervisor_pid)
        for identity in current:
            observed[(int(identity["pid"]), str(identity["start_ticks"]))] = None
        survivors = [
            {"pid": pid, "start_ticks": ticks}
            for pid, ticks in observed
            if _identity_alive(pid, ticks)
        ]
        if not survivors:
            return []
        time.sleep(0.05)
    return survivors


def _artifact_metrics(spec: AgyLaunchSpec) -> dict[str, int | float]:
    file_count = 0
    total_bytes = 0
    newest_mtime = 0.0
    root = Path(spec.artifact_root)
    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            metadata = path.stat()
        except OSError:
            continue
        file_count += 1
        total_bytes += metadata.st_size
        newest_mtime = max(newest_mtime, metadata.st_mtime)
    return {
        "artifact_file_count": file_count,
        "artifact_bytes": total_bytes,
        "artifact_newest_mtime": newest_mtime,
    }


def _activity_signature(metrics: dict[str, int | float]) -> tuple[int | float, ...]:
    return tuple(metrics[key] for key in sorted(metrics))


def _run_manifest(path: Path) -> int:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    spec_fields = {field.name for field in AgyLaunchSpec.__dataclass_fields__.values()}
    spec = AgyLaunchSpec(**{key: manifest[key] for key in spec_fields})
    expected = spec.manifest()
    for key in ("task_sha256", "argv", "goal_prompt", "transport", "workflow_version"):
        if manifest.get(key) != expected.get(key):
            raise AgyWorkflowError(f"manifest drift: {key}")
    _enable_child_subreaper()
    supervisor_pid = os.getpid()
    result_record = Path(manifest["process_result_path"])
    child: subprocess.Popen[bytes] | None = None
    cancel_signal: int | None = None

    def request_cancel(signum: int, _frame: object) -> None:
        nonlocal cancel_signal
        cancel_signal = signal.SIGTERM if signum == signal.SIGHUP else signum

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_cancel)
    started = time.time()
    stdout_path = Path(spec.stdout_path)
    stderr_path = Path(spec.stderr_path)
    observed: dict[tuple[int, str], None] = {}
    survivors: list[dict[str, Any]] = []
    cleanup_verified = False
    with (
        stdout_path.open("ab", buffering=0) as stdout,
        stderr_path.open("ab", buffering=0) as stderr,
    ):
        child = subprocess.Popen(
            spec.argv(),
            cwd=spec.workspace,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=_canonical_child_env(spec),
        )
        child_start_ticks = _proc_start_ticks(child.pid)
        observed[(child.pid, child_start_ticks)] = None
        activity_path = Path(str(manifest["activity_path"]))
        last_signature: tuple[int | float, ...] | None = None
        last_progress_at = started
        sequence = 0
        while True:
            polled_exit_code = child.poll()
            metrics, identities = _process_tree_snapshot(supervisor_pid)
            for identity in identities:
                observed[(int(identity["pid"]), str(identity["start_ticks"]))] = None
            terminal_requested = (
                polled_exit_code is not None or cancel_signal is not None
            )
            if terminal_requested:
                survivors = _contain_exact_process_tree(supervisor_pid, observed)
                if child.poll() is None:
                    exit_code = child.wait()
                else:
                    exit_code = int(child.returncode)
                _reap_children()
                cleanup_verified = not survivors
                metrics, identities = _process_tree_snapshot(supervisor_pid)
            else:
                exit_code = None
            now = time.time()
            metrics_with_artifacts: dict[str, int | float] = {
                **metrics,
                **_artifact_metrics(spec),
            }
            signature = _activity_signature(metrics_with_artifacts)
            changed = last_signature is None or signature != last_signature
            if changed:
                last_progress_at = now
            quiet_seconds = max(0.0, now - last_progress_at)
            if terminal_requested:
                classification = "terminal"
            elif quiet_seconds >= ACTIVITY_SUSPECT_SECONDS:
                classification = "suspect"
            elif quiet_seconds >= ACTIVITY_QUIET_SECONDS:
                classification = "quiet"
            else:
                classification = "working"
            sequence += 1
            _atomic_json(
                activity_path,
                {
                    "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
                    "identifier": spec.identifier,
                    "sequence": sequence,
                    "observed_at_unix": now,
                    "last_progress_at_unix": last_progress_at,
                    "quiet_seconds": round(quiet_seconds, 3),
                    "classification": classification,
                    "process_alive": not terminal_requested,
                    "child_pid": child.pid,
                    "child_start_ticks": child_start_ticks,
                    "process_identities": identities,
                    "runtime_deadline": None,
                    "automatic_kill": False,
                    "cancel_requested": cancel_signal is not None,
                    "process_tree_cleanup_verified": cleanup_verified
                    if terminal_requested
                    else None,
                    "metrics": metrics_with_artifacts,
                },
            )
            last_signature = signature
            if terminal_requested:
                break
            time.sleep(ACTIVITY_POLL_SECONDS)
        assert exit_code is not None
    record = {
        "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
        "identifier": spec.identifier,
        "child_pid": child.pid,
        "child_start_ticks": child_start_ticks,
        "exit_code": exit_code,
        "cancel_requested": cancel_signal is not None,
        "process_tree_cleanup_verified": cleanup_verified,
        "surviving_process_identities": survivors,
        "observed_process_count": len(observed),
        "elapsed_seconds": round(time.time() - started, 3),
        "finished_at_unix": time.time(),
        "stdout_path": spec.stdout_path,
        "stderr_path": spec.stderr_path,
        "diagnostics_path": spec.diagnostics_path,
        "activity_path": str(activity_path),
        "runtime_deadline": None,
        "result_path": spec.result_path,
        "result_exists": Path(spec.result_path).is_file(),
        "result_sha256": _sha256_file(Path(spec.result_path))
        if Path(spec.result_path).is_file()
        else None,
    }
    _atomic_json(result_record, record)
    return exit_code if cleanup_verified else 125


def _spec_from_args(args: argparse.Namespace) -> AgyLaunchSpec:
    return AgyLaunchSpec(
        identifier=args.identifier,
        task_file=str(Path(args.task_file).resolve()),
        workspace=str(Path(args.workspace).resolve()),
        artifact_root=str(Path(args.artifact_root).resolve()),
        result_path=str(Path(args.result_path).resolve()),
        plan_path=str(Path(args.plan_path).resolve()),
        stdout_path=str(Path(args.stdout_path).resolve()),
        stderr_path=str(Path(args.stderr_path).resolve()),
        diagnostics_path=str(Path(args.diagnostics_path).resolve()),
        agy_binary=str(Path(args.agy_binary).resolve()),
        agy_binary_sha256=args.agy_binary_sha256,
        agy_home=str(Path(args.agy_home).resolve()),
        model=args.model,
        sandbox=not args.no_sandbox,
    )


def _add_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--identifier", required=True)
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--plan-path", required=True)
    parser.add_argument("--stdout-path", required=True)
    parser.add_argument("--stderr-path", required=True)
    parser.add_argument("--diagnostics-path", required=True)
    parser.add_argument("--agy-binary", required=True)
    parser.add_argument("--agy-binary-sha256", required=True)
    parser.add_argument("--agy-home", required=True)
    parser.add_argument("--model", default="gemini-3.6-flash-high")
    parser.add_argument("--no-sandbox", action="store_true")


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prismatic agy")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("contract", help="Print the canonical AGY workflow contract")
    render = commands.add_parser("render", help="Validate and render a launch manifest")
    _add_spec_arguments(render)
    launch = commands.add_parser("launch", help="Launch via the canonical tmux anchor")
    _add_spec_arguments(launch)
    launch.add_argument("--runtime-dir", required=True)
    launch.add_argument("--admission-receipt", required=True)
    launch.add_argument(
        "--execute", action="store_true", help="Required real-launch gate"
    )
    wait = commands.add_parser(
        "wait", help="Wait without a runtime deadline for one canonical tmux run"
    )
    wait.add_argument("--receipt", required=True)
    internal = commands.add_parser("_run-manifest", help=argparse.SUPPRESS)
    internal.add_argument("manifest")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "contract":
        print(json.dumps(canonical_contract(), indent=2, sort_keys=True))
        return 0
    if args.command == "_run-manifest":
        return _run_manifest(Path(args.manifest))
    if args.command == "wait":
        result = wait_tmux(Path(args.receipt).resolve())
        print(json.dumps(result, indent=2, sort_keys=True))
        return int(result.get("exit_code", 1))
    spec = _spec_from_args(args)
    if args.command == "render":
        print(json.dumps(spec.manifest(), indent=2, sort_keys=True))
        return 0
    if not args.execute:
        raise AgyWorkflowError("launch requires --execute")
    receipt = launch_tmux(
        spec,
        Path(args.runtime_dir).resolve(),
        admission_receipt=Path(args.admission_receipt).resolve(),
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
