"""Tests for WS5: kill the merge->deploy race.

On deploy trigger the receiver must fetch the routed repo's mirror BEFORE
SHA validation (reusing ``refresh_repo_mirror``), so a merge that beats the
15-minute mirror timer still deploys first try. #528's fail-fast is
preserved: a SHA that is still unknown after a fresh fetch refuses loudly.
"""

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

import pe.deploy.receiver as receiver_mod
from pe.deploy.receiver import DeployReceiverPipeline
from pe.deploy.manifest import DeployManifestStore


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _git_ok(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _remote_pair(tmp_path, name):
    """One fixture remote + a mirror that lacks the remote's tip SHA.

    Returns (remote, mirror, tip_sha). The mirror/tip gap IS the race: the
    trigger arrives before the mirror timer has fetched the merge.
    """
    remote = tmp_path / f"{name}.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=str(tmp_path))
    seed = tmp_path / f"{name}-seed"
    _git("clone", "-q", str(remote), str(seed), cwd=str(tmp_path))
    _git("config", "user.email", "t@example.com", cwd=str(seed))
    _git("config", "user.name", "t", cwd=str(seed))
    (seed / "prismatic").mkdir()
    (seed / "prismatic" / "__init__.py").write_text("")
    (seed / "f.txt").write_text("v1")
    _git("add", ".", cwd=str(seed))
    _git("commit", "-qm", "v1", cwd=str(seed))
    _git("push", "-q", "origin", "main", cwd=str(seed))

    # Mirror cloned BEFORE the tip commit -> it lacks the tip SHA.
    mirror = tmp_path / f"{name}-mirror"
    _git("clone", "-q", str(remote), str(mirror), cwd=str(tmp_path))

    # The "merge": a new commit pushed to the remote only.
    (seed / "f.txt").write_text("v2")
    _git("add", ".", cwd=str(seed))
    _git("commit", "-qm", "v2", cwd=str(seed))
    _git("push", "-q", "origin", "main", cwd=str(seed))
    tip = _git_ok("rev-parse", "HEAD", cwd=str(seed)).stdout.strip()

    # Sanity: the mirror really lacks the tip (the race condition holds).
    assert _git_ok("cat-file", "-t", tip, cwd=str(mirror)).returncode != 0
    return remote, mirror, tip


@pytest.fixture
def race_env(tmp_path, monkeypatch):
    """Hermetic single-repo env: no HOME/CWD/.env leakage."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in (
        "PRISMATIC_DEPLOY_REPOS",
        "PRISMATIC_DEPLOY_REPOS_FILE",
        "PRISMATIC_ALLOW_DEFAULT_HMAC",
        "PRISMATIC_STRICT_SECRETS",
        "DEPLOY_HMAC_SECRET",
        "PRISMATIC_STATE_DIR",
        "PRISMATIC_DEPLOY_SOURCE_REPO",
        "PRISMATIC_DEPLOY_DB",
        "PRISMATIC_ALERT_LOG",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / ".prismatic"))
    monkeypatch.setenv("PRISMATIC_DEPLOY_DB", str(tmp_path / "deploy_records.json"))
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))

    remote, mirror, tip = _remote_pair(tmp_path, "widgets")

    repos = {"acme/widgets": {"mirror_dir": str(mirror), "release_prefix": "widgets"}}
    repos_file = tmp_path / "repos.json"
    repos_file.write_text(json.dumps(repos), encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(repos_file))
    # Like production: the deploy source IS the persistent mirror.
    monkeypatch.setenv("PRISMATIC_DEPLOY_SOURCE_REPO", str(mirror))
    return {"tmp": tmp_path, "remote": remote, "mirror": mirror, "tip": tip}


class _FakeRunner:
    """Mirrors AtomicDeployRunner.deploy's SHA validation and nothing else.

    If the receiver regresses to validating the SHA before fetching, this
    fake refuses exactly like the real runner (#528), so the race test
    fails instead of silently passing.
    """

    def __init__(self, tmp_path):
        self.dry_run = False
        self.release_symlink = tmp_path / "releases" / "widgets"
        self.release_prefix = "widgets"

    def deploy(self, source_repo, pr_sha, branch="main"):
        proc = _git_ok(
            "rev-parse", "--verify", f"{pr_sha}^{{commit}}", cwd=str(source_repo)
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return (
                False,
                Path(""),
                f"pr_sha {pr_sha!r} is not a commit in {source_repo}; "
                "refusing to deploy",
            )
        version_dir = self.release_symlink.parent / (
            f"{self.release_prefix}-{pr_sha[:12]}"
        )
        version_dir.mkdir(parents=True, exist_ok=True)
        return True, version_dir, ""


class _FakeHealth:
    def check(self, version_dir, release_symlink, dry_run=False):
        return {"passed": True, "checks": {}, "details": {}}


class _FakeTransitions:
    def transition_issues_for_deploy(self, **kwargs):
        return []


class _FakeRedeployResult:
    skipped = False
    success = True
    rolled_back = False

    def to_dict(self):
        return {"skipped": False, "success": True, "rolled_back": False}


class _FakeRedeployer:
    def redeploy(self, **kwargs):
        return _FakeRedeployResult()


def _make_pipeline(env, **overrides):
    kwargs = dict(
        source_repo=env["mirror"],
        deploy_runner=_FakeRunner(env["tmp"]),
        health_checker=_FakeHealth(),
        transitioner=_FakeTransitions(),
        gateway_redeployer=_FakeRedeployer(),
        mirror_repo=env["mirror"],
        store=DeployManifestStore(db_path=env["tmp"] / "deploy_records.json"),
    )
    kwargs.update(overrides)
    return DeployReceiverPipeline(**kwargs)


def _payload(repository, pr_sha):
    return {
        "pr_sha": pr_sha,
        "pr_number": 7,
        "pr_title": "race",
        "deployer": "test",
        "repository": repository,
        "ref": "main",
    }


def _alerts(env):
    return (env["tmp"] / "alerts.log").read_text(encoding="utf-8")


class TestRaceFixed:
    def test_trigger_fetches_mirror_before_sha_validation(self, race_env):
        """The race: the mirror lacks the tip SHA until the fetch runs."""
        env = race_env
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload("acme/widgets", env["tip"]))
        assert record.success is True, record.failure_reason
        # The fetch really ran: the tip is resolvable in the mirror now.
        assert (
            _git_ok("cat-file", "-t", env["tip"], cwd=str(env["mirror"])).returncode
            == 0
        )
        assert "PostMergeDeploySucceeded" in _alerts(env)

    def test_predeploy_fetch_uses_routed_repo_mirror(self, tmp_path, monkeypatch):
        """With two routed repos, the fetch hits the TRIGGERED repo's mirror."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        for var in (
            "PRISMATIC_DEPLOY_REPOS",
            "PRISMATIC_DEPLOY_REPOS_FILE",
            "PRISMATIC_ALLOW_DEFAULT_HMAC",
            "PRISMATIC_STRICT_SECRETS",
            "DEPLOY_HMAC_SECRET",
            "PRISMATIC_STATE_DIR",
            "PRISMATIC_DEPLOY_SOURCE_REPO",
            "PRISMATIC_DEPLOY_DB",
            "PRISMATIC_ALERT_LOG",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / ".prismatic"))
        monkeypatch.setenv("PRISMATIC_DEPLOY_DB", str(tmp_path / "deploy_records.json"))
        monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))

        _, gadgets_mirror, gadgets_tip = _remote_pair(tmp_path, "gadgets")
        _, widgets_mirror, widgets_tip = _remote_pair(tmp_path, "widgets")

        # gadgets first so it is the registry default (the injected fake
        # runner is the default repo's runner).
        repos = {
            "acme/gadgets": {
                "mirror_dir": str(gadgets_mirror),
                "release_prefix": "gadgets",
            },
            "acme/widgets": {
                "mirror_dir": str(widgets_mirror),
                "release_prefix": "widgets",
            },
        }
        repos_file = tmp_path / "repos.json"
        repos_file.write_text(json.dumps(repos), encoding="utf-8")
        monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(repos_file))
        monkeypatch.setenv("PRISMATIC_DEPLOY_SOURCE_REPO", str(gadgets_mirror))

        seen = []
        orig = DeployReceiverPipeline.refresh_repo_mirror

        def spy(self, repo=None, mirror=None):
            seen.append(repo.full_name if repo is not None else None)
            return orig(self, repo=repo, mirror=mirror)

        monkeypatch.setattr(DeployReceiverPipeline, "refresh_repo_mirror", spy)

        env = {"tmp": tmp_path, "mirror": gadgets_mirror}
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload("acme/gadgets", gadgets_tip))

        assert record.success is True, record.failure_reason
        # The pre-deploy fetch AND the post-success refresh both ran, each
        # against the routed repo's mirror.
        assert seen == ["acme/gadgets", "acme/gadgets"]
        # The other repo's mirror was never touched: still lacks its tip.
        assert (
            _git_ok("cat-file", "-t", widgets_tip, cwd=str(widgets_mirror)).returncode
            != 0
        )

    def test_refresh_accepts_explicit_mirror_path(self, race_env):
        """refresh_repo_mirror() accepts a per-repo mirror path (WS5)."""
        env = race_env
        pipeline = DeployReceiverPipeline(
            store=DeployManifestStore(db_path=env["tmp"] / "deploy_records.json")
        )
        result = pipeline.refresh_repo_mirror(mirror=env["mirror"])
        assert result == {"refreshed": True, "reason": "fetch-ok"}
        assert (
            _git_ok("cat-file", "-t", env["tip"], cwd=str(env["mirror"])).returncode
            == 0
        )

    def test_unknown_sha_after_fresh_fetch_still_refuses(self, race_env):
        """#528 fail-fast preserved: fetch ok + unknown SHA -> loud refusal."""
        env = race_env
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload("acme/widgets", "e" * 40))
        assert record.success is False
        assert "is not a commit" in (record.failure_reason or "")
        # The fetch succeeded, so no fetch complaint is attached.
        assert "pre-deploy mirror fetch" not in (record.failure_reason or "")
        assert "PostMergeDeployFailed" in _alerts(env)


