#!/usr/bin/env bash
# ==============================================================================
# scripts/check_linear_cron_rate.sh
# Lint script to audit Linear API consumption rates for cron jobs.
# Fails if total rate exceeds 2000 requests/hour.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRISMATIC_DIR="$(dirname "$SCRIPT_DIR")"

# Default to the production orchestrator profile path, fallback to local repo root or arg
JOBS_JSON="${1:-/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json}"

if [[ ! -f "$JOBS_JSON" ]]; then
    # Try looking in the repo root cron/jobs.json if it exists
    if [[ -f "$PRISMATIC_DIR/cron/jobs.json" ]]; then
        JOBS_JSON="$PRISMATIC_DIR/cron/jobs.json"
    else
        echo "❌ Error: cron/jobs.json not found at '$JOBS_JSON' or '$PRISMATIC_DIR/cron/jobs.json'" >&2
        exit 1
    fi
fi

echo "🔍 Auditing Linear API rate in $JOBS_JSON..."

python3 - "$JOBS_JSON" << 'EOF'
import sys
import json
import os

jobs_file = sys.argv[1]

# Query cost mapping for known scripts
SCRIPT_QUERY_MAP = {
    "agent_dispatcher.py": 30,
    "comment_trigger_monitor.py": 3,
    "kai_callback_monitor.py": 3,
    "prismatic_event_trigger.py": 1,
    "agent_output_validator.py": 12,
    "agy_sandbox_event_supervisor.py": 1,
    "kai_delta_dispatcher.py": 1,
    "ned_delta_dispatcher.py": 1,
    "second_witness_agy_proxy.py": 1,
    "prismatic_port_progress.py": 1,
    "nightly_backlog_delta.py": 1,
    "action_item_extractor.py": 15,
    "github_pr_monitor.py": 4,
    "jules_session_watchdog.py": 1,
}

def get_cycles_per_hour(job):
    # Returns (active_cycles_per_hour, potential_cycles_per_hour)
    # potential assumes the job is enabled. active is 0 if paused.
    schedule = job.get("schedule", {})
    if not schedule:
        return 0.0, 0.0
    
    kind = schedule.get("kind")
    cycles = 0.0
    if kind == "interval":
        minutes = schedule.get("minutes")
        if minutes:
            cycles = 60.0 / minutes
        else:
            hours = schedule.get("hours")
            if hours:
                cycles = 1.0 / hours
            else:
                seconds = schedule.get("seconds")
                if seconds:
                    cycles = 3600.0 / seconds
    elif kind == "cron":
        expr = schedule.get("expr")
        if expr:
            parts = expr.split()
            if len(parts) >= 5:
                min_part, hour_part = parts[0], parts[1]
                hours_per_day = 24
                if hour_part != "*":
                    hours_per_day = len(hour_part.split(','))
                
                runs_per_hour = 1.0
                if min_part == "*":
                    runs_per_hour = 60.0
                elif min_part.startswith("*/"):
                    try:
                        step = int(min_part.split("/")[-1])
                        runs_per_hour = 60.0 / step
                    except ValueError:
                        runs_per_hour = 1.0
                elif "," in min_part:
                    runs_per_hour = float(len(min_part.split(',')))
                
                if hour_part != "*":
                    cycles = (runs_per_hour * hours_per_day) / 24.0
                else:
                    cycles = runs_per_hour
    
    is_paused = (
        not job.get("enabled", True) or 
        job.get("state") == "paused" or 
        job.get("paused", False)
    )
    
    active_cycles = 0.0 if is_paused else cycles
    return active_cycles, cycles

try:
    with open(jobs_file, "r") as f:
        data = json.load(f)
except Exception as e:
    print(f"❌ Failed to parse JSON file {jobs_file}: {e}")
    sys.exit(1)

raw_jobs = data if isinstance(data, list) else data.get("jobs", []) if isinstance(data, dict) else []

linear_jobs = []
for job in raw_jobs:
    if not isinstance(job, dict):
        continue
    
    name = job.get("name") or ""
    prompt = job.get("prompt") or ""
    script = job.get("script") or ""
    
    # Direct metadata checks
    is_linear = (
        "linear" in name.lower() or
        "linear" in prompt.lower() or
        "linear" in script.lower()
    )
    
    # If not matched by name/prompt/script, scan the script file content
    if not is_linear and script:
        jobs_dir = os.path.dirname(os.path.abspath(jobs_file))
        profile_dir = os.path.dirname(jobs_dir)
        paths_to_try = [
            os.path.join(profile_dir, "scripts", script),
            os.path.join(os.path.dirname(jobs_file), script),
            script
        ]
        for path in paths_to_try:
            if os.path.exists(path):
                try:
                    with open(path, "r", errors="ignore") as sf:
                        content = sf.read().lower()
                        if "linear" in content or "api.linear.app" in content or "lineartaskprovider" in content:
                            is_linear = True
                            break
                except Exception:
                    pass
    
    if is_linear:
        linear_jobs.append(job)

print(f"Found {len(linear_jobs)} cron jobs related to Linear API.\n")
print(f"{'Job ID':<15} | {'Script/Job Name':<45} | {'Status':<8} | {'Req/Cycle':<9} | {'Cycles/Hr':<9} | {'Active R/Hr':<11} | {'Potential R/Hr':<14}")
print("-" * 125)

total_active_rate = 0.0
total_potential_rate = 0.0

for job in linear_jobs:
    job_id = job.get("id") or "unknown"
    script_name = job.get("script") or job.get("name") or "unknown"
    
    # Clean up name/script for display
    display_name = script_name
    if len(display_name) > 45:
        display_name = display_name[:42] + "..."
        
    status = "paused" if (not job.get("enabled", True) or job.get("state") == "paused" or job.get("paused", False)) else "active"
    
    # Find queries_per_cycle
    queries = 1 # default
    script_file = job.get("script") or ""
    if script_file in SCRIPT_QUERY_MAP:
        queries = SCRIPT_QUERY_MAP[script_file]
    else:
        # Check if name contains key terms
        for k, v in SCRIPT_QUERY_MAP.items():
            if k in display_name.lower():
                queries = v
                break
    
    active_cycles, potential_cycles = get_cycles_per_hour(job)
    active_rate = queries * active_cycles
    potential_rate = queries * potential_cycles
    
    total_active_rate += active_rate
    total_potential_rate += potential_rate
    
    print(f"{job_id:<15} | {display_name:<45} | {status:<8} | {queries:<9} | {potential_cycles:<9.2f} | {active_rate:<11.2f} | {potential_rate:<14.2f}")

print("-" * 125)
print(f"{'TOTALS':<72} | {'Active Rate:':<22} {total_active_rate:<11.2f} | {'Potential Rate:':<16} {total_potential_rate:<14.2f}")
print()

# Verification check
LIMIT = 2000.0
if total_active_rate > LIMIT:
    print(f"🚨 CI FAILURE: Total ACTIVE rate limit estimate ({total_active_rate:.2f} req/hour) exceeds the safety limit of {LIMIT} req/hour!")
    sys.exit(1)
elif total_potential_rate > LIMIT:
    print(f"⚠️  WARNING: Potential rate ({total_potential_rate:.2f} req/hour) exceeds safety limit of {LIMIT} if all jobs are resumed!")
    print("✅ CI Passed (active rate within limits).")
    sys.exit(0)
else:
    print("✅ CI Passed: All rate limit estimates are within safety limits.")
    sys.exit(0)

EOF
