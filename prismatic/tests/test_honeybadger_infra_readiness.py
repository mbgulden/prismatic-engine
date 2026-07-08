from __future__ import annotations

import json
import pathlib
from typing import Any, cast

from scripts.ops.honeybadger_infra_readiness import build_report, parse_env_file


def test_parse_env_file_redacts_values_by_callers(tmp_path: pathlib.Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "export CLOUDFLARE_GROWTHWEB_EMAIL=ops@example.com\n"
        "CLOUDFLARE_GROWTHWEB_API_KEY='secret'\n"
        "IGNORED\n"
    )

    parsed = parse_env_file(env_file)

    assert parsed["CLOUDFLARE_GROWTHWEB_EMAIL"] == "ops@example.com"
    assert parsed["CLOUDFLARE_GROWTHWEB_API_KEY"] == "secret"
    assert "IGNORED" not in parsed


def test_build_report_warns_when_honeybadger_repo_missing_but_keeps_json_safe(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "reports").mkdir(parents=True)
    (repo / "network").mkdir(parents=True)
    (repo / "network" / "latency_report.md").write_text("RDMA baseline")
    (repo / "docs" / "provider-playbook-local-llm.md").write_text("vLLM")
    (repo / "reports" / "agy-local-agent-architecture.md").write_text(
        "vLLM architecture"
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLOUDFLARE_GROWTHWEB_EMAIL=ops@example.com\n"
        "CLOUDFLARE_GROWTHWEB_API_KEY=top-secret\n"
        "CLOUDFLARED_TUNNEL_TOKEN_GROWTH_WEB=tunnel-secret\n"
    )

    report = build_report(repo, env_paths=[env_file])
    encoded = json.dumps(report)

    checks = cast(list[dict[str, Any]], report["checks"])
    credential_presence = cast(dict[str, dict[str, Any]], report["credential_presence"])

    assert report["issue"] == "GRO-149"
    assert report["overall_status"] in {"pass", "warn"}
    assert any(
        check["name"] == "honeybadger_private_repo" and check["status"] == "warn"
        for check in checks
    )
    assert "top-secret" not in encoded
    assert "tunnel-secret" not in encoded
    assert credential_presence["CLOUDFLARE_GROWTHWEB_API_KEY"]["present"] is True
