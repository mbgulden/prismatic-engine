#!/usr/bin/env python3
"""Honeybadger day-1 infrastructure readiness probe.

This intentionally does not deploy or mutate infrastructure. It gives the
Honeybadger owner a repeatable pre-flight check for the parts called out in
GRO-149: 40G/RDMA evidence, Cloudflare Tunnel credentials, and local LLM/vLLM
serving documentation.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable, cast

DEFAULT_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILES = (
    pathlib.Path("/home/ubuntu/.hermes/profiles/ned/.env"),
    pathlib.Path("/home/ubuntu/.hermes/profiles/orchestrator/.env"),
    pathlib.Path("/home/ubuntu/.hermes/profiles/fred/.env"),
)
REQUIRED_CLOUDFLARE_KEYS = (
    "CLOUDFLARE_GROWTHWEB_EMAIL",
    "CLOUDFLARE_GROWTHWEB_API_KEY",
    "CLOUDFLARED_TUNNEL_TOKEN_GROWTH_WEB",
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    evidence: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.evidence:
            data["evidence"] = self.evidence
        return data


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """Parse shell-style KEY=VALUE lines without expanding or exposing secrets."""
    values: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return values
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def collect_env(
    paths: Iterable[pathlib.Path],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    merged: dict[str, str] = dict(os.environ)
    sources: dict[str, list[str]] = {}
    for path in paths:
        parsed = parse_env_file(path)
        for key, value in parsed.items():
            if key not in merged or not merged[key]:
                merged[key] = value
            sources.setdefault(key, []).append(str(path))
    return merged, sources


def redact_presence(
    keys: Iterable[str], env: dict[str, str], sources: dict[str, list[str]]
) -> dict[str, dict[str, object]]:
    return {
        key: {
            "present": bool(env.get(key)),
            "sources": sources.get(key, ["process-env"] if os.environ.get(key) else []),
        }
        for key in keys
    }


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def probe_command(cmd: list[str], timeout: int = 3) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (proc.stdout or proc.stderr).strip()
    return proc.returncode == 0, output[:300]


def build_report(
    repo_root: pathlib.Path, env_paths: Iterable[pathlib.Path] = DEFAULT_ENV_FILES
) -> dict[str, Any]:
    env, sources = collect_env(env_paths)
    checks: list[Check] = []

    rdma_report = repo_root / "network" / "latency_report.md"
    if rdma_report.exists():
        checks.append(
            Check(
                "40g_rdma_design_evidence",
                "pass",
                "Existing 40G/RDMA design evidence found; use it as Honeybadger network baseline.",
                str(rdma_report),
            )
        )
    else:
        stable_copy = pathlib.Path(
            "/home/ubuntu/work/prismatic-engine-stable/network/latency_report.md"
        )
        status = "warn" if stable_copy.exists() else "fail"
        detail = "Current checkout lacks network/latency_report.md."
        if stable_copy.exists():
            detail += " Stable checkout has a copy that should be ported when Honeybadger repo is available."
        checks.append(
            Check(
                "40g_rdma_design_evidence",
                status,
                detail,
                str(stable_copy) if stable_copy.exists() else None,
            )
        )

    missing_cf = [key for key in REQUIRED_CLOUDFLARE_KEYS if not env.get(key)]
    checks.append(
        Check(
            "cloudflare_tunnel_credentials",
            "pass" if not missing_cf else "warn",
            "GrowthWeb Cloudflare + tunnel credential names present."
            if not missing_cf
            else f"Missing credential names: {', '.join(missing_cf)}",
        )
    )

    cloudflared_ok = command_available("cloudflared")
    checks.append(
        Check(
            "cloudflared_binary",
            "pass" if cloudflared_ok else "warn",
            "cloudflared binary is available in PATH."
            if cloudflared_ok
            else "cloudflared not found in PATH; install before cutover or run tunnel from managed host.",
        )
    )

    provider_playbook = repo_root / "docs" / "provider-playbook-local-llm.md"
    local_arch = repo_root / "reports" / "agy-local-agent-architecture.md"
    if provider_playbook.exists() and local_arch.exists():
        checks.append(
            Check(
                "vllm_ingestion_reference_docs",
                "pass",
                "Local LLM/vLLM provider and agent architecture docs exist in this checkout.",
                f"{provider_playbook}; {local_arch}",
            )
        )
    else:
        checks.append(
            Check(
                "vllm_ingestion_reference_docs",
                "warn",
                "Missing one or more local LLM/vLLM reference docs in this checkout.",
            )
        )

    honeybadger_repo = pathlib.Path("/home/ubuntu/work/honeybadger")
    checks.append(
        Check(
            "honeybadger_private_repo",
            "pass" if honeybadger_repo.exists() else "warn",
            "Honeybadger private repo is present."
            if honeybadger_repo.exists()
            else "Honeybadger private repo not mounted at /home/ubuntu/work/honeybadger; use this report as a Prismatic testbed readiness artifact until repo access is restored.",
            str(honeybadger_repo),
        )
    )

    tailscale_ok, tailscale_out = (
        probe_command(["tailscale", "status", "--peers=false"])
        if command_available("tailscale")
        else (False, "tailscale not installed")
    )
    checks.append(
        Check(
            "tailscale_control_plane",
            "pass" if tailscale_ok else "warn",
            "Tailscale status command succeeded."
            if tailscale_ok
            else f"Tailscale status probe unavailable: {tailscale_out}",
        )
    )

    status_rank = {"pass": 0, "warn": 1, "fail": 2}
    overall = max(
        (check.status for check in checks), key=lambda status: status_rank[status]
    )
    next_actions = [
        "Port or recreate Honeybadger ARCHITECTURE.md before implementation work; /home/ubuntu/work/honeybadger is absent in this runtime.",
        "Reuse GrowthWeb Cloudflare tunnel pattern from the Prismatic testbed; never print or commit token values.",
        "Treat 40G/RDMA and vLLM as prerequisite gates: validate fabric, then expose ingestion via tunnel, then attach model workers.",
    ]
    return {
        "issue": "GRO-149",
        "title": "Honeybadger Infrastructure — 40G RDMA, Cloudflare Tunnels, vLLM Ingestion Factory",
        "overall_status": overall,
        "checks": [check.to_dict() for check in checks],
        "credential_presence": redact_presence(REQUIRED_CLOUDFLARE_KEYS, env, sources),
        "next_actions": next_actions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=pathlib.Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)

    report = build_report(args.repo_root.resolve())
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Honeybadger readiness: {str(report['overall_status']).upper()}")
        for check in cast(list[dict[str, Any]], report["checks"]):
            print(f"- {check['status'].upper():4} {check['name']}: {check['detail']}")
        print("Next actions:")
        for action in cast(list[str], report["next_actions"]):
            print(f"- {action}")
    return 0 if report["overall_status"] in {"pass", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
