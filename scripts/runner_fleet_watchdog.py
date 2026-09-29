#!/usr/bin/env python3
"""Runner fleet watchdog: detect phantom-busy self-hosted runners, recover safely.

Problem: cancelling an in-progress GitHub Actions job can leave a self-hosted
runner reporting ``busy: true`` to GitHub with zero jobs actually running. The
manual recovery (``systemctl restart`` on the runner unit) has no grace period
and races real job starts -- it once killed a live main-branch signal suite.

This watchdog closes that race:

1. Diagnose purely from the GitHub API (no box access needed): a runner is a
   *phantom candidate* when it is online, reports busy, and its name is absent
   from the set of runners with live (queued/in_progress) jobs.
2. Declare phantom-busy only after the candidacy persists for ``--grace-seconds``
   (default 300) across check cycles. State survives in a JSON file so grace
   periods span cron invocations.
3. Recover ONLY after a *fresh* pre-restart fetch proves the runner still has no
   live job. If a job appeared between diagnosis and restart, abort and alert.
   This is the hard invariant: **never restart a runner with a live job.**
4. Emit a structured alert (JSONL) on every recovery, aborted recovery, offline
   runner, and check failure.

The script is stdlib-only so it can run on any ops box without the engine venv.

Exit codes: 0 healthy/no action; 1 check error; 2 recovered >=1 runner;
3 alerts emitted but no recovery performed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "https://api.github.com"
USER_AGENT = "prismatic-runner-fleet-watchdog/1.0"

# Classification outcomes.
LEGIT = "legit"  # a live job is attributed to this runner: never touch it
IDLE = "idle"  # online, not busy, no live job: healthy
PHANTOM_CANDIDATE = "phantom_candidate"  # online + busy + no live job
OFFLINE = "offline"  # runner not online: alert, never auto-restart
UNKNOWN = "unknown"  # diagnosis data missing: alert, never restart

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_RECOVERED = 2
EXIT_ALERTS = 3


@dataclass
class RunnerState:
    name: str
    busy: bool
    online: bool


class GitHubClient:
    """Thin read-only GitHub Actions client (urllib, stdlib only)."""

    def __init__(
        self, repo: str, token: str, timeout: float = 30.0, max_pages: int = 10
    ):
        self.repo = repo
        self.token = token
        self.timeout = timeout
        self.max_pages = max_pages

    def _get(self, path: str):
        url = API + path
        pages = []
        while url and len(pages) < self.max_pages:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": USER_AGENT,
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    pages.append(json.load(resp))
                    url = _next_link(resp.headers.get("Link", ""))
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"GitHub API {e.code} on {path}: {e.reason}") from e
            except urllib.error.URLError as e:
                raise RuntimeError(f"GitHub API network error on {path}: {e}") from e
        return pages

    def list_runners(self) -> list[RunnerState]:
        runners = []
        for page in self._get(f"/repos/{self.repo}/actions/runners?per_page=100"):
            for r in page.get("runners", []):
                runners.append(
                    RunnerState(
                        name=r.get("name", ""),
                        busy=bool(r.get("busy")),
                        online=r.get("status") == "online",
                    )
                )
        return runners

    def live_job_runners(self) -> set[str]:
        """Names of runners with a queued or in_progress job right now."""
        live: set[str] = set()
        for page in self._get(
            f"/repos/{self.repo}/actions/runs?status=in_progress&per_page=100"
        ):
            for run in page.get("workflow_runs", []):
                run_id = run.get("id")
                if run_id is None:
                    continue
                for jobs_page in self._get(
                    f"/repos/{self.repo}/actions/runs/{run_id}/jobs?per_page=100"
                ):
                    for job in jobs_page.get("jobs", []):
                        name = job.get("runner_name")
                        if name and job.get("status") in ("queued", "in_progress"):
                            live.add(name)
        return live


def _next_link(link_header: str) -> str:
    for part in link_header.split(","):
        segments = part.strip().split(";")
        if len(segments) == 2 and 'rel="next"' in segments[1]:
            return segments[0].strip().strip("<>")
    return ""


def classify(runner: RunnerState, live: set[str]) -> str:
    """Pure diagnosis: which state is this runner in?

    Anything we cannot prove is a phantom degrades to a non-restartable
    outcome. Only PHANTOM_CANDIDATE can ever lead to a restart, and only
    after the grace period plus a fresh pre-restart check.
    """
    if not runner.online:
        return OFFLINE
    if runner.name in live:
        return LEGIT
    if runner.busy:
        return PHANTOM_CANDIDATE
    return IDLE


class WatchdogState:
    """Grace-period bookkeeping, persisted as JSON across invocations.

    File shape: {"runners": {"<name>": {"first_seen": <epoch>,
    "dry_run_notified": <bool>}}}
    """

    def __init__(self, path: str, now=None):
        self.path = path
        self.now = now or time.time
        self._data: dict = {"runners": {}}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("runners"), dict):
                self._data = data
        except (OSError, ValueError):
            # Corrupt or missing state: start over. Grace periods restart
            # from zero -- never a shortcut to a restart.
            self._data = {"runners": {}}

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"warning: could not persist watchdog state: {e}", file=sys.stderr)

    def note_candidate(self, name: str) -> None:
        entry = self._data["runners"].get(name)
        if entry is None:
            self._data["runners"][name] = {
                "first_seen": self.now(),
                "dry_run_notified": False,
            }

    def is_confirmed(self, name: str, grace_seconds: float) -> bool:
        entry = self._data["runners"].get(name)
        if entry is None:
            return False
        return self.now() - float(entry.get("first_seen", self.now())) >= grace_seconds

    def mark_notified(self, name: str) -> None:
        entry = self._data["runners"].get(name)
        if entry is not None:
            entry["dry_run_notified"] = True

    def was_notified(self, name: str) -> bool:
        entry = self._data["runners"].get(name)
        return bool(entry and entry.get("dry_run_notified"))

    def clear(self, name: str) -> None:
        self._data["runners"].pop(name, None)

    def drop_non_candidates(self, candidates: set[str]) -> None:
        for name in list(self._data["runners"]):
            if name not in candidates:
                self.clear(name)


class Alerter:
    """Structured JSONL alerts: local log file + stdout."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self.emitted: list[dict] = []

    def emit(
        self, alert_type: str, runner: str = "", detail: dict | None = None
    ) -> dict:
        alert = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": alert_type,
            "runner": runner,
            "detail": detail or {},
        }
        self.emitted.append(alert)
        line = json.dumps(alert, sort_keys=True)
        print(line)
        try:
            if self.log_path:
                os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as e:
            print(f"warning: could not append to alert log: {e}", file=sys.stderr)
        return alert


