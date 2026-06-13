"""
Orchestrator Command Deck Backend API (GRO-68)

Provides swarm status data for the Orchestrator Command Deck dashboard plugin:
- Swarm agent status (Sentinel, AGY, Jules, Codex, Hermes)
- Active task counts and session budgets
- System health and resource snapshots
- State persistence for controls, queue, and logs
"""

import json
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

CACHE_TTL = 5  # seconds
_cache: dict = {"data": None, "ts": 0}

HERMES_PROFILE = "orchestrator"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
STATE_FILE = HERMES_HOME / "profiles" / HERMES_PROFILE / "command_deck_state.json"

DEFAULT_STATE = {
    "mode": "interactive",
    "agents": {
        "Antigravity": { "name": "Antigravity", "role": "Main Spoke CLI", "status": "Running", "task": "Resolving Linear Issue GRO-1222", "color": "var(--color-antigravity)", "glow": "rgba(243, 156, 18, 0.15)" },
        "Jules": { "name": "Jules", "role": "Async Git & PR Agent", "status": "Idle", "task": "Idle · Listening to Linear webhooks", "color": "var(--color-jules)", "glow": "rgba(230, 126, 34, 0.15)" },
        "Codex": { "name": "Codex", "role": "Code reviewer specialist", "status": "Paused", "task": "Awaiting safe-directory approvals", "color": "var(--color-codex)", "glow": "rgba(231, 76, 60, 0.15)" },
        "Kai": { "name": "Kai", "role": "K3s cluster balancer", "status": "Running", "task": "Monitoring Sovereign Sentinel states", "color": "var(--color-kai)", "glow": "rgba(46, 204, 113, 0.15)" },
        "Fred": { "name": "Fred", "role": "Deployment staging gatekeeper", "status": "Idle", "task": "Idle · Awaiting staging run signals", "color": "var(--color-fred)", "glow": "rgba(52, 152, 219, 0.15)" },
        "Ned": { "name": "Ned", "role": "Swarm research synthesizer", "status": "Idle", "task": "Idle · Compiling logs backup", "color": "var(--color-ned)", "glow": "rgba(155, 89, 182, 0.15)" }
    },
    "tasks": [
        { "id": 1, "agent": "Antigravity", "desc": "Implement high-fidelity HTML command deck for command-deck plugin", "priority": "Critical", "age": 2 },
        { "id": 2, "agent": "Jules", "desc": "Sync local git safe.directory configurations with swarm profiles", "priority": "High", "age": 8 },
        { "id": 3, "agent": "Kai", "desc": "Check K3s namespaces for stale agent-worker containers", "priority": "Medium", "age": 15 },
        { "id": 4, "agent": "Codex", "desc": "Verify changes in PR #42 against security policies", "priority": "Low", "age": 34 }
    ],
    "logs": {}
}

def _seed_logs(logs_dict: dict):
    import random
    levels = ["INFO", "SUCCESS", "WARN"]
    actions = {
        "Antigravity": ["Command resolved successfully", "Permission safe check: ok", "Broadcasted tab-sync composer state change", "Reading manifest.json under orchestrator-command-deck"],
        "Jules": ["Checking commits on dev-branch", "Linear event match detected: GRO-123", "Rebasing workspace logs to local backup", "Git fetch complete: origin"],
        "Codex": ["Parsing security abstract syntax trees", "Safe directory audit: no violations found", "Review completed for diff PR #12", "VRAM watchdog check: 24% capacity"],
        "Kai": ["Autonomously balancing resource threads", "Sentinel check: heartbeat response in 12ms", "Flushing SQLite cached event log keys", "K3s cluster telemetry matched baseline"],
        "Fred": ["Listening for local package release hook", "Staging container setup: verified safe", "Pty keepalive timeout updated to 120m", "Safe gate checks passed"],
        "Ned": ["Research query finalized: Safe directory exceptions", "Synthesizing report: agent-runs output", "Linear ticket GRO-1222 re-labelled to agent:ned", "Archived legacy configs"]
    }
    for agent in ["Antigravity", "Jules", "Codex", "Kai", "Fred", "Ned"]:
        if agent not in logs_dict or not logs_dict[agent]:
            logs_dict[agent] = []
            base_time = datetime.now(timezone.utc)
            for i in range(35):
                offset = (35 - i) * 45
                log_time = base_time.timestamp() - offset
                time_str = datetime.fromtimestamp(log_time, timezone.utc).strftime("%H:%M:%S")
                lvl = random.choice(levels)
                act = actions[agent][i % len(actions[agent])] + f" ({i})"
                logs_dict[agent].append({"time": time_str, "level": lvl, "text": f"[{lvl}] {act}"})

