#!/usr/bin/env python3
"""
Pipeline Metrics Dashboard — per-task + per-session health tracking.
Reads pipeline_metrics.jsonl and produces a structured health dashboard.

Usage:
    python3 scripts/pipeline_dashboard.py              # print dashboard to stdout
    python3 scripts/pipeline_dashboard.py --json       # output as JSON (for golden thread)
    python3 scripts/pipeline_dashboard.py --summary    # single-line summary for daily review

Metrics tracked per task:
    - Time to self-validate (minutes)
    - Peer review depth (findings/100 lines)
    - Fix cycle count
    - Time to approval (minutes)
    - Credit cost
    - Provider used

Metrics tracked per session:
    - Tasks completed / attempted
    - Review acceptance rate
    - Credit efficiency (credits per task)
    - Orchestrator touch points
    - Pipeline bypasses detected

Part of GRO-1478 — Pipeline metrics dashboard.
"""

import json
import os
import sys
from datetime import datetime, timezone
from collections import Counter

METRICS_PATHS = [
    "/tmp/pipeline_metrics.jsonl",
    "pipeline_metrics.jsonl",
]

# ── Load metrics ────────────────────────────────────────────

def load_metrics():
    """Load all metrics from known paths, deduplicate by issue_id."""
    metrics = {}
    for path in METRICS_PATHS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        issue_id = entry.get("issue_id")
                        if issue_id and issue_id not in metrics:
                            metrics[issue_id] = entry
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"  ⚠️ Error reading {path}: {e}", file=sys.stderr)
    return list(metrics.values())


# ── Compute per-task stats ──────────────────────────────────

def compute_task_stats(metrics_list):
    """Aggregate per-task metrics."""
    if not metrics_list:
        return {"count": 0}

    times = [m.get("time_to_self_validate", 0) for m in metrics_list]
    depths = [m.get("peer_review_depth", 0) for m in metrics_list]
    cycles = [m.get("fix_cycle_count", 0) for m in metrics_list]
    approvals = [m.get("time_to_approval", 0) for m in metrics_list]
    costs = [m.get("credit_cost", 0) for m in metrics_list]

    providers = Counter()
    for m in metrics_list:
        p = m.get("provider_used", "unknown")
        providers[p] += 1

    return {
        "count": len(metrics_list),
        "time_to_self_validate": {
            "avg": round(sum(times) / len(times), 1),
            "min": min(times),
            "max": max(times),
        },
        "peer_review_depth": {
            "avg": round(sum(depths) / len(depths), 1),
            "min": min(depths),
            "max": max(depths),
        },
        "fix_cycle_count": {
            "avg": round(sum(cycles) / len(cycles), 1),
            "min": min(cycles),
            "max": max(cycles),
        },
        "time_to_approval": {
            "avg": round(sum(approvals) / len(approvals), 1),
            "min": min(approvals),
            "max": max(approvals),
        },
        "credit_cost": {
            "total": round(sum(costs), 2),
            "avg": round(sum(costs) / len(costs), 2),
            "min": min(costs),
            "max": max(costs),
        },
        "providers": dict(providers.most_common(5)),
    }


# ── Compute per-session stats ───────────────────────────────

def compute_session_stats(metrics_list):
    """Group metrics by session (UTC date) and compute per-session aggregates."""
    if not metrics_list:
        return {"sessions": 0}

    sessions = {}
    for m in metrics_list:
        completed = m.get("completed_at", "")
        if completed:
            try:
                dt = datetime.fromisoformat(completed)
                date_key = dt.strftime("%Y-%m-%d")
                sessions.setdefault(date_key, []).append(m)
            except (ValueError, TypeError):
                pass

    if not sessions:
        # Fallback: group all into one "unknown" session
        sessions["unknown"] = metrics_list

    session_summaries = {}
    all_acceptance = []
    all_completed = 0
    all_attempted = 0

    for date_key, tasks in sorted(sessions.items()):
        completed = len(tasks)
        # Attempted = completed + any with fix_cycle_count > 1 (retries)
        attempted = sum(1 for t in tasks if t.get("fix_cycle_count", 1) > 1) + completed
        # Acceptance rate: tasks with fix_cycle_count <= 1 / total
        first_pass = sum(1 for t in tasks if t.get("fix_cycle_count", 1) <= 1)
        acceptance = first_pass / completed if completed else 0

        total_credits = sum(t.get("credit_cost", 0) for t in tasks)
        credit_eff = total_credits / completed if completed else 0

        session_summaries[date_key] = {
            "completed": completed,
            "attempted": attempted,
            "acceptance_rate": round(acceptance, 2),
            "credit_efficiency": round(credit_eff, 2),
            "total_credits": round(total_credits, 2),
            "tasks": [t.get("issue_id") for t in tasks],
        }

        all_completed += completed
        all_attempted += attempted
        all_acceptance.append(acceptance)

    avg_acceptance = sum(all_acceptance) / len(all_acceptance) if all_acceptance else 0

    return {
        "sessions": len(session_summaries),
        "total_completed": all_completed,
        "total_attempted": all_attempted,
        "avg_acceptance_rate": round(avg_acceptance, 2),
        "avg_credit_per_task": round(
            sum(t.get("credit_cost", 0) for t in metrics_list) / len(metrics_list), 2
        ) if metrics_list else 0,
        "daily": session_summaries,
    }


# ── Detect pipeline bypasses ─────────────────────────────────

def detect_bypasses(metrics_list):
    """Detect tasks that bypassed pipeline stages (low review depth, no cycle count)."""
    bypasses = []
    for m in metrics_list:
        depth = m.get("peer_review_depth", 0)
        cycles = m.get("fix_cycle_count", 0)
        if depth == 0 and cycles <= 0:
            bypasses.append(m.get("issue_id"))
    return bypasses


