#!/usr/bin/env bash
set -euo pipefail

export HOME=/home/ubuntu
export AGY_BIN="${AGY_BIN:-/home/ubuntu/.local/bin/agy}"
export AGY_CLI_HOME="${AGY_CLI_HOME:-/home/ubuntu/.hermes/profiles/kai/home}"

cd /home/ubuntu/work/prismatic-engine
exec python3 scripts/agy_sandbox_event_supervisor.py \
  --cron-mode \
  --long-run \
  --lane-mode auto \
  --active-project pwp \
  --backlog-age-days 30 \
  --jitter 15-30 \
  --backoff 8-15 \
  --max-concurrent 3
