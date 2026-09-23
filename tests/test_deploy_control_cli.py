"""Tests for the `prismatic deploy` control commands (Portal Plan Phase 1, P0 #2).

The control commands are thin HTTP clients of the gateway's deploy control
API; these tests pin the request shape and the presentation, not the API.
`remove-repo` is exercised end to end against :mod:`pe.deploy.onboard` with
an isolated registry file.
"""

from __future__ import annotations

import argparse
import json

import pytest

from prismatic.cli import deploy as deploy_cli


def _args(**overrides):
    base = dict(
        gateway=None,
        token="test-token",
        repo=None,
        ref=None,
        dry_run=False,
        deploy_id=None,
        status=None,
        limit=20,
        full_name=None,
        yes=True,
        registry_file="",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _api_ok(payload):
    def fake(method, gateway, path, token=None, payload=None, timeout=60):  # noqa: ANN001, ANN202
        return payload

    return fake


class TestTrigger:
    def test_missing_token_is_fail_closed_without_http(self, monkeypatch, capsys):
        monkeypatch.delenv("PRISMATIC_CONTROL_TOKEN", raising=False)
        called = []
        monkeypatch.setattr(
            deploy_cli, "_api_request", lambda *a, **k: called.append((a, k))
        )
        rc = deploy_cli.run_deploy(
            _args(deploy_command="trigger", token=None, repo="mbgulden/test-pilot")
        )
        assert rc == 1
        assert called == []
        assert "PRISMATIC_CONTROL_TOKEN" in capsys.readouterr().err

    def test_trigger_posts_repo_ref_and_dry_run(self, monkeypatch, capsys):
        seen = {}

        def fake(method, gateway, path, token=None, payload=None, timeout=60):  # noqa: ANN001, ANN202
            seen.update(
                method=method, path=path, token=token, payload=payload, gateway=gateway
            )
            return {
                "status": "dispatched",
                "trigger_id": "trig-1",
                "receipt_id": "r-1",
                "repo": "mbgulden/test-pilot",
                "pr_sha": "a" * 40,
                "dry_run": True,
                "detail": "deploy dispatched to the receiver",
            }

        monkeypatch.setattr(deploy_cli, "_api_request", fake)
        rc = deploy_cli.run_deploy(
            _args(
                deploy_command="trigger",
                repo="mbgulden/test-pilot",
                ref="a" * 40,
                dry_run=True,
            )
        )
        assert rc == 0
        assert seen["method"] == "POST"
        assert seen["path"] == "/api/deploys"
        assert seen["token"] == "test-token"
        assert seen["payload"] == {
            "repo": "mbgulden/test-pilot",
            "ref": "a" * 40,
            "dry_run": True,
        }
        out = capsys.readouterr().out
        assert "trig-1" in out and "r-1" in out and "dispatched" in out

    def test_trigger_api_error_maps_to_exit_1(self, monkeypatch, capsys):
        def fake(*a, **k):  # noqa: ANN001, ANN202
            raise deploy_cli._ApiError(400, "unknown repo 'octo/ghost'")

        monkeypatch.setattr(deploy_cli, "_api_request", fake)
        rc = deploy_cli.run_deploy(_args(deploy_command="trigger", repo="octo/ghost"))
        assert rc == 1
        assert "unknown repo" in capsys.readouterr().err


class TestRollback:
    def test_rollback_posts_to_deploy_id_path(self, monkeypatch, capsys):
        seen = {}

        def fake(method, gateway, path, token=None, payload=None, timeout=60):  # noqa: ANN001, ANN202
            seen.update(method=method, path=path)
            return {
                "status": "rolled_back",
                "deploy_id": "dep-1",
                "repo": "mbgulden/test-pilot",
                "restored_release": "testpilot-b",
                "restored_sha": "b" * 40,
                "receipt_id": "r-2",
            }

        monkeypatch.setattr(deploy_cli, "_api_request", fake)
        rc = deploy_cli.run_deploy(_args(deploy_command="rollback", deploy_id="dep-1"))
        assert rc == 0
        assert seen["method"] == "POST"
        assert seen["path"] == "/api/deploys/dep-1/rollback"
        out = capsys.readouterr().out
        assert "rolled_back" in out and "testpilot-b" in out

    def test_rollback_api_error_maps_to_exit_1(self, monkeypatch):
        def fake(*a, **k):  # noqa: ANN001, ANN202
            raise deploy_cli._ApiError(400, "refusing")

        monkeypatch.setattr(deploy_cli, "_api_request", fake)
        assert (
            deploy_cli.run_deploy(_args(deploy_command="rollback", deploy_id="x")) == 1
        )


class TestHistory:
    def test_history_prints_rows(self, monkeypatch, capsys):
        def fake(method, gateway, path, token=None, payload=None, timeout=60):  # noqa: ANN001, ANN202
            assert method == "GET" and path == "/api/deploys"
            assert payload == {"repo": None, "status": None, "limit": 20}
            return {
                "count": 2,
                "deploys": [
                    {
                        "type": "deploy_failed",
                        "alert": "PostMergeDeployFailed",
                        "severity": "critical",
                        "summary": "deploy d3 failed",
                        "repo": "mbgulden/test-pilot",
                        "deploy_id": "d3",
                        "pr_sha": "c" * 40,
                        "timestamp": "2026-09-23T12:00:00Z",
                        "source": "alerts.log",
                    },
                    {
                        "type": "deploy_succeeded",
                        "alert": "PostMergeDeploySucceeded",
                        "severity": "info",
                        "summary": "deploy d1 succeeded",
                        "repo": "mbgulden/prismatic-engine",
                        "deploy_id": "d1",
                        "pr_sha": "a" * 40,
                        "timestamp": "2026-09-23T10:00:00Z",
                        "source": "alerts.log",
                    },
                ],
            }

        monkeypatch.setattr(deploy_cli, "_api_request", fake)
        assert deploy_cli.run_deploy(_args(deploy_command="history")) == 0
        out = capsys.readouterr().out
        assert "d3" in out and "d1" in out and "2 entries" in out

    def test_history_api_error_maps_to_exit_1(self, monkeypatch):
        def fake(*a, **k):  # noqa: ANN001, ANN202
            raise deploy_cli._ApiError(0, "cannot reach gateway")

        monkeypatch.setattr(deploy_cli, "_api_request", fake)
        assert deploy_cli.run_deploy(_args(deploy_command="history")) == 1


class TestReleases:
    def test_releases_prints_per_repo_state(self, monkeypatch, capsys):
        def fake(method, gateway, path, token=None, payload=None, timeout=60):  # noqa: ANN001, ANN202
            assert method == "GET" and path == "/api/deploys/releases"
            return {
                "repos": [
                    {
                        "repo": "mbgulden/test-pilot",
                        "release_prefix": "testpilot",
                        "target_service": "test-pilot.service",
                        "target_node": "local",
                        "mirror_present": True,
                        "secret_configured": True,
                        "current_release": "testpilot-aaa",
                        "current_venv": "testpilot-aaa",
                        "releases": [
                            {
                                "name": "testpilot-aaa",
                                "sha": "a" * 40,
                                "is_current": True,
                                "is_dir": True,
                                "modified": "2026-09-23T10:00:00Z",
                            }
                        ],
                        "latest_deploy": {
                            "deploy_id": "d1",
                            "pr_sha": "a" * 40,
                            "success": True,
                            "deployed_at": "2026-09-23T10:00:00Z",
                            "deployer": "github-action",
                        },
                    }
                ]
            }

        monkeypatch.setattr(deploy_cli, "_api_request", fake)
        assert deploy_cli.run_deploy(_args(deploy_command="releases")) == 0
        out = capsys.readouterr().out
        assert "mbgulden/test-pilot" in out
        assert "* testpilot-aaa" in out


class TestRemoveRepo:
    @pytest.fixture
    def registry(self, tmp_path, monkeypatch):
        reg = tmp_path / "deploy-repos.json"
        reg.write_text(
            json.dumps(
                {
                    "mbgulden/prismatic-engine": {},
                    "octo/repo": {"release_prefix": "octo-repo"},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("HOME", str(tmp_path))
        return reg

    def test_remove_repo_removes_entry_and_keeps_others(self, registry, capsys):
        rc = deploy_cli.run_deploy(
            _args(
                deploy_command="remove-repo",
                full_name="octo/repo",
                registry_file=str(registry),
            )
        )
        assert rc == 0
        data = json.loads(registry.read_text(encoding="utf-8"))
        assert set(data) == {"mbgulden/prismatic-engine"}
        out = capsys.readouterr().out
        assert "removed octo/repo" in out
        # Leftovers are reported, not deleted.
        assert "left in place" in out

    def test_remove_repo_dry_run_changes_nothing(self, registry, capsys):
        before = registry.read_text(encoding="utf-8")
        rc = deploy_cli.run_deploy(
            _args(
                deploy_command="remove-repo",
                full_name="octo/repo",
                dry_run=True,
                registry_file=str(registry),
            )
        )
        assert rc == 0
        assert registry.read_text(encoding="utf-8") == before
        assert "dry run" in capsys.readouterr().out.lower()

    def test_remove_repo_unknown_repo_fails(self, registry, capsys):
        rc = deploy_cli.run_deploy(
            _args(
                deploy_command="remove-repo",
                full_name="octo/ghost",
                registry_file=str(registry),
            )
        )
        assert rc == 1
        assert "not in the deploy registry" in capsys.readouterr().err

    def test_remove_repo_refuses_last_remaining_repo(self, tmp_path, capsys):
        reg = tmp_path / "deploy-repos.json"
        reg.write_text(json.dumps({"octo/solo": {}}), encoding="utf-8")
        rc = deploy_cli.run_deploy(
            _args(
                deploy_command="remove-repo",
                full_name="octo/solo",
                registry_file=str(reg),
            )
        )
        assert rc == 1
        assert "at least one repo" in capsys.readouterr().err
        # Registry untouched.
        assert set(json.loads(reg.read_text(encoding="utf-8"))) == {"octo/solo"}
