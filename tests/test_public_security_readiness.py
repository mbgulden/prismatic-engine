from __future__ import annotations

import importlib.util
from pathlib import Path

from prismatic.gateway import server
from prismatic.plugin_artifacts import safe_local_artifact_path
from prismatic.plugin_policy import evaluate_job_request_policy, redact_secrets


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "scripts" / "public_security_readiness_audit.py"


def _load_audit_module():
    spec = importlib.util.spec_from_file_location(
        "public_security_readiness_audit", AUDIT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_security_audit_passes() -> None:
    audit = _load_audit_module()
    result = audit.run_audit()
    assert result["ok"] is True, result["failures"]
    assert result["failures"] == []


def test_secret_scan_includes_private_key_pem_files(tmp_path: Path) -> None:
    audit = _load_audit_module()
    private_key = "-----BEGIN " + "PRIVATE KEY-----\nDUMMY\n-----END PRIVATE KEY-----\n"
    key_path = tmp_path / "review-bot.private-key.pem"
    key_path.write_text(private_key, encoding="utf-8")

    findings = audit.scan_for_raw_secrets(tmp_path)

    assert findings == [
        {
            "path": "review-bot.private-key.pem",
            "line": 1,
            "issue": "high-confidence secret-like value",
        }
    ]


def test_cors_defaults_are_local_only_and_wildcard_is_rejected(monkeypatch) -> None:
    monkeypatch.delenv("PRISMATIC_CORS_ORIGINS", raising=False)
    defaults = server._configured_cors_origins()
    assert "*" not in defaults
    assert "http://127.0.0.1:9000" in defaults
    assert "http://localhost:9000" in defaults

    monkeypatch.setenv("PRISMATIC_CORS_ORIGINS", "*,https://dashboard.example.test/")
    explicit = server._configured_cors_origins()
    assert "*" not in explicit
    assert "https://dashboard.example.test" in explicit


def test_public_redaction_preserves_env_names_but_redacts_values() -> None:
    payload = {
        "provider": "example",
        "env_name": "PWP_SERVICE_API_KEY",
        "nested": {"api_key": "sk" + "-" + "publicsecurityexamplevalue123456"},
    }
    redacted = redact_secrets(payload)
    assert redacted["env_name"] == "PWP_SERVICE_API_KEY"
    assert redacted["nested"]["api_key"] == "[REDACTED]"

    policy = evaluate_job_request_policy(
        "pwp-design-token-plugin",
        "smoke_validate",
        input_summary={"token": "sk" + "-" + "publicsecurityexamplevalue123456"},
    )
    assert policy["decision"] == "block"
    assert "sk-publicsecurity" not in str(policy)


def test_artifact_path_traversal_is_not_hashable(tmp_path: Path) -> None:
    outside = safe_local_artifact_path("/etc/passwd")
    traversal = safe_local_artifact_path("../../../../etc/passwd")
    assert outside is None
    assert traversal is None

    safe_file = tmp_path / "artifact.txt"
    safe_file.write_text("safe artifact", encoding="utf-8")
    # pytest tmp_path is normally under /tmp and is intentionally allowed for
    # local smoke artifacts.
    assert safe_local_artifact_path(str(safe_file)) == safe_file.resolve()