class TestFetchFailure:
    def test_fetch_failure_refuses_cleanly_and_names_fetch(self, race_env):
        """Unreachable remote + unknown SHA -> refusal naming the fetch."""
        env = race_env
        _git(
            "remote",
            "set-url",
            "origin",
            str(env["tmp"] / "nope.git"),
            cwd=str(env["mirror"]),
        )
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload("acme/widgets", "f" * 40))
        assert record.success is False
        assert "pre-deploy mirror fetch" in (record.failure_reason or "")
        alerts = _alerts(env)
        assert "PostMergeDeployFailed" in alerts
        assert "pre-deploy mirror fetch" in alerts

    def test_fetch_timeout_is_enforced_and_never_hangs(self, race_env, monkeypatch):
        """The fetch timeout is passed through and TimeoutExpired is fail-closed."""
        env = race_env

        def hanging_run(*args, **kwargs):
            assert kwargs.get("timeout") == receiver_mod.MIRROR_FETCH_TIMEOUT_S
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

        monkeypatch.setattr(receiver_mod.subprocess, "run", hanging_run)
        pipeline = DeployReceiverPipeline(mirror_repo=env["mirror"])
        start = time.monotonic()
        result = pipeline.refresh_repo_mirror()
        elapsed = time.monotonic() - start
        assert result["refreshed"] is False
        assert "fetch-error" in result["reason"]
        assert elapsed < 10, f"fetch took {elapsed:.1f}s: it hung"


