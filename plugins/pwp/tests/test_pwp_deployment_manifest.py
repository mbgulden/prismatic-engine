from __future__ import annotations

from typing import Any, Dict, List

from prismatic.core.registry import PWPPluginRunner
from prismatic.interface.hooks import HOOK_ON_DEPLOY


class RecordingLoader:
    core_version = "1.0.0"

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def execute_hook(self, hook_name: str, *args: Any, **kwargs: Any) -> None:
        self.events.append({"hook": hook_name, "args": args, "kwargs": kwargs})


def test_pwp_runner_attaches_theme_provenance_to_deployment_manifest() -> None:
    """GRO-3713: deploy artifacts carry canonical theme provenance."""
    loader = RecordingLoader()
    runner = PWPPluginRunner(loader)  # type: ignore[arg-type]

    result = runner.run(
        pipeline_id="GRO-3713-provenance",
        context={
            "themeProvenance": {
                "themeId": "pwp.theme.trust-light",
                "themeVersion": "0.1.0",
                "tokenHash": "sha256:token",
                "moduleHash": "sha256:module",
                "contentHash": "sha256:content",
                "sourceCommit": "abc1234",
            }
        },
        stages=[("build", lambda _ctx: "ok")],
        deploy_target="cloudflare-pages",
        deploy_artifact_provider=lambda _r: {"url": "https://example.test"},
    )

    manifest = result["deploymentManifest"]
    assert manifest == {
        "pipelineId": "GRO-3713-provenance",
        "themeId": "pwp.theme.trust-light",
        "themeVersion": "0.1.0",
        "tokenHash": "sha256:token",
        "moduleHash": "sha256:module",
        "contentHash": "sha256:content",
        "engineVersion": "1.0.0",
        "sourceCommit": "abc1234",
    }

    deploy_events = [e for e in loader.events if e["hook"] == HOOK_ON_DEPLOY]
    assert len(deploy_events) == 1
    _pipeline_id, target, artifact = deploy_events[0]["args"]
    assert target == "cloudflare-pages"
    assert artifact["deploymentManifest"] == manifest


def test_pwp_runner_accepts_snake_case_theme_provenance_aliases() -> None:
    """PWP callers can pass Pythonic snake_case and still get manifest JSON."""
    loader = RecordingLoader()
    runner = PWPPluginRunner(loader)  # type: ignore[arg-type]

    result = runner.run(
        pipeline_id="GRO-3713-aliases",
        context={
            "theme_provenance": {
                "theme_id": "pwp.theme.saas-product",
                "theme_version": "2.0.0",
                "token_hash": "sha256:token2",
                "module_hash": "sha256:module2",
                "content_hash": "sha256:content2",
                "source_commit": "def5678",
            }
        },
        stages=[("build", lambda _ctx: "ok")],
        deploy_target="cloudflare-pages",
        deploy_artifact_provider=lambda _r: {"deployment_manifest": {"target": "cf"}},
    )

    manifest = result["deploymentManifest"]
    assert manifest["target"] == "cf"
    assert manifest["themeId"] == "pwp.theme.saas-product"
    assert manifest["themeVersion"] == "2.0.0"
    assert manifest["tokenHash"] == "sha256:token2"
    assert manifest["moduleHash"] == "sha256:module2"
    assert manifest["contentHash"] == "sha256:content2"
    assert manifest["engineVersion"] == "1.0.0"
    assert manifest["sourceCommit"] == "def5678"
