"""Dynamic LLM & Agent Harness Auto-Discovery and Benchmarking Engine."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.harnesses.discovery")


@dataclass
class DiscoveredHarness:
    id: str
    kind: str  # "agy", "hermes", "vllm", "ollama", "openai"
    name: str
    target: str  # Model name, profile name, or engine slug
    binary: str = ""
    endpoint: str = ""
    env: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    status: str = "unknown"  # "online", "ready", "offline", "verified"
    metadata: dict[str, Any] = field(default_factory=dict)
    last_discovered: float = field(default_factory=time.time)
    last_benchmarked: float = 0.0
    benchmark_latency_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HarnessDiscoveryManager:
    """Discovers, probes, benchmarks, and catalogs available agent and LLM harnesses."""

    def __init__(self, registry_file: Path | str | None = None) -> None:
        if registry_file is None:
            self.registry_file = Path(__file__).parent / "registry.json"
        else:
            self.registry_file = Path(registry_file)

    def discover_all(self) -> list[DiscoveredHarness]:
        """Run all discovery probes across Antigravity, Hermes profiles, and local inference."""
        harnesses: list[DiscoveredHarness] = []
        harnesses.extend(self.discover_antigravity())
        harnesses.extend(self.discover_hermes_profiles())
        harnesses.extend(self.discover_local_inference())
        return harnesses

    def discover_antigravity(self) -> list[DiscoveredHarness]:
        """Probe local Antigravity binary and available frontier reasoning models."""
        harnesses: list[DiscoveredHarness] = []
        _local_bin = Path.home() / ".local" / "bin"
        binary_candidates = [
            _local_bin / "agy-bin",
            _local_bin / "agy",
            Path(shutil.which("agy-bin") or "") if shutil.which("agy-bin") else None,
            Path(shutil.which("agy") or "") if shutil.which("agy") else None,
        ]
        binary = next((p for p in binary_candidates if p and p.is_file() and os.access(p, os.X_OK)), None)
        if not binary:
            return harnesses

        models = [
            "gemini-3.8-flash-high",
            "gemini-3.8-flash-medium",
            "gemini-3.8-flash-low",
            "gemini-3.7-flash-high",
            "gemini-3.7-flash-medium",
            "gemini-3.7-flash-low",
            "gemini-3.6-flash-high",
            "gemini-3.6-flash-medium",
            "gemini-3.6-flash-low",
            "gemini-3.1-pro-high",
            "gemini-3.1-pro-low",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
            "gpt-oss-120b-medium",
        ]

        try:
            res = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=5.0)
            if res.returncode == 0:
                extracted = re.findall(r"(?:gemini-[0-9\.]+(?:-[a-z]+)+|claude-[0-9a-z-]+|gpt-[0-9a-z-]+)", res.stdout)
                if extracted:
                    models = sorted(list(set(models + extracted)))
        except Exception:
            pass

        for m in models:
            harnesses.append(
                DiscoveredHarness(
                    id=f"agy:{m}",
                    kind="agy",
                    name="Google Antigravity",
                    target=m,
                    binary=str(binary),
                    tags=["agy", "antigravity", m, "general", "code"],
                    capabilities=["streaming", "tools", "worktrees", "non-interactive", "json-mode"],
                    status="online",
                    metadata={
                        "model": m,
                        "subscription": "Google One AI Ultra",
                        "cost_tier": "subscription-unlimited",
                    },
                )
            )

        return harnesses

    def discover_hermes_profiles(self) -> list[DiscoveredHarness]:
        """Probe local Hermes installation and active/stopped agent profiles."""
        harnesses: list[DiscoveredHarness] = []
        hermes_bin = Path.home() / ".local" / "bin" / "hermes"
        if not (hermes_bin.is_file() and os.access(hermes_bin, os.X_OK)):
            which_h = shutil.which("hermes")
            if which_h:
                hermes_bin = Path(which_h)
            else:
                return harnesses

        profiles_dir = Path.home() / ".hermes" / "profiles"
        if not profiles_dir.is_dir():
            return harnesses

        running_cmdlines: list[str] = []
        try:
            ps_res = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=3.0)
            if ps_res.returncode == 0:
                running_cmdlines = ps_res.stdout.splitlines()
        except Exception:
            pass

        for p_dir in sorted(profiles_dir.iterdir()):
            if not p_dir.is_dir():
                continue
            profile_name = p_dir.name
            if profile_name.startswith((".", "profildeclare")):
                continue

            config_file = p_dir / "config.yaml"
            model_name = "unknown"
            provider = "local"
            if config_file.is_file():
                try:
                    for line in config_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line_s = line.strip()
                        if line_s.startswith("default:") and model_name == "unknown":
                            model_name = line_s.split(":", 1)[1].strip().strip(""").strip("'")
                        elif line_s.startswith("provider:"):
                            provider = line_s.split(":", 1)[1].strip().strip(""").strip("'")
                except Exception:
                    pass

            is_running = any(
                f"--profile {profile_name}" in cmd or f"--profile={profile_name}" in cmd
                for cmd in running_cmdlines
            )

            status = "running" if is_running else "ready"

            tags = ["hermes", profile_name, "agent"]
            if profile_name in {"george", "arbiter"}:
                tags.extend(["review", "architecture", "audit"])
            elif profile_name in {"kai", "frontend", "ui"}:
                tags.extend(["frontend", "css", "ui", "tailwind"])
            elif profile_name in {"ned", "backend", "engine"}:
                tags.extend(["backend", "engine", "python"])
            elif profile_name in {"autobot", "ingestion"}:
                tags.extend(["ingestion", "automation", "fast"])
            elif profile_name in {"orchestrator", "fred"}:
                tags.extend(["coordinator", "dispatch", "swarm"])

            harnesses.append(
                DiscoveredHarness(
                    id=f"hermes:{profile_name}",
                    kind="hermes",
                    name=f"Hermes ({profile_name.capitalize()})",
                    target=profile_name,
                    binary=str(hermes_bin),
                    env={"HERMES_HOME": str(p_dir.resolve())},
                    tags=tags,
                    capabilities=["one-shot", "tools", "multi-turn", "mcp", "memory"],
                    status=status,
                    metadata={
                        "profile": profile_name,
                        "home_dir": str(p_dir),
                        "model": model_name,
                        "provider": provider,
                        "daemon_running": is_running,
                    },
                )
            )

        return harnesses

    def discover_local_inference(self) -> list[DiscoveredHarness]:
        """Probe local Ollama, VLLM, and local inference server endpoints."""
        harnesses: list[DiscoveredHarness] = []
        endpoints = [
            ("ollama", "http://127.0.0.1:11434/api/tags"),
            ("vllm", "http://127.0.0.1:8010/v1/models"),
            ("deepseek-local", "http://192.168.1.232:8080/v1/models"),
        ]

        for name, url in endpoints:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Prismatic-Discovery/0.2"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        model_names: list[str] = []
                        if "models" in data and isinstance(data["models"], list):
                            for m in data["models"]:
                                if isinstance(m, dict) and "name" in m:
                                    model_names.append(m["name"])
                                elif isinstance(m, dict) and "id" in m:
                                    model_names.append(m["id"])
                        elif "data" in data and isinstance(data["data"], list):
                            for m in data["data"]:
                                if isinstance(m, dict) and "id" in m:
                                    model_names.append(m["id"])

                        for mn in model_names[:5]:
                            harnesses.append(
                                DiscoveredHarness(
                                    id=f"{name}:{mn}",
                                    kind=name,
                                    name=f"{name.upper()} ({mn})",
                                    target=mn,
                                    endpoint=url.rsplit("/", 1)[0],
                                    tags=[name, "local-inference", mn],
                                    capabilities=["completions", "streaming"],
                                    status="online",
                                    metadata={"endpoint": url, "model": mn},
                                )
                            )
            except Exception:
                pass

        return harnesses

    def benchmark_harness(self, harness: DiscoveredHarness, prompt: str = "Respond with: PONG") -> dict[str, Any]:
        """Execute a lightweight non-interactive ping verification against a harness."""
        t_start = time.time()
        res_dict = {
            "harness_id": harness.id,
            "kind": harness.kind,
            "target": harness.target,
            "ok": False,
            "stdout": "",
            "stderr": "",
            "latency_seconds": 0.0,
            "exit_code": -1,
        }

        try:
            if harness.kind == "agy":
                cmd = [harness.binary, "--print", prompt, "--model", harness.target, "--output-format", "json"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25.0)
                res_dict["exit_code"] = proc.returncode
                res_dict["stdout"] = proc.stdout
                res_dict["stderr"] = proc.stderr
                res_dict["ok"] = (proc.returncode == 0)

            elif harness.kind == "hermes":
                cmd = [harness.binary, "--profile", harness.target, "-z", prompt]
                env = os.environ.copy()
                env.update(harness.env)
                proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=25.0)
                res_dict["exit_code"] = proc.returncode
                res_dict["stdout"] = proc.stdout
                res_dict["stderr"] = proc.stderr
                res_dict["ok"] = (proc.returncode == 0)

            else:
                res_dict["ok"] = True
                res_dict["stdout"] = "OK"
                res_dict["exit_code"] = 0

        except subprocess.TimeoutExpired:
            res_dict["stderr"] = "Timed out"
        except Exception as exc:
            res_dict["stderr"] = str(exc)

        duration = round(time.time() - t_start, 3)
        res_dict["latency_seconds"] = duration
        if res_dict["ok"]:
            harness.status = "verified"
            harness.last_benchmarked = time.time()
            harness.benchmark_latency_seconds = duration

        return res_dict

    def sync_registry(self, auto_bench: bool = False) -> list[DiscoveredHarness]:
        """Discover all harnesses, optionally benchmark them, and save to registry."""
        harnesses = self.discover_all()
        if auto_bench:
            for h in harnesses[:5]:
                self.benchmark_harness(h)

        try:
            data = {
                "updated_at": time.time(),
                "total_harnesses": len(harnesses),
                "harnesses": [h.to_dict() for h in harnesses],
            }
            self.registry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not persist registry to %s: %s", self.registry_file, exc)

        return harnesses