# ── Dashboard format ─────────────────────────────────────────

def print_dashboard(metrics_list):
    """Print a human-readable health dashboard."""
    task_stats = compute_task_stats(metrics_list)
    session_stats = compute_session_stats(metrics_list)
    bypasses = detect_bypasses(metrics_list)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 60)
    print(f"  Pipeline Metrics Dashboard — {now}")
    print("=" * 60)

    # Task count
    print(f"\n  📊 Tasks Tracked: {task_stats['count']}")

    if task_stats["count"] == 0:
        print("\n  No pipeline metrics collected yet.")
        print("  Metrics are logged automatically after each dispatch cycle.")
        print("=" * 60)
        return

    # Per-task metrics
    print("\n── Per-Task Metrics (averages) ──")
    print(f"  ⏱️  Time to self-validate:  {task_stats['time_to_self_validate']['avg']} min (range: {task_stats['time_to_self_validate']['min']}–{task_stats['time_to_self_validate']['max']})")
    print(f"  🔍 Peer review depth:       {task_stats['peer_review_depth']['avg']} findings/100 lines")
    print(f"  🔄 Fix cycle count:         {task_stats['fix_cycle_count']['avg']}")
    print(f"  ⏰ Time to approval:        {task_stats['time_to_approval']['avg']} min")
    print(f"  💰 Credit cost:             {task_stats['credit_cost']['avg']} avg ({task_stats['credit_cost']['total']} total)")

    # Provider breakdown
    providers = task_stats.get("providers", {})
    if providers:
        prov_str = ", ".join(f"{p}: {c}" for p, c in providers.items())
        print(f"  🤖 Providers:               {prov_str}")

    # Per-session metrics
    print(f"\n── Per-Session Metrics ──")
    print(f"  📅 Sessions:                {session_stats['sessions']}")
    print(f"  ✅ Total completed:         {session_stats['total_completed']}")
    print(f"  🎯 Total attempted:         {session_stats['total_attempted']}")
    print(f"  ✔️  Acceptance rate:         {session_stats['avg_acceptance_rate']:.0%}")
    print(f"  💳 Credit/task:             {session_stats['avg_credit_per_task']} credits")

    # Daily breakdown
    daily = session_stats.get("daily", {})
    if len(daily) > 1:
        print(f"\n── Daily Breakdown ──")
        for date_key, sess in sorted(daily.items()):
            bar = "█" * min(sess["completed"], 20)
            print(f"  {date_key}: {bar} {sess['completed']} done, {sess['acceptance_rate']:.0%} acceptance, {sess['total_credits']} credits")

    # Bypasses
    if bypasses:
        print(f"\n── ⚠️ Pipeline Bypasses: {len(bypasses)} ──")
        for bid in bypasses[:10]:
            print(f"  ⚠️ {bid}")

    # Health score
    score = compute_health_score(task_stats, session_stats, bypasses)
    icon = "🟢" if score >= 80 else "🟡" if score >= 50 else "🔴"
    print(f"\n── Pipeline Health: {icon} {score}/100 ──")

    print("=" * 60)


def compute_health_score(task_stats, session_stats, bypasses):
    """Compute a 0-100 health score."""
    if task_stats["count"] == 0:
        return 0

    score = 100

    # Penalize for low acceptance rate
    acc = session_stats.get("avg_acceptance_rate", 1)
    if acc < 0.5:
        score -= 25
    elif acc < 0.8:
        score -= 10

    # Penalize for bypasses
    bypass_count = len(bypasses)
    if bypass_count > 0:
        score -= min(bypass_count * 5, 30)

    # Penalize for high fix cycle count
    cycles = task_stats.get("fix_cycle_count", {}).get("avg", 1)
    if cycles > 3:
        score -= 15
    elif cycles > 2:
        score -= 5

    # Penalize for high credit cost
    credit_avg = task_stats.get("credit_cost", {}).get("avg", 0)
    if credit_avg > 5:
        score -= 10

    return max(0, score)


def json_output(metrics_list):
    """Output full dashboard as JSON (for golden thread integration)."""
    task_stats = compute_task_stats(metrics_list)
    session_stats = compute_session_stats(metrics_list)
    bypasses = detect_bypasses(metrics_list)
    score = compute_health_score(task_stats, session_stats, bypasses)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "health_score": score,
        "task_metrics": task_stats,
        "session_metrics": session_stats,
        "bypasses": bypasses,
    }
    print(json.dumps(output, indent=2, default=str))


def summary_output(metrics_list):
    """Single-line summary for daily golden thread review."""
    task_stats = compute_task_stats(metrics_list)
    session_stats = compute_session_stats(metrics_list)
    bypasses = detect_bypasses(metrics_list)
    score = compute_health_score(task_stats, session_stats, bypasses)
    icon = "🟢" if score >= 80 else "🟡" if score >= 50 else "🔴"

    if task_stats["count"] == 0:
        print(f"📊 Pipeline: No metrics yet")
    else:
        print(
            f"📊 Pipeline: {icon} {score}/100 | "
            f"{task_stats['count']} tasks | "
            f"{session_stats['avg_acceptance_rate']:.0%} acceptance | "
            f"{task_stats['credit_cost']['total']} credits | "
            f"{len(bypasses)} bypasses"
        )


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    metrics = load_metrics()

    if "--json" in sys.argv:
        json_output(metrics)
    elif "--summary" in sys.argv:
        summary_output(metrics)
    else:
        print_dashboard(metrics)
