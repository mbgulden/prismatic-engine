from __future__ import annotations

import json

from prismatic.core.pwp_state import PWPRunState, PWPRunStateStore


def test_run_state_preserves_theme_metadata(tmp_path):
    store_path = tmp_path / "pwp_run_state.json"
    store = PWPRunStateStore(store_path=str(store_path))

    record = store.record_deploy(
        run_id="run-1",
        client_id="sentinelitad",
        target="cloudflare-pages",
        artifact_sha="sha256:artifact",
        deployed_by="ned",
        reversible=True,
        commit_hash="git:abc123",
        theme_id="pwp.theme.trust-light",
        theme_version="0.1.0",
        theme_hash="sha256:theme",
        token_hash="sha256:tokens",
        module_hash="sha256:modules",
        content_hash="sha256:content",
        theme_engine_compatibility=">=0.2.0",
        theme_schema_version="2026-07-09",
    )

    assert record.theme_id == "pwp.theme.trust-light"
    assert record.theme_version == "0.1.0"
    assert record.token_hash == "sha256:tokens"
    assert record.module_hash == "sha256:modules"
    assert record.theme_engine_compatibility == ">=0.2.0"
    assert record.theme_schema_version == "2026-07-09"

    reloaded = PWPRunStateStore(store_path=str(store_path)).get_run("run-1")
    assert reloaded is not None
    assert reloaded.theme_id == "pwp.theme.trust-light"
    assert reloaded.theme_version == "0.1.0"
    assert reloaded.theme_hash == "sha256:theme"
    assert reloaded.token_hash == "sha256:tokens"
    assert reloaded.module_hash == "sha256:modules"
    assert reloaded.content_hash == "sha256:content"
    assert reloaded.theme_engine_compatibility == ">=0.2.0"
    assert reloaded.theme_schema_version == "2026-07-09"

    stored_json = json.loads(store_path.read_text())
    assert stored_json["run-1"]["theme_id"] == "pwp.theme.trust-light"
    assert stored_json["run-1"]["module_hash"] == "sha256:modules"


def test_run_state_loads_legacy_and_future_records():
    record = PWPRunState.from_dict(
        {
            "run_id": "legacy-run",
            "client_id": "legacy-client",
            "target": "file",
            "artifact_sha": "sha256:artifact",
            "deployed_at": "2026-07-09T00:00:00+00:00",
            "deployed_by": "ned",
            "future_field": "ignored",
        }
    )

    assert record.run_id == "legacy-run"
    assert record.theme_id is None
    assert record.theme_version is None
    assert record.token_hash is None
    assert record.module_hash is None
    assert record.theme_engine_compatibility is None


def test_should_skip_deploy_compares_theme_token_module_and_content_hashes(tmp_path):
    store = PWPRunStateStore(store_path=str(tmp_path / "pwp_run_state.json"))
    store.record_deploy(
        run_id="run-1",
        client_id="sentinelitad",
        target="cloudflare-pages",
        artifact_sha="sha256:artifact",
        deployed_by="ned",
        commit_hash="git:abc123",
        theme_hash="sha256:theme",
        token_hash="sha256:tokens",
        module_hash="sha256:modules",
        content_hash="sha256:content",
    )

    assert store.should_skip_deploy(
        client_id="sentinelitad",
        target="cloudflare-pages",
        commit_hash="git:abc123",
        theme_hash="sha256:theme",
        token_hash="sha256:tokens",
        module_hash="sha256:modules",
        content_hash="sha256:content",
    )

    assert not store.should_skip_deploy(
        client_id="sentinelitad",
        target="cloudflare-pages",
        commit_hash="git:abc123",
        theme_hash="sha256:theme",
        token_hash="sha256:tokens-v2",
        module_hash="sha256:modules",
        content_hash="sha256:content",
    )

    assert not store.should_skip_deploy(
        client_id="sentinelitad",
        target="cloudflare-pages",
    )