class TestConcurrency:
    def test_concurrent_triggers_do_not_corrupt_mirror(self, race_env, monkeypatch):
        """Four rapid fetches of the same mirror: serialized, mirror healthy."""
        env = race_env
        pipeline = DeployReceiverPipeline(mirror_repo=env["mirror"])

        concurrent = {"current": 0, "max": 0}
        guard = threading.Lock()
        real_run = receiver_mod.subprocess.run

        def counting_run(*args, **kwargs):
            with guard:
                concurrent["current"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["current"])
            try:
                time.sleep(0.3)  # widen the race window
                return real_run(*args, **kwargs)
            finally:
                with guard:
                    concurrent["current"] -= 1

        monkeypatch.setattr(receiver_mod.subprocess, "run", counting_run)

        results = []
        errors = []

        def worker():
            try:
                results.append(pipeline.refresh_repo_mirror())
            except Exception as exc:  # fail-closed means this never happens
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors, errors
        assert len(results) == 4
        assert all(r["refreshed"] for r in results), results
        assert concurrent["max"] == 1, (
            f"fetches were not serialized (max concurrent: {concurrent['max']})"
        )
        # Mirror is healthy and has the tip.
        assert (
            _git_ok("rev-parse", "--verify", "HEAD", cwd=str(env["mirror"])).returncode
            == 0
        )
        assert _git_ok("fsck", "--no-dangling", cwd=str(env["mirror"])).returncode == 0
        assert (
            _git_ok("cat-file", "-t", env["tip"], cwd=str(env["mirror"])).returncode
            == 0
        )
