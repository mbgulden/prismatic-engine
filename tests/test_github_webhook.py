import unittest
import os
import json
import hashlib
import hmac as _hmac
from unittest.mock import patch

from fastapi.testclient import TestClient

# Avoid loading real env variables before importing app
with patch.dict(os.environ, {"PRISMATIC_ENV_FILE": ""}):
    from prismatic.gateway.server import app


class TestGitHubWebhookSignature(unittest.TestCase):
    """Test suite for GitHub Webhook signature/HMAC verification."""

    def setUp(self):
        self.client = TestClient(app)

    def _generate_signature(self, secret: str, body: bytes) -> str:
        mac = _hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
        return f"sha256={mac.hexdigest()}"

    @patch("prismatic.gateway.server.get_github_secrets")
    def test_webhook_accepts_valid_signature(self, mock_get_secrets):
        # Mock configured secrets
        mock_get_secrets.return_value = ["test-secret-123", "rotated-secret-456"]

        payload = {"action": "opened", "pull_request": {"number": 42}}
        body = json.dumps(payload).encode("utf-8")
        signature = self._generate_signature("test-secret-123", body)

        response = self.client.post(
            "/api/gateway/github",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("prismatic.gateway.server.get_github_secrets")
    def test_webhook_accepts_rotated_secret(self, mock_get_secrets):
        # Mock configured secrets: primary is 123, secondary is 456
        mock_get_secrets.return_value = ["test-secret-123", "rotated-secret-456"]

        payload = {"action": "opened", "pull_request": {"number": 42}}
        body = json.dumps(payload).encode("utf-8")
        signature = self._generate_signature("rotated-secret-456", body)

        response = self.client.post(
            "/api/gateway/github",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("prismatic.gateway.server.get_github_secrets")
    def test_webhook_rejects_invalid_signature(self, mock_get_secrets):
        mock_get_secrets.return_value = ["test-secret-123"]

        payload = {"action": "opened", "pull_request": {"number": 42}}
        body = json.dumps(payload).encode("utf-8")
        signature = "sha256=invalidsignaturehere"

        response = self.client.post(
            "/api/gateway/github",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["status"], "auth-failed")

    @patch("prismatic.gateway.server.get_github_secrets")
    def test_webhook_rejects_missing_signature_when_secrets_exist(self, mock_get_secrets):
        mock_get_secrets.return_value = ["test-secret-123"]

        payload = {"action": "opened", "pull_request": {"number": 42}}
        body = json.dumps(payload).encode("utf-8")

        # No signature header sent
        response = self.client.post(
            "/api/gateway/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["status"], "auth-failed")

    @patch("prismatic.gateway.server.get_github_secrets")
    def test_webhook_bypasses_when_no_secrets_configured(self, mock_get_secrets):
        # Empty secrets list (e.g. local dev / default settings)
        mock_get_secrets.return_value = []

        payload = {"action": "opened", "pull_request": {"number": 42}}
        body = json.dumps(payload).encode("utf-8")

        response = self.client.post(
            "/api/gateway/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