def test_rollback_record_captures_prior_theme_token_and_content_state(tmp_path):
    store_path = tmp_path / "pwp_run_state.json"
    store = PWPRunStateStore(store_path=str(store_path))

    store.record_deploy(
        run_id="run-1",
        client_id="sentinelitad",
        target="cloudflare-pages",
        artifact_sha="sha256:artifact-v1",
        deployed_by="ned",
        reversible=True,
        theme_id="pwp.theme.trust-light",
        theme_version="0.1.0",
        theme_hash="sha256:theme-v1",
        token_hash="sha256:tokens-v1",
        module_hash="sha256:modules-v1",
        content_hash="sha256:content-v1",
        theme_engine_compatibility=">=0.2.0",
        theme_schema_version="2026-07-09",
    )

    run_2 = store.record_deploy(
        run_id="run-2",
        client_id="sentinelitad",
        target="cloudflare-pages",
        artifact_sha="sha256:artifact-v2",
        deployed_by="ned",
        reversible=True,
        theme_id="pwp.theme.trust-light",
        theme_version="0.2.0",
        theme_hash="sha256:theme-v2",
        token_hash="sha256:tokens-v2",
        module_hash="sha256:modules-v2",
        content_hash="sha256:content-v2",
        theme_engine_compatibility=">=0.3.0",
        theme_schema_version="2026-07-10",
    )

    assert run_2.previous_run_id == "run-1"
    assert run_2.previous_artifact_sha == "sha256:artifact-v1"
    assert run_2.previous_theme_id == "pwp.theme.trust-light"
    assert run_2.previous_theme_version == "0.1.0"
    assert run_2.previous_theme_hash == "sha256:theme-v1"
    assert run_2.previous_token_hash == "sha256:tokens-v1"
    assert run_2.previous_module_hash == "sha256:modules-v1"
    assert run_2.previous_content_hash == "sha256:content-v1"
    assert run_2.previous_theme_engine_compatibility == ">=0.2.0"
    assert run_2.previous_theme_schema_version == "2026-07-09"
    assert run_2.rollback_restore_metadata() == {
        "previous_run_id": "run-1",
        "previous_artifact_sha": "sha256:artifact-v1",
        "theme_id": "pwp.theme.trust-light",
        "theme_version": "0.1.0",
        "theme_hash": "sha256:theme-v1",
        "token_hash": "sha256:tokens-v1",
        "module_hash": "sha256:modules-v1",
        "content_hash": "sha256:content-v1",
        "theme_engine_compatibility": ">=0.2.0",
        "theme_schema_version": "2026-07-09",
    }

    stored_json = json.loads(store_path.read_text())
    assert stored_json["run-2"]["previous_run_id"] == "run-1"
    assert stored_json["run-2"]["previous_token_hash"] == "sha256:tokens-v1"
    assert stored_json["run-2"]["previous_content_hash"] == "sha256:content-v1"


def test_non_reversible_deploy_does_not_become_theme_rollback_target(tmp_path):
    store = PWPRunStateStore(store_path=str(tmp_path / "pwp_run_state.json"))
    store.record_deploy(
        run_id="run-1",
        client_id="sentinelitad",
        target="cloudflare-pages",
        artifact_sha="sha256:artifact-v1",
        deployed_by="ned",
        reversible=True,
        theme_version="0.1.0",
        token_hash="sha256:tokens-v1",
    )
    store.record_deploy(
        run_id="run-2",
        client_id="sentinelitad",
        target="cloudflare-pages",
        artifact_sha="sha256:artifact-v2",
        deployed_by="ned",
        reversible=False,
        theme_version="0.2.0",
        token_hash="sha256:tokens-v2",
    )

    run_3 = store.record_deploy(
        run_id="run-3",
        client_id="sentinelitad",
        target="cloudflare-pages",
        artifact_sha="sha256:artifact-v3",
        deployed_by="ned",
        reversible=True,
        theme_version="0.3.0",
        token_hash="sha256:tokens-v3",
    )

    assert run_3.previous_run_id == "run-1"
    assert run_3.previous_artifact_sha == "sha256:artifact-v1"
    assert run_3.previous_theme_version == "0.1.0"
    assert run_3.previous_token_hash == "sha256:tokens-v1"