def _read_state() -> dict:
    if not STATE_FILE.exists():
        state = dict(DEFAULT_STATE)
        state["logs"] = {}
        _seed_logs(state["logs"])
        _write_state(state)
        return state
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
            if "logs" not in state or not state["logs"]:
                state["logs"] = {}
                _seed_logs(state["logs"])
                _write_state(state)
            return state
    except Exception:
        state = dict(DEFAULT_STATE)
        state["logs"] = {}
        _seed_logs(state["logs"])
        return state

def _write_state(state: dict):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def _invalidate_cache():
    _cache["ts"] = 0
    _cache["data"] = None


def _get_swarm_status() -> dict:
    """Collect swarm agent and system status."""
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]

    status = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_sync": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "sentinel_active": False,
        "agents": {},
        "active_tasks": 0,
        "session_budgets": {},
        "system": {},
    }

    # Read persistent state
    state = _read_state()
    status["mode"] = state.get("mode", "interactive")
    status["tasks"] = state.get("tasks", [])

    # ── Check running agent processes ────────────────────────
    agent_patterns = {
        "sentinel": ["hermes", "gateway"],
        "hermes": ["hermes", "dashboard"],
        "agy": ["agy-bin", "antigravity"],
        "jules": ["jules"],
        "codex": ["codex"],
        "kai": ["kai"],
        "fred": ["fred"],
        "ned": ["ned"],
    }

    try:
        result = subprocess.run(
            ["ps", "aux", "--no-headers"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            cmd_lower = line.lower()
            for agent_name, patterns in agent_patterns.items():
                if all(p in cmd_lower for p in patterns):
                    if agent_name not in status["agents"]:
                        status["agents"][agent_name] = {
                            "running": True,
                            "processes": 0,
                            "total_cpu": 0.0,
                            "total_mem_mb": 0.0,
                        }
                    status["agents"][agent_name]["processes"] += 1
                    parts = line.split(None, 10)
                    if len(parts) >= 4:
                        try:
                            status["agents"][agent_name]["total_cpu"] += float(parts[2])
                        except ValueError:
                            pass
                    if len(parts) >= 5:
                        try:
                            status["agents"][agent_name]["total_mem_mb"] += float(parts[3])
                        except ValueError:
                            pass
                    break
    except Exception:
        pass

    # ── Sentinel status ─────────────────────────────────────
    sentinel = status["agents"].get("sentinel", {})
    status["sentinel_active"] = sentinel.get("running", False)
    status["sentinel"] = {
        "active": status["sentinel_active"],
        "processes": sentinel.get("processes", 0),
        "cpu_pct": round(sentinel.get("total_cpu", 0), 1),
        "mem_mb": round(sentinel.get("total_mem_mb", 0), 1),
    }

    # ── Jules session budget ────────────────────────────────
    jules = status["agents"].get("jules", {})
    jules_count = jules.get("processes", 0)
    status["session_budgets"]["jules"] = {
        "active_sessions": jules_count,
        "daily_limit": 300,
        "pct_used": round(jules_count / 300 * 100, 1) if jules_count else 0,
    }

    # ── Active task count ───────────────────
    status["active_tasks"] = len(status["tasks"])

    # ── Agent status summary ────────────────────────────────
    status["agents_summary"] = {
        name: {
            "running": info.get("running", False),
            "processes": info.get("processes", 0),
            "cpu_pct": round(info.get("total_cpu", 0), 1),
            "mem_mb": round(info.get("total_mem_mb", 0), 1),
        }
        for name, info in status["agents"].items()
    }
    # Ensure all expected agents show up even if not running
    for name in ["hermes", "agy", "jules", "codex", "fred", "ned", "kai"]:
        if name not in status["agents_summary"]:
            status["agents_summary"][name] = {
                "running": False,
                "processes": 0,
                "cpu_pct": 0.0,
                "mem_mb": 0.0,
            }

    # Merge status with ui_agents
    agent_key_to_ps = {
        "Antigravity": "agy",
        "Jules": "jules",
        "Codex": "codex",
        "Kai": "kai",
        "Fred": "fred",
        "Ned": "ned"
    }

    ui_agents = {}
    for key, agent_info in state.get("agents", {}).items():
        ps_key = agent_key_to_ps.get(key)
        scanned_info = status["agents"].get(ps_key, {}) if ps_key else {}
        
        ui_agents[key] = {
            "name": agent_info["name"],
            "role": agent_info["role"],
            "status": agent_info["status"],
            "task": agent_info["task"],
            "color": agent_info["color"],
            "glow": agent_info["glow"],
            "cpu_pct": round(scanned_info.get("total_cpu", 0.0), 1),
            "mem_mb": round(scanned_info.get("total_mem_mb", 0.0), 1),
            "processes": scanned_info.get("processes", 0)
        }
    status["ui_agents"] = ui_agents

    # ── System resources ────────────────────────────────────
    try:
        load = os.getloadavg()
        status["system"]["load"] = {
            "1m": round(load[0], 2),
            "5m": round(load[1], 2),
            "15m": round(load[2], 2),
        }
    except Exception:
        status["system"]["load"] = {"1m": 0, "5m": 0, "15m": 0}

    try:
        mem = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
        mem_lines = mem.stdout.strip().splitlines()
        if len(mem_lines) > 1:
            parts = mem_lines[1].split()
            if len(parts) >= 7:
                status["system"]["memory"] = {
                    "total_mb": int(parts[1]),
                    "used_mb": int(parts[2]),
                    "free_mb": int(parts[3]),
                    "pct_used": round(int(parts[2]) / max(int(parts[1]), 1) * 100, 1),
                }
    except Exception:
        status["system"]["memory"] = {"total_mb": 0, "used_mb": 0, "free_mb": 0, "pct_used": 0}

    _cache["data"] = status
    _cache["ts"] = now
    return status


@router.get("/status")
async def get_status():
    """Return full swarm orchestration status."""
    return _get_swarm_status()


@router.post("/mode")
async def set_mode(request: Request):
    body = await request.json()
    mode = body.get("mode")
    if mode not in ["interactive", "collaborative", "autonomous"]:
        raise HTTPException(status_code=400, detail="Invalid mode")
    state = _read_state()
    state["mode"] = mode
    
    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    log_text = f"[WARN] System mode switched to: {mode.upper()}"
    state.setdefault("logs", {}).setdefault("Antigravity", []).append({
        "time": time_str,
        "level": "WARN",
        "text": log_text
    })
    state["logs"]["Antigravity"] = state["logs"]["Antigravity"][-50:]
    _write_state(state)
    _invalidate_cache()
    return {"success": True, "mode": mode}


@router.post("/agent/{agent_name}/control")
async def control_agent(agent_name: str, request: Request):
    body = await request.json()
    action = body.get("action")
    if action not in ["start", "pause", "resume", "kill"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    state = _read_state()
    
    agent_key = agent_name.capitalize()
    if agent_key == "Agy":
        agent_key = "Antigravity"
    
    agent = state.setdefault("agents", {}).get(agent_key)
    if not agent:
        # Check case insensitively
        found = False
        for k in list(state["agents"].keys()):
            if k.lower() == agent_name.lower():
                agent = state["agents"][k]
                agent_key = k
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail=f"Agent {agent_name} not found")
            
    if action == "start":
        agent["status"] = "Running"
        agent["task"] = "Initializing active loop context..."
        level = "SUCCESS"
    elif action == "pause":
        agent["status"] = "Paused"
        level = "WARN"
    elif action == "resume":
        agent["status"] = "Running"
        level = "SUCCESS"
    elif action == "kill":
        agent["status"] = "Terminated"
        agent["task"] = "Terminated by operator signal"
        level = "ERROR"
        
    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    log_text = f"[{level}] Lifecycle status transitioned to: {action.upper()} via Command Deck"
    state.setdefault("logs", {}).setdefault(agent_key, []).append({
        "time": time_str,
        "level": level,
        "text": log_text
    })
    state["logs"][agent_key] = state["logs"][agent_key][-50:]
    
    _write_state(state)
    _invalidate_cache()
    return {"success": True, "agent": agent_key, "status": agent["status"]}


@router.post("/tasks/dispatch")
async def dispatch_task(request: Request):
    body = await request.json()
    agent_name = body.get("agent")
    desc = body.get("description")
    priority = body.get("priority")
    
    if not agent_name or not desc or not priority:
        raise HTTPException(status_code=400, detail="Missing required task fields")
        
    state = _read_state()
    new_task = {
        "id": int(time.time() * 1000),
        "agent": agent_name,
        "desc": desc,
        "priority": priority,
        "age": 0
    }
    state.setdefault("tasks", []).append(new_task)
    
    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    log_text = f"[INFO] Received new Sandbox Dispatch command: \"{desc}\""
    state.setdefault("logs", {}).setdefault(agent_name, []).append({
        "time": time_str,
        "level": "INFO",
        "text": log_text
    })
    state["logs"][agent_name] = state["logs"][agent_name][-50:]
    
    _write_state(state)
    _invalidate_cache()
    return {"success": True, "task": new_task}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int):
    state = _read_state()
    tasks = state.setdefault("tasks", [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    state["tasks"] = [t for t in tasks if t["id"] != task_id]
    
    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    agent_name = task["agent"]
    log_text = "[WARN] Canceled queue task manually by user request"
    state.setdefault("logs", {}).setdefault(agent_name, []).append({
        "time": time_str,
        "level": "WARN",
        "text": log_text
    })
    state["logs"][agent_name] = state["logs"][agent_name][-50:]
    
    _write_state(state)
    _invalidate_cache()
    return {"success": True}


@router.post("/tasks/{task_id}/reorder")
async def reorder_task(task_id: int, request: Request):
    body = await request.json()
    direction = body.get("direction")
    if direction not in ["up", "down"]:
        raise HTTPException(status_code=400, detail="Invalid direction")
        
    state = _read_state()
    tasks = state.setdefault("tasks", [])
    idx = next((i for i, t in enumerate(tasks) if t["id"] == task_id), -1)
    if idx == -1:
        raise HTTPException(status_code=404, detail="Task not found")
        
    shift = -1 if direction == "up" else 1
    target_idx = idx + shift
    if 0 <= target_idx < len(tasks):
        tasks[idx], tasks[target_idx] = tasks[target_idx], tasks[idx]
        
    _write_state(state)
    _invalidate_cache()
    return {"success": True, "tasks": tasks}


@router.get("/agent/{agent_name}/logs")
async def get_agent_logs(agent_name: str, limit: int = 50):
    state = _read_state()
    agent_key = agent_name.capitalize()
    if agent_key == "Agy":
        agent_key = "Antigravity"
    logs_dict = state.setdefault("logs", {})
    
    agent_logs = logs_dict.get(agent_key, logs_dict.get(agent_name, []))
    return agent_logs[-limit:]


@router.post("/agent/{agent_name}/logs")
async def add_agent_log(agent_name: str, request: Request):
    body = await request.json()
    level = body.get("level", "INFO").upper()
    text = body.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
        
    state = _read_state()
    agent_key = agent_name.capitalize()
    if agent_key == "Agy":
        agent_key = "Antigravity"
        
    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    state.setdefault("logs", {}).setdefault(agent_key, []).append({
        "time": time_str,
        "level": level,
        "text": text
    })
    state["logs"][agent_key] = state["logs"][agent_key][-50:]
    _write_state(state)
    _invalidate_cache()
    return {"success": True}


@router.get("/health")
async def health():
    return {"status": "ok", "plugin": "hermes-plugin-orchestrator-command-deck"}
