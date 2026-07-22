from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))

loaded = sys.modules.get("prismatic")
if loaded is not None:
    module_file = getattr(loaded, "__file__", "") or ""
    if module_file and not module_file.startswith(str(REPO_ROOT)):
        for name in list(sys.modules):
            if name == "prismatic" or name.startswith("prismatic."):
                sys.modules.pop(name, None)


_CONTROL_TEST_TOKEN = "prismatic-pytest-control-token"
_ORIGINAL_TESTCLIENT_REQUEST = TestClient.request


@pytest.fixture(autouse=True)
def authenticated_gateway_test_client(request, tmp_path, monkeypatch):
    """Give legacy gateway tests explicit control credentials.

    The control-auth module's own adversarial tests opt out so they can prove
    missing/invalid credential behavior. Production code has no testing bypass.
    """

    if request.path.name == "test_dashboard_control_auth.py":
        return

    credentials = tmp_path / "control-auth-test.json"
    credentials.write_text(
        json.dumps(
            {
                "version": 1,
                "credentials": [
                    {
                        "actor": "pytest-gateway-client",
                        "token_sha256": hashlib.sha256(
                            _CONTROL_TEST_TOKEN.encode("utf-8")
                        ).hexdigest(),
                        "roles": ["operator", "approver", "executor"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    credentials.chmod(0o600)
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(credentials))

    def authenticated_request(client, method, url, *args, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        gateway_module = sys.modules.get("prismatic.gateway.server")
        gateway_app = getattr(gateway_module, "app", None)
        if gateway_app is not None and getattr(client, "app", None) is gateway_app:
            headers.setdefault("Authorization", f"Bearer {_CONTROL_TEST_TOKEN}")
        return _ORIGINAL_TESTCLIENT_REQUEST(
            client, method, url, *args, headers=headers, **kwargs
        )

    monkeypatch.setattr(TestClient, "request", authenticated_request)
