"""Tests for pe.deploy.config: the multi-repo registry (WS1) and the
centralized PRISMATIC_* env vars.

The headline invariant: with the new env vars unset, the resolved registry
and every centralized default must be byte-identical to today's hardcoded
behavior (single repo mbgulden/prismatic-engine, today's exact paths).
"""

import json
from pathlib import Path

import pytest

from pe.deploy import config
from pe.deploy.config import (
    DEFAULT_GATEWAY_EXTRAS,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_GATEWAY_SERVICE,
    DEFAULT_HEALTH_ENDPOINTS,
    DEFAULT_RELEASE_PREFIX,
    DEFAULT_REPO_FULL_NAME,
    DeployRepoConfig,
    DeployRepoRegistry,
    UnknownRepoError,
    default_repo_config,
    default_repo_full_name,
    load_repo_registry,
    sanitize_env_name,
)


@pytest.fixture
def clean_registry_env(monkeypatch, tmp_path):
    """Registry env cleared; HOME pointed at tmp so path defaults are hermetic."""
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


class TestEnvUnsetDefaults:
    """Env-unset == today's exact behavior (the WS1 no-change gate)."""

    def test_registry_defaults_to_single_repo(self, clean_registry_env):
        reg = load_repo_registry()
        assert len(reg) == 1
        repo = reg.default()
        assert repo.full_name == DEFAULT_REPO_FULL_NAME == "mbgulden/prismatic-engine"
        assert default_repo_full_name() == "mbgulden/prismatic-engine"

    def test_default_repo_resolves_to_todays_exact_paths(
        self, clean_registry_env, tmp_path
    ):
        repo = default_repo_config()
        # The real mirror path the old MIRROR_REPO_RELATIVE resolved to.
        assert repo.mirror_dir == Path.home() / ".prismatic" / "repos" / "mbgulden" / "prismatic-engine"
        assert repo.mirror_dir == tmp_path / ".prismatic" / "repos" / "mbgulden" / "prismatic-engine"
        assert repo.release_prefix == DEFAULT_RELEASE_PREFIX == "prismatic-engine"
        assert repo.target_service == DEFAULT_GATEWAY_SERVICE == "prismatic-gateway.service"
        assert repo.health_endpoints == DEFAULT_HEALTH_ENDPOINTS == (
            ("/health", "gateway_health"),
            ("/api/review-factory/jobs", "review_jobs"),
        )
        assert repo.target_node == "local"

    def test_per_repo_secret_env_naming(self, clean_registry_env):
        repo = default_repo_config()
        assert repo.hmac_secret_env == "DEPLOY_HMAC_SECRET_MBGULDEN_PRISMATIC_ENGINE"
        assert sanitize_env_name("my-org/some_repo") == "MY_ORG_SOME_REPO"

    def test_centralized_env_defaults(self, clean_registry_env, tmp_path, monkeypatch):
        for var in (
            "PRISMATIC_STATE_DIR",
            "PRISMATIC_VERSIONS_DIR",
            "PRISMATIC_RELEASE_SYMLINK",
            "PRISMATIC_GATEWAY_SERVICE",
            "PRISMATIC_PORT",
            "PRISMATIC_GATEWAY_EXTRAS",
            "PRISMATIC_ALERT_LOG",
            "PRISMATIC_DEPLOY_DB",
            "PRISMATIC_LINEAR_TRANSITIONS_DB",
            "PRISMATIC_DEPLOY_TIMEOUT_S",
            "PRISMATIC_DEPLOY_MIN_FREE_GB",
        ):
            monkeypatch.delenv(var, raising=False)
        home = Path.home()
        assert config.gateway_service() == "prismatic-gateway.service"
        assert config.gateway_port() == 9000 == DEFAULT_GATEWAY_PORT
        assert config.gateway_extras() == "gateway,primitives,verification" == DEFAULT_GATEWAY_EXTRAS
        assert config.deploy_timeout_s() == 1800
        assert config.min_free_gb() == 5.0
        assert config.alert_log_path() == home / ".prismatic" / "alerts.log"
        assert config.versions_dir() == home / ".prismatic" / "versions"
        assert config.release_symlink_path() == home / ".prismatic" / "releases" / "prismatic-engine"
        assert config.release_symlink_path("widgets") == home / ".prismatic" / "releases" / "widgets"
        # deploy_db_path: pre-create ~/.prismatic so the db branch is taken.
        (home / ".prismatic").mkdir(parents=True, exist_ok=True)
        assert config.deploy_db_path() == home / ".prismatic" / "db" / "deploy_records.json"
        assert config.linear_transitions_db_path() == (
            home / ".prismatic" / "db" / "linear_transitions.json"
        )

    def test_env_overrides_still_win(self, clean_registry_env, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISMATIC_GATEWAY_SERVICE", "custom.service")
        monkeypatch.setenv("PRISMATIC_PORT", "9001")
        monkeypatch.setenv("PRISMATIC_DEPLOY_TIMEOUT_S", "60")
        assert config.gateway_service() == "custom.service"
        assert config.gateway_port() == 9001
        assert config.deploy_timeout_s() == 60


class TestRegistryFromEnvList:
    def test_comma_separated_list(self, clean_registry_env, monkeypatch):
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS", "acme/widgets, other-org/gadgets ")
        reg = load_repo_registry()
        assert len(reg) == 2
        assert reg.default().full_name == "acme/widgets"
        widgets = reg.get("acme/widgets")
        assert widgets.mirror_dir == (
            Path.home() / ".prismatic" / "repos" / "acme" / "widgets"
        )
        assert widgets.release_prefix == "prismatic-engine"  # default prefix
        assert widgets.target_service == "prismatic-gateway.service"
        assert widgets.target_node == "local"
        assert widgets.hmac_secret_env == "DEPLOY_HMAC_SECRET_ACME_WIDGETS"
        gadgets = reg.get("other-org/gadgets")
        assert gadgets.hmac_secret_env == "DEPLOY_HMAC_SECRET_OTHER_ORG_GADGETS"
        assert "acme/widgets" in reg
        assert "acme/nope" not in reg

    def test_multi_account_same_short_name(self, clean_registry_env, monkeypatch):
        """full_name is globally unique: two accounts can own the same repo name."""
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS", "org-a/svc,org-b/svc")
        reg = load_repo_registry()
        assert reg.get("org-a/svc").mirror_dir != reg.get("org-b/svc").mirror_dir
        assert reg.get("org-a/svc").hmac_secret_env != reg.get("org-b/svc").hmac_secret_env

    def test_invalid_names_rejected(self, clean_registry_env, monkeypatch):
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS", "not-a-repo")
        with pytest.raises(ValueError):
            load_repo_registry()

    def test_duplicate_names_rejected(self, clean_registry_env, monkeypatch):
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS", "acme/widgets,acme/widgets")
        with pytest.raises(ValueError):
            load_repo_registry()


class TestRegistryFromFile:
    def _write(self, tmp_path, data):
        f = tmp_path / "repos.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        return f

    def test_per_repo_overrides(self, clean_registry_env, tmp_path, monkeypatch):
        f = self._write(
            tmp_path,
            {
                "acme/widgets": {
                    "mirror_dir": str(tmp_path / "m1"),
                    "release_prefix": "widgets",
                    "target_service": "widgets.service",
                    "target_node": "node-2",
                    "health_endpoints": [["/healthz", "alive"]],
                    "hmac_secret_env": "WIDGETS_CUSTOM_SECRET",
                },
                "acme/gadgets": {},
            },
        )
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(f))
        reg = load_repo_registry()
        assert len(reg) == 2
        w = reg.get("acme/widgets")
        assert w.mirror_dir == tmp_path / "m1"
        assert w.release_prefix == "widgets"
        assert w.target_service == "widgets.service"
        assert w.target_node == "node-2"
        assert w.health_endpoints == (("/healthz", "alive"),)
        assert w.hmac_secret_env == "WIDGETS_CUSTOM_SECRET"
        g = reg.get("acme/gadgets")
        assert g.release_prefix == "prismatic-engine"  # defaults fill in
        assert g.target_node == "local"

    def test_file_wins_over_env_list(self, clean_registry_env, tmp_path, monkeypatch):
        f = self._write(tmp_path, {"acme/only": {}})
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(f))
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS", "acme/ignored")
        reg = load_repo_registry()
        assert len(reg) == 1
        assert reg.default().full_name == "acme/only"

    def test_unknown_override_key_rejected(self, clean_registry_env, tmp_path, monkeypatch):
        f = self._write(tmp_path, {"acme/widgets": {"bogus_key": 1}})
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(f))
        with pytest.raises(ValueError, match="bogus_key"):
            load_repo_registry()

    def test_missing_file_rejected(self, clean_registry_env, monkeypatch):
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", "/no/such/repos.json")
        with pytest.raises(ValueError):
            load_repo_registry()

    def test_bad_json_rejected(self, clean_registry_env, tmp_path, monkeypatch):
        f = tmp_path / "repos.json"
        f.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(f))
        with pytest.raises(ValueError):
            load_repo_registry()


class TestUnknownRepo:
    def test_get_unknown_raises(self, clean_registry_env, monkeypatch):
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS", "acme/widgets")
        reg = load_repo_registry()
        with pytest.raises(UnknownRepoError, match="acme/nope"):
            reg.get("acme/nope")

    def test_registry_dataclass_guards(self):
        with pytest.raises(ValueError):
            DeployRepoRegistry(())
        r = DeployRepoConfig(full_name="a/b", mirror_dir=Path("/tmp/x"))
        with pytest.raises(ValueError):
            DeployRepoRegistry((r, r))
