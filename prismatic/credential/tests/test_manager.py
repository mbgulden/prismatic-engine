"""Unit tests for prismatic.credential.manager."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from prismatic.credential.manager import get_credentials_status, update_credentials


def test_get_credentials_status_defaults(tmp_path):
    with patch.dict("os.environ", {}, clear=True):
        with patch("prismatic.credential.manager.ENV_PATH_CANDIDATES", [tmp_path / ".env"]):
            status = get_credentials_status()
            assert status["ok"] is True
            creds = status["credentials"]
            assert "GOOGLE_SA_JSON" in creds
            assert creds["GOOGLE_SA_JSON"]["configured"] is False
            assert creds["GOOGLE_SA_JSON"]["settings_tab_url"] == "https://prismatic.growthwebdev.com/tab/settings"


def test_update_and_persist_credentials(tmp_path):
    env_file = tmp_path / ".env"
    with patch.dict("os.environ", {}, clear=True):
        with patch("prismatic.credential.manager.ENV_PATH_CANDIDATES", [env_file]):
            res = update_credentials({
                "GA4_ACCOUNT_ID": "12345678",
                "CLOUDFLARE_API_TOKEN": "secret_cf_token_1234",
            })
            assert res["ok"] is True
            assert "GA4_ACCOUNT_ID" in res["updated_keys"]
            assert env_file.exists()
            content = env_file.read_text()
            assert "GA4_ACCOUNT_ID=12345678" in content
            assert "CLOUDFLARE_API_TOKEN=secret_cf_token_1234" in content

            status = get_credentials_status()["credentials"]
            assert status["GA4_ACCOUNT_ID"]["configured"] is True
            assert status["CLOUDFLARE_API_TOKEN"]["configured"] is True
            assert status["CLOUDFLARE_API_TOKEN"]["redacted_value"] == "secr...1234"
