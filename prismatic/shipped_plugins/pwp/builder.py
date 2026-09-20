"""
prismatic-web-plugin builder

The "system" that ties together the 3 PWP steps (ingest, synthesize, distill)
with the agent swarm (dispatch, build, review, deploy) and the post-build
actions (CF Pages deploy, OKF handoff).

This is the durable, generalized PWP — works on ANY client's framework
docs, not just Meridian.

Usage:
    # Full pipeline (new client)
    python -m prismatic_web_publisher.builder run --client meridian-womens-defense

    # Watch a running build
    python -m prismatic_web_publisher.builder watch --epic GRO-2142

    # Report build status
    python -m prismatic_web_publisher.builder status --epic GRO-2142
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The 3 pipeline steps live in this package as library functions.
# We import + call them directly (no subprocess). This is faster, testable,
# and lets the orchestrator see structured return values instead of parsing CLI output.

from .ingest import run_ingest
from .synthesize import run_synthesize
from .distill import run_distill

PWP_HOME = Path(os.environ.get("PWP_HOME", "/home/ubuntu/work/prismatic-web-plugin"))
OUTPUT_BASE = PWP_HOME / "output"
PROJECTS_BASE = Path(os.environ.get("PWP_PROJECTS_BASE", "/home/ubuntu/work"))
SRC_DIR = PWP_HOME / "src"


class PrismaticWebBuilder:
    """Class wrapper for PWP pipeline operations."""

    def __init__(self, pwp_home: str | Path | None = None):
        self.pwp_home = Path(pwp_home) if pwp_home else PWP_HOME

    def run(self, client_slug: str, *, skip_agy: bool = False, dry_run: bool = False) -> dict[str, Any]:
        return run_pipeline(client_slug, skip_agy=skip_agy, dry_run=dry_run)

    def watch(self, epic_id: str, *, poll_interval: int = 60, max_runtime: int = 86400) -> int:
        return watch_epic(epic_id, poll_interval=poll_interval, max_runtime=max_runtime)

    def status(self, epic_id: str) -> int:
        return print_status(epic_id)


def load_env():

    """Load .env from the orchestrator profile.""" # hermes orchestrator profile, not this CLI
    env_path = Path("/home/ubuntu/.hermes/profiles/orchestrator/.env")
    if not env_path.exists():
        return {}
    env = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def log(stage: str, msg: str, **fields):
    """Structured log line."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extras = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[{ts}] [{stage}] {msg} {extras}".strip(), flush=True)


