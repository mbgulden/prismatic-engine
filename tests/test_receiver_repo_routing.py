"""Tests for WS1 repo routing in the deploy receiver.

Signed triggers for two fixture repos must route to different mirror dirs /
services; unknown repos, wrong per-repo secrets, missing secrets, and a
missing ``repository`` field must all fail closed.
"""

import hashlib
import hmac
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from pe.deploy.receiver import (
    DeployReceiverPipeline,
    create_deploy_receiver_app,
    get_repo_hmac_secret,
    verify_hmac_signature,
)
from pe.deploy.config import load_repo_registry


WIDGETS = "acme/widgets"
GADGETS = "acme/gadgets"
WIDGETS_SECRET = "test-secret-widgets"
GADGETS_SECRET = "test-secret-gadgets"


def _git(*args, cwd):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.fixture
def two_repo_env(tmp_path, monkeypatch):
    """Hermetic two-repo registry: no HOME/CWD/.env leakage."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    monkeypatch.delenv("PRISMATIC_ALLOW_DEFAULT_HMAC", raising=False)
    monkeypatch.delenv("PRISMATIC_STRICT_SECRETS", raising=False)
    monkeypatch.delenv("DEPLOY_HMAC_SECRET", raising=False)

    repos = {
        WIDGETS: {
            "mirror_dir": str(tmp_path / "m1"),
            "release_prefix": "widgets",
            "target_service": "widgets.service",
        },
        GADGETS: {
            "mirror_dir": str(tmp_path / "m2"),
            "release_prefix": "gadgets",
            "target_service": "gadgets.service",
        },
    }
    repos_file = tmp_path / "repos.json"
    repos_file.write_text(json.dumps(repos), encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(repos_file))

    monkeypatch.setenv("DEPLOY_HMAC_SECRET_ACME_WIDGETS", WIDGETS_SECRET)
    monkeypatch.setenv("DEPLOY_HMAC_SECRET_ACME_GADGETS", GADGETS_SECRET)

    # Deploy source checkout (pipeline construction fail-fasts without it).
    src = tmp_path / "src"
    src.mkdir()
    (src / "prismatic").mkdir()
    (src / "prismatic" / "__init__.py").write_text("# main", encoding="utf-8")
    _git("init", "-q", "-b", "main", str(src), cwd=str(tmp_path))
    _git("config", "user.email", "test@example.com", cwd=str(src))
    _git("config", "user.name", "test", cwd=str(src))
    _git("add", ".", cwd=str(src))
    _git("commit", "-qm", "seed", cwd=str(src))
    monkeypatch.setenv("PRISMATIC_DEPLOY_SOURCE_REPO", str(src))

    monkeypatch.setenv("PRISMATIC_DEPLOY_DB", str(tmp_path / "deploy_records.json"))
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))
    return tmp_path


def _signed_payload(payload, secret):
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, {
        "X-Hub-Signature-256": f"sha256={sig}",
        "Content-Type": "application/json",
    }


def _dry_run_payload(repository):
    return {
        "pr_sha": "a" * 40,
        "pr_title": "test",
        "deployer": "test",
        "dry_run": True,
        "repository": repository,
    }


class TestTwoRepoRouting:
    def test_signed_triggers_route_to_their_own_repo(self, two_repo_env):
        app = create_deploy_receiver_app()
        client = TestClient(app)
        for repo, secret in ((WIDGETS, WIDGETS_SECRET), (GADGETS, GADGETS_SECRET)):
            body, headers = _signed_payload(_dry_run_payload(repo), secret)
            res = client.post("/deploy", content=body, headers=headers)
            assert res.status_code == 200, res.text
            record = res.json()["deploy_record"]
            assert record["repository"] == repo
            assert record["success"] is True

    def test_routing_resolves_different_mirrors_and_services(self, two_repo_env):
        pipeline = DeployReceiverPipeline(
            source_repo=two_repo_env / "src",
        )
        widgets = pipeline._resolve_repo({"repository": WIDGETS}, None)
        gadgets = pipeline._resolve_repo({"repository": GADGETS}, None)
        assert widgets.mirror_dir == two_repo_env / "m1"
        assert gadgets.mirror_dir == two_repo_env / "m2"
        assert widgets.mirror_dir != gadgets.mirror_dir
        assert pipeline._redeployer_for(widgets).service == "widgets.service"
        assert pipeline._redeployer_for(gadgets).service == "gadgets.service"
        assert pipeline._runner_for(widgets).release_prefix == "widgets"
        assert pipeline._runner_for(gadgets).release_prefix == "gadgets"

    def test_source_checkout_is_per_repo(self, two_repo_env):
        pipeline = DeployReceiverPipeline(source_repo=two_repo_env / "src")
        widgets = pipeline._resolve_repo({"repository": WIDGETS}, None)
        gadgets = pipeline._resolve_repo({"repository": GADGETS}, None)
        # Default repo: the explicit PRISMATIC_DEPLOY_SOURCE_REPO checkout.
        assert pipeline._source_repo_for(widgets) == two_repo_env / "src"
        # Additional repo: its own persistent mirror.
        assert pipeline._source_repo_for(gadgets) == two_repo_env / "m2"

    def test_explicit_repo_config_wins_over_payload(self, two_repo_env):
        reg = load_repo_registry()
        pipeline = DeployReceiverPipeline(source_repo=two_repo_env / "src")
        resolved = pipeline._resolve_repo(
            {"repository": WIDGETS}, reg.get(GADGETS)
        )
        assert resolved.full_name == GADGETS


class TestFailClosed:
    def test_missing_repository_field_is_rejected(self, two_repo_env):
        app = create_deploy_receiver_app()
        client = TestClient(app)
        payload = {
            "pr_sha": "a" * 40,
            "pr_title": "t",
            "deployer": "t",
            "dry_run": True,
        }
        body, headers = _signed_payload(payload, WIDGETS_SECRET)
        res = client.post("/deploy", content=body, headers=headers)
        assert res.status_code == 400
        assert "repository" in res.json()["detail"]

    def test_unknown_repo_is_refused_and_alerted(self, two_repo_env):
        app = create_deploy_receiver_app()
        client = TestClient(app)
        payload = _dry_run_payload("acme/nope")
        body, headers = _signed_payload(payload, "anything")
        res = client.post("/deploy", content=body, headers=headers)
        assert res.status_code == 400
        assert "acme/nope" in res.json()["detail"]
        alerts = (two_repo_env / "alerts.log").read_text(encoding="utf-8")
        assert "PostMergeDeployFailed" in alerts
        assert "acme/nope" in alerts

    def test_unknown_repo_fail_closed_inside_pipeline(self, two_repo_env):
        pipeline = DeployReceiverPipeline(source_repo=two_repo_env / "src")
        record = pipeline.process_deploy(
            {"pr_sha": "a" * 40, "repository": "acme/nope"}
        )
        assert record.success is False
        assert "acme/nope" in (record.failure_reason or "")
        assert record.repository == "acme/nope"

    def test_wrong_per_repo_secret_is_401(self, two_repo_env):
        app = create_deploy_receiver_app()
        client = TestClient(app)
        body, headers = _signed_payload(_dry_run_payload(WIDGETS), "wrong-secret")
        res = client.post("/deploy", content=body, headers=headers)
        assert res.status_code == 401

    def test_shared_secret_fallback(self, two_repo_env, monkeypatch):
        monkeypatch.delenv("DEPLOY_HMAC_SECRET_ACME_WIDGETS", raising=False)
        monkeypatch.setenv("DEPLOY_HMAC_SECRET", "shared-fallback-secret")
        app = create_deploy_receiver_app()
        client = TestClient(app)
        body, headers = _signed_payload(
            _dry_run_payload(WIDGETS), "shared-fallback-secret"
        )
        res = client.post("/deploy", content=body, headers=headers)
        assert res.status_code == 200, res.text

    def test_missing_secret_for_routed_repo_is_loud_refusal(
        self, two_repo_env, monkeypatch
    ):
        monkeypatch.setenv("PRISMATIC_STRICT_SECRETS", "1")
        monkeypatch.delenv("DEPLOY_HMAC_SECRET_ACME_GADGETS", raising=False)
        monkeypatch.delenv("DEPLOY_HMAC_SECRET", raising=False)
        app = create_deploy_receiver_app()
        client = TestClient(app)
        body, headers = _signed_payload(_dry_run_payload(GADGETS), "whatever")
        res = client.post("/deploy", content=body, headers=headers)
        assert res.status_code == 500
        assert "DEPLOY_HMAC_SECRET_ACME_GADGETS" in res.json()["detail"]


class TestPerRepoSecretResolution:
    def test_per_repo_beats_shared(self, two_repo_env, monkeypatch):
        monkeypatch.setenv("DEPLOY_HMAC_SECRET", "shared")
        reg = load_repo_registry()
        assert get_repo_hmac_secret(reg.get(WIDGETS)) == WIDGETS_SECRET

    def test_verify_with_resolved_secret(self, two_repo_env):
        reg = load_repo_registry()
        secret = get_repo_hmac_secret(reg.get(GADGETS))
        body = b'{"repository":"acme/gadgets"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_hmac_signature(body, f"sha256={sig}", secret=secret) is True
        assert (
            verify_hmac_signature(body, "sha256=deadbeef", secret=secret) is False
        )
