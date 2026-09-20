"""Pytest conftest for tests at the repository root.

RF-M3 (see GRO-4504) intentionally removed the auto-wrap pattern that
previously installed a test-only wrapper on ``ReviewQueue.enqueue_completed_work``
at conftest-load time. Tests that need to enqueue completed work now use
``enqueue_with_defaults`` from ``prismatic.review_factory.testing`` explicitly.

Why this conftest still exists
------------------------------
The file is preserved so that:

1. The gateway autouse authentication fixture below has a place to live.
2. Any future root-level pytest fixture declarations have a place to live.

Gateway autouse authentication fixture
--------------------------------------
The ``authenticated_gateway_test_client`` autouse fixture (originally added in
commit ``44ffe1c`` and stabilized across ``306b064`` / ``93de7b5``) supplies
control credentials to legacy gateway HTTP tests that do not opt in to
explicit credential setup. Without it, the ``control_authorization_middleware``
returns 401 for any non-read, non-webhook request and a large set of gateway
integration tests fail for an infrastructure reason unrelated to the test
itself.

RF-V1 hygiene commit (``tests: restore gateway auth autouse fixture for V3 HEAD``)
restores this fixture after it was inadvertently dropped by commit ``994b592``
(RF-R1/R2/R3) when the file was rewritten to install the RF enqueue wrapper.
The two fixtures are orthogonal: the auth fixture supplies *gateway* test
credentials; the RF-M3 helpers supply *review-factory* test bundles.

Historical context (RF-R2, prior to RF-M3)
------------------------------------------
Before RF-M3, this conftest installed a wrapper at module-load time
that filled in ``result_packet_path`` and ``result_packet_sha256``
defaults for any test that called
``ReviewQueue.enqueue_completed_work`` without supplying them. That
auto-wrap was brittle (see commit ``5e334c1``: install-once sentinel
fix) and is now removed.
"""

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


_CONTROL_TEST_TOKEN = "prisma...oken"
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
        app_state = getattr(getattr(client, "app", None), "state", None)
        if bool(getattr(app_state, "prismatic_control_authorization_installed", False)):
            headers.setdefault("Authorization", f"Bearer {_CONTROL_TEST_TOKEN}")
        return _ORIGINAL_TESTCLIENT_REQUEST(
            client, method, url, *args, headers=headers, **kwargs
        )

    monkeypatch.setattr(TestClient, "request", authenticated_request)