def run_pipeline(client_slug: str, *, skip_agy: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Run the full PWP pipeline: ingest → synthesize → distill.

    Args:
        client_slug: directory name in okf/projects/ that holds the 5 Website Dev docs
        skip_agy: skip the AGY call in synthesize (faster, lower quality)
        dry_run: don't write any output files or Linear issues

    Returns:
        dict with keys: status, client_slug, outputs, epic_id, child_ids, errors
    """
    result = {
        "client_slug": client_slug,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "stages": {},
    }

    output_dir = OUTPUT_BASE / client_slug
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine the input directory
    okf_projects = Path("/home/ubuntu/work/growthwebdev-knowledge/okf/projects")
    docs_dir = okf_projects / client_slug

    # Step 1: Ingest (library call)
    log("pipeline", f"Step 1: Ingest for {client_slug}")
    try:
        ingest_result = run_ingest(str(docs_dir), output_dir=output_dir, dry_run=dry_run)
        if ingest_result["status"] == "error":
            raise RuntimeError(ingest_result.get("error"))
        result["stages"]["ingest"] = {
            "status": "ok",
            "output": ingest_result["paths"],
            "missing_fields": ingest_result.get("missing_fields", []),
        }
        if ingest_result["status"] == "partial":
            log("ingest", f"Partial: {len(ingest_result['missing_fields'])} missing fields")
    except Exception as e:
        log("ingest", f"FAILED: {e}")
        result["stages"]["ingest"] = {"status": "error", "error": str(e)}
        result["status"] = "failed"
        return result

    # Step 2: Synthesize (library call)
    log("pipeline", f"Step 2: Synthesize for {client_slug}")
    try:
        profile_path = output_dir / "client_profile.json"
        synth_result = run_synthesize(profile_path, output_dir=output_dir, skip_agy=skip_agy)
        if synth_result["status"] == "error":
            raise RuntimeError(synth_result.get("error"))
        result["stages"]["synthesize"] = {
            "status": "ok",
            "word_count": synth_result["word_count"],
            "path": synth_result["path"],
        }
    except Exception as e:
        log("synthesize", f"FAILED: {e}")
        result["stages"]["synthesize"] = {"status": "error", "error": str(e)}
        result["status"] = "failed"
        return result

    # Step 3: Distill (library call)
    log("pipeline", f"Step 3: Distill for {client_slug}")
    try:
        plan_path = output_dir / "website_build_plan.md"
        distill_result = run_distill(plan_path, dry_run=dry_run)
        if distill_result["status"] == "error":
            raise RuntimeError(distill_result.get("error"))
        result["stages"]["distill"] = {
            "status": "ok",
            "epic_id": distill_result["epic_id"],
            "child_ids": distill_result.get("child_ids", []),
        }
        result["epic_id"] = distill_result["epic_id"]
        result["child_ids"] = distill_result.get("child_ids", [])
    except Exception as e:
        log("distill", f"FAILED: {e}")
        result["stages"]["distill"] = {"status": "error", "error": str(e)}
        result["status"] = "failed"
        return result

    result["status"] = "ok"
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    log("pipeline", f"DONE: epic={result.get('epic_id')} children={len(result.get('child_ids', []))}")
    return result


def watch_epic(epic_id: str, *, poll_interval: int = 60, max_runtime: int = 86400):
    """Watch a Linear epic until all children are Done (or max_runtime).

    Posts a comment on the epic with the progress every 10 minutes.
    """
    log("watch", f"Starting watch on {epic_id} (poll={poll_interval}s, max={max_runtime}s)")
    env = load_env()
    api_key = env.get("LINEAR_API_KEY")
    if not api_key:
        log("watch", "ERROR: LINEAR_API_KEY not set")
        return 1

    start = time.time()
    last_comment = 0.0
    last_status = None
    while time.time() - start < max_runtime:
        try:
            status = _epic_status(epic_id, api_key)
        except Exception as e:
            log("watch", f"ERROR fetching status: {e}")
            time.sleep(poll_interval)
            continue

        if status != last_status:
            log("watch", f"Status: {status}")
            last_status = status

        # Post progress comment every 10 min
        if time.time() - last_comment > 600:
            try:
                _post_progress_comment(epic_id, status, api_key)
                last_comment = time.time()
            except Exception as e:
                log("watch", f"ERROR posting comment: {e}")

        # Done?
        if all(c["state"] in ("Done", "Canceled") for c in status.get("children", [])):
            log("watch", f"All children terminal. Watch ending.")
            return 0

        time.sleep(poll_interval)

    log("watch", f"Max runtime {max_runtime}s reached. Ending watch.")
    return 2


def _epic_status(epic_id: str, api_key: str) -> dict[str, Any]:
    """Fetch epic + children status from Linear."""
    import urllib.request
    q = """
    query($id: String!) {
      issue(id: $id) {
        identifier
        title
        state { name }
        children {
          nodes { identifier title state { name } }
        }
      }
    }
    """
    r = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": q, "variables": {"id": epic_id}}).encode(),
        headers={"Authorization": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=20) as f:
        data = json.loads(f.read())
    issue = data["data"]["issue"]
    children = issue["children"]["nodes"]
    states = [c["state"]["name"] for c in children]
    return {
        "identifier": issue["identifier"],
        "title": issue["title"],
        "state": issue["state"]["name"],
        "children": [{"id": c["identifier"], "title": c["title"], "state": c["state"]["name"]} for c in children],
        "summary": {
            "total": len(children),
            "todo": states.count("Todo"),
            "in_progress": states.count("In Progress"),
            "done": states.count("Done"),
            "canceled": states.count("Canceled"),
            "backlog": states.count("Backlog"),
        }
    }


def _post_progress_comment(epic_id: str, status: dict, api_key: str):
    import urllib.request
    summary = status["summary"]
    total = summary["total"]
    done = summary["done"]
    in_prog = summary["in_progress"]
    pct = int(100 * done / total) if total else 0
    body = f"""PWP builder status update:

Progress: {done}/{total} children done ({pct}%)
In Progress: {in_prog}

```
{json.dumps(summary, indent=2)}
```

Auto-posted by prismatic-web-plugin builder."""
    q = """
    mutation($id: String!, $body: String!) {
      commentCreate(input: { issueId: $id, body: $body }) { success }
    }
    """
    r = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": q, "variables": {"id": epic_id, "body": body}}).encode(),
        headers={"Authorization": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=20) as f:
        f.read()


def print_status(epic_id: str):
    env = load_env()
    api_key = env.get("LINEAR_API_KEY")
    if not api_key:
        print("ERROR: LINEAR_API_KEY not set")
        return 1
    status = _epic_status(epic_id, api_key)
    print(f"Epic: {status['identifier']} - {status['title']}")
    print(f"State: {status['state']}")
    print()
    print(f"Children: {status['summary']}")
    print()
    for c in status["children"]:
        print(f"  [{c['state']:<11}]  {c['id']}  {c['title'][:60]}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="PWP builder (pwb)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full PWP pipeline")
    p_run.add_argument("--client", required=True, help="Client slug (e.g. meridian-womens-defense)")
    p_run.add_argument("--skip-agy", action="store_true", help="Skip AGY calls in synthesize")
    p_run.add_argument("--dry-run", action="store_true", help="Don't write files or create Linear issues")

    p_watch = sub.add_parser("watch", help="Watch a Linear epic")
    p_watch.add_argument("--epic", required=True, help="Epic UUID (e.g. from distill output)")
    p_watch.add_argument("--interval", type=int, default=60, help="Poll interval (seconds)")
    p_watch.add_argument("--max-runtime", type=int, default=86400, help="Max runtime (seconds)")

    p_status = sub.add_parser("status", help="Print epic status")
    p_status.add_argument("--epic", required=True, help="Epic UUID")

    args = parser.parse_args()

    if args.command == "run":
        result = run_pipeline(args.client, skip_agy=args.skip_agy, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "ok" else 1
    elif args.command == "watch":
        return watch_epic(args.epic, poll_interval=args.interval, max_runtime=args.max_runtime)
    elif args.command == "status":
        return print_status(args.epic)


if __name__ == "__main__":
    sys.exit(main())
