# Runner fleet watchdog

`scripts/runner_fleet_watchdog.py` watches the repo's self-hosted GitHub Actions
runners for the **phantom-busy** state: a runner reporting `busy: true` to GitHub
with zero jobs actually running (seen twice after cancelling in-progress jobs).
The manual recovery (`systemctl restart` on the runner unit) once killed a live
main-branch signal suite, so the watchdog recovers only when it can prove the
runner is truly idle.

## The hard invariant

**The watchdog never restarts a runner with a live job.** Three mechanisms
enforce it:

1. **Grace period** (default 300s): a runner must report busy-with-no-live-job
   continuously before it is even *eligible* for recovery. State persists in a
   JSON file so grace spans cron invocations; corrupt state restarts grace
   from zero, never shortcuts it.
2. **Fresh pre-restart check**: immediately before issuing the restart, the
   watchdog re-fetches the live-job set. If the runner has a job now, it aborts
   and alerts (`recovery_aborted_live_job`).
3. **Dry-run default**: without `--no-dry-run` the watchdog only diagnoses and
   alerts. Arming it is an explicit, logged decision.

Offline runners are alerted on (`runner_offline`) but never auto-restarted.

## Running it

```bash
# Diagnose + alert only (default; safe to cron immediately)
python3 scripts/runner_fleet_watchdog.py \
  --repo mbgulden/prismatic-engine \
  --token "$GITHUB_TOKEN"

# Armed: actually restart confirmed phantom runners
python3 scripts/runner_fleet_watchdog.py \
  --repo mbgulden/prismatic-engine \
  --token "$GITHUB_TOKEN" \
  --no-dry-run
```

Exit codes: `0` healthy/no action, `1` check error, `2` recovered >=1 runner,
`3` alerts emitted but no recovery.

Options: `--grace-seconds`, `--state-file`, `--alert-log`, `--unit-template`
(default `actions.runner.{repo_slug}.{runner_name}`), `--webhook-url`.

## Deployment (not done by the PR — operator decision)

On the runner host, as a user with passwordless sudo for the two runner units:

```ini
# /etc/systemd/system/runner-fleet-watchdog.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/prismatic-engine/scripts/runner_fleet_watchdog.py \
  --repo mbgulden/prismatic-engine --no-dry-run
# /etc/prismatic/github-token contains one line: GITHUB_TOKEN=<token>
# (0400 perms). systemd reads it into the environment; the script itself
# only honors $GITHUB_TOKEN / --token.
EnvironmentFile=/etc/prismatic/github-token
```

```ini
# /etc/systemd/system/runner-fleet-watchdog.timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=2min
```

```ini
# /etc/sudoers.d/runner-fleet-watchdog
watchdog-user ALL=(root) NOPASSWD: /bin/systemctl restart actions.runner.mbgulden-prismatic-engine.*
```

Notes:

- The token needs only `actions:read` on the repo. It is read from
  `$GITHUB_TOKEN` / `--token` only — never bake it into the unit file.
  Under systemd, supply it via `EnvironmentFile=` pointing at a `0400`
  file containing a `GITHUB_TOKEN=<token>` line, as shown above.
- Every recovery, aborted recovery, and offline runner appends a JSON line to
  the alert log (default `~/.prismatic/runner-fleet-watchdog/alerts.jsonl`)
  and prints to stdout (captured by the journal under systemd).
- The watchdog is deliberately stdlib-only: it runs without the engine venv.

## Verification

`python3 -m pytest tests/test_runner_fleet_watchdog.py` — hermetic tests
(fake GitHub, fake systemctl, fake clock), including adversarial cases: no
restart before grace elapses, abort when a live job appears between diagnosis
and restart, flap during grace clears candidacy.