class Systemctl:
    """Restart self-hosted runner units via systemd."""

    def __init__(self, runner=None):
        self._runner = runner or subprocess.run

    def restart(self, unit: str) -> bool:
        try:
            proc = self._runner(
                ["systemctl", "restart", unit],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as e:
            print(f"systemctl restart {unit} failed to launch: {e}", file=sys.stderr)
            return False
        if proc.returncode != 0:
            print(
                f"systemctl restart {unit} exited {proc.returncode}: "
                f"{proc.stderr.strip()}",
                file=sys.stderr,
            )
            return False
        return True


def unit_for_runner(unit_template: str, repo: str, runner_name: str) -> str:
    repo_slug = repo.replace("/", "-")
    return unit_template.format(repo_slug=repo_slug, runner_name=runner_name)


@dataclass
class CycleResult:
    recovered: list[str]
    alerts: list[dict]
    error: str = ""


def run_cycle(
    github,
    state: WatchdogState,
    alerter: Alerter,
    systemctl: Systemctl,
    *,
    grace_seconds: float,
    unit_template: str,
    repo: str,
    dry_run: bool,
    post_restart_timeout: float = 60.0,
    post_restart_poll_interval: float = 5.0,
    webhook_url: str = "",
) -> CycleResult:
    """One watchdog pass. Pure orchestration; all I/O via injected clients."""
    result = CycleResult(recovered=[], alerts=[])

    def _alert(alert_type: str, runner: str = "", detail: dict | None = None) -> None:
        alert = alerter.emit(alert_type, runner, detail)
        result.alerts.append(alert)
        if webhook_url:
            _post_webhook(webhook_url, alert)

    try:
        runners = github.list_runners()
        live = github.live_job_runners()
    except Exception as e:  # noqa: BLE001 -- diagnosis data missing: no restarts
        _alert("check_failed", detail={"error": str(e)})
        result.error = str(e)
        return result

    live_by_name = {r.name: r for r in runners}
    classifications = {r.name: classify(r, live) for r in runners}

    # Offline runners: alert, never auto-restart.
    for name, outcome in classifications.items():
        if outcome == OFFLINE:
            _alert("runner_offline", name, {"busy": live_by_name[name].busy})

    # Grace bookkeeping: only current candidates keep their first_seen.
    candidates = {n for n, o in classifications.items() if o == PHANTOM_CANDIDATE}
    for name in candidates:
        state.note_candidate(name)
    state.drop_non_candidates(candidates)

    confirmed = [n for n in candidates if state.is_confirmed(n, grace_seconds)]

    for name in sorted(confirmed):
        if dry_run:
            if not state.was_notified(name):
                _alert(
                    "recovery_dry_run_would_restart",
                    name,
                    {
                        "grace_seconds": grace_seconds,
                        "unit": unit_for_runner(unit_template, repo, name),
                    },
                )
                state.mark_notified(name)
            continue
        # HARD INVARIANT: fresh pre-restart check. The diagnosis snapshot
        # may be stale; a job may have started since. Re-fetch now.
        try:
            fresh_live = github.live_job_runners()
        except Exception as e:  # noqa: BLE001 -- cannot prove idle: abort
            _alert("recovery_aborted_check_failed", name, {"error": str(e)})
            continue
        if name in fresh_live:
            _alert(
                "recovery_aborted_live_job",
                name,
                {"note": "job started between diagnosis and restart"},
            )
            state.clear(name)  # legit now; candidacy restarts from zero
            continue
        unit = unit_for_runner(unit_template, repo, name)
        if not systemctl.restart(unit):
            _alert("runner_restart_failed", name, {"unit": unit})
            continue
        if _wait_healthy(
            github, name, post_restart_timeout, post_restart_poll_interval
        ):
            _alert("runner_recovered", name, {"unit": unit})
            state.clear(name)
            result.recovered.append(name)
        else:
            _alert(
                "runner_recovery_unverified",
                name,
                {"unit": unit, "note": "restart issued but runner not healthy yet"},
            )

    state.save()
    return result


def _wait_healthy(
    github, name: str, timeout: float, poll_interval: float = 5.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            runners = {r.name: r for r in github.list_runners()}
            live = github.live_job_runners()
        except Exception:  # noqa: BLE001 -- transient; keep waiting
            time.sleep(poll_interval)
            continue
        runner = runners.get(name)
        if runner is None or not runner.online:
            time.sleep(poll_interval)
            continue
        # Healthy = back online and either idle or doing real work.
        if not runner.busy or name in live:
            return True
        time.sleep(poll_interval)
    return False


def _post_webhook(url: str, alert: dict) -> None:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(alert).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception as e:  # noqa: BLE001 -- alerting must never break the cycle
        print(f"warning: webhook post failed: {e}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Watchdog for phantom-busy GitHub self-hosted runners."
    )
    p.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="owner/repo (or $GITHUB_REPOSITORY)",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN", ""),
        help="GitHub token (or $GITHUB_TOKEN)",
    )
    p.add_argument(
        "--grace-seconds",
        type=float,
        default=300,
        help="candidacy duration before phantom is confirmed",
    )
    p.add_argument(
        "--state-file",
        default=os.path.expanduser("~/.prismatic/runner-fleet-watchdog/state.json"),
    )
    p.add_argument(
        "--alert-log",
        default=os.path.expanduser("~/.prismatic/runner-fleet-watchdog/alerts.jsonl"),
    )
    p.add_argument(
        "--unit-template",
        default="actions.runner.{repo_slug}.{runner_name}",
        help="systemd unit; {repo_slug} and {runner_name} fields",
    )
    p.add_argument("--webhook-url", default="", help="optional webhook for alert POSTs")
    p.add_argument(
        "--no-dry-run",
        action="store_true",
        help="ARM the watchdog: actually restart confirmed "
        "phantom runners. Default is dry-run (diagnose and "
        "alert only).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.repo or not args.token:
        print(
            "error: --repo and --token (or GITHUB_REPOSITORY/GITHUB_TOKEN) "
            "are required",
            file=sys.stderr,
        )
        return EXIT_ERROR

    github = GitHubClient(args.repo, args.token)
    state = WatchdogState(args.state_file)
    alerter = Alerter(args.alert_log)
    systemctl = Systemctl()
    result = run_cycle(
        github,
        state,
        alerter,
        systemctl,
        grace_seconds=args.grace_seconds,
        unit_template=args.unit_template,
        repo=args.repo,
        dry_run=not args.no_dry_run,
        webhook_url=args.webhook_url,
    )
    if result.error:
        return EXIT_ERROR
    if result.recovered:
        return EXIT_RECOVERED
    if result.alerts:
        return EXIT_ALERTS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
