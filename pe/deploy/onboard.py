"""Guided onboarding for the multi-repo deploy registry.

Implements the machine-doable half of ``docs/deploy-trigger.md`` §4
("Per-repo setup checklist"): validate the repo, register it, clone the
mirror, provision the per-repo HMAC secret, and print the exact human steps
(workflow file, Actions secrets, runner).

The human-doable steps (GitHub UI / ``gh``) are printed as copy-paste
commands — this module never pushes to GitHub and never deploys anything.

Stdlib only, no prints, no work at import time — the same contract as
``pe.deploy.install``, so it stays runnable on bare systems. ``run``
(subprocess) is injectable for tests.

Entry: ``prismatic deploy add-repo`` (presentation: ``prismatic/cli/deploy.py``).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pe.deploy import config as deploy_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Registry file this flow writes when PRISMATIC_DEPLOY_REPOS_FILE is unset.
DEFAULT_REGISTRY_FILENAME = "deploy-repos.json"

#: Receiver env file the per-repo HMAC secret is appended to (0600).
#: Same path the WS3 installer uses for the shared secret.
RECEIVER_ENV_RELATIVE = Path("env.d") / "deploy-receiver.env"

#: Canonical workflow file to copy into the onboarded repo.
WORKFLOW_PATH = ".github/workflows/post-merge-deploy.yml"
WORKFLOW_SOURCE_URL = (
    "https://github.com/mbgulden/prismatic-engine/blob/main/"
    ".github/workflows/post-merge-deploy.yml"
)


class OnboardError(Exception):
    """A loud, user-facing onboarding failure. Nothing was half-done."""


def _default_release_prefix(full_name: str) -> str:
    """Derive a unique, filesystem-safe release prefix from ``owner/repo``.

    Lowercased ``owner-repo`` with non-alphanumerics as dashes. Used for
    release dir names and live symlink suffixes, so two repos never share
    the production gateway's links. (``mbgulden/prismatic-engine`` itself is
    the default entry and keeps the config default ``prismatic-engine``; this
    derivation only applies to newly onboarded repos.)
    """
    owner, repo = full_name.split("/")
    slug = f"{owner}-{repo}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or deploy_config.DEFAULT_RELEASE_PREFIX


# ---------------------------------------------------------------------------
# Options / results (no prints; the CLI renders these)
# ---------------------------------------------------------------------------


@dataclass
class AddRepoOptions:
    """Knobs for :func:`add_repo`."""

    full_name: str
    dry_run: bool = False
    no_mirror: bool = False
    repo_url: str = ""  # mirror source override; default https://github.com/<full>.git
    target_service: str = ""  # default: registry default
    health_endpoints: tuple[tuple[str, str], ...] = ()
    registry_file: str = ""  # override for PRISMATIC_DEPLOY_REPOS_FILE
    release_prefix: str = ""  # default: derived from owner/repo (unique per repo)
    port: int = 0  # 0 = registry default
    extras: str | None = None  # None = registry default; "" = explicitly no extras
    smoke_import: str = ""  # default: registry default


@dataclass
class RepoStep:
    """One onboarding step outcome."""

    name: str
    status: str  # "ok" | "would-do" | "skipped"
    detail: str


@dataclass
class AddRepoResult:
    """Outcome of :func:`add_repo`."""

    ok: bool
    full_name: str
    steps: list[RepoStep] = field(default_factory=list)
    registry_file: str = ""
    registry_export_hint: str = ""  # non-empty when the user must export the file var
    secret_env_var: str = ""
    secret_value: str = ""  # shown ONCE by the CLI; never logged
    gh_secret_command: str = ""
    checklist: str = ""


@dataclass
class RepoCheck:
    """One validation check outcome."""

    name: str
    ok: bool
    detail: str


@dataclass
class ValidateResult:
    """Outcome of :func:`validate_repo`."""

    ok: bool
    full_name: str
    checks: list[RepoCheck] = field(default_factory=list)


@dataclass
class RepoStatus:
    """Read-only status row for :func:`list_repos`."""

    full_name: str
    mirror_dir: str
    mirror_present: bool
    target_service: str
    target_node: str
    secret: str  # "per-repo" | "shared-fallback" | "missing"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _default_run(cmd: list[str], timeout: int = 60) -> Any:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def default_registry_file() -> Path:
    """Registry file this flow manages.

    ``PRISMATIC_DEPLOY_REPOS_FILE`` when set, else
    ``<state_dir>/deploy-repos.json``.
    """
    raw = os.environ.get(deploy_config.REPOS_FILE_ENV_VAR, "").strip()
    if raw:
        return Path(raw).expanduser()
    return deploy_config.state_dir() / DEFAULT_REGISTRY_FILENAME


def _mirror_url(full_name: str, repo_url: str) -> str:
    if repo_url:
        return repo_url
    return f"https://github.com/{full_name}.git"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via temp+rename: no partial registry file, ever."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_registry_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        raise OnboardError(
            f"registry file {path} must be a JSON object mapping "
            "'owner/repo' to overrides"
        )
    return raw


def _env_file_vars(env_file: Path) -> set[str]:
    """Var names already present in a KEY=value env file."""
    found: set[str] = set()
    try:
        text = env_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return found
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        found.add(line.split("=", 1)[0].strip())
    return found


def _append_secret_var(env_file: Path, var: str, value: str, dry_run: bool) -> str:
    """Append VAR=value to the receiver env file (0600). Idempotent-safe.

    Returns "ok" | "would-do" | "skipped" (already present).
    """
    if var in _env_file_vars(env_file):
        return "skipped"
    if dry_run:
        return "would-do"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Per-repo deploy HMAC secrets. Do not commit.\n"
        "# Rotation: put a new value here, update the repo's Actions secret,\n"
        "# then restart prismatic-deploy-receiver.\n"
    )
    if not env_file.exists():
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(header)
    with open(env_file, "a", encoding="utf-8") as fh:
        fh.write(f"{var}={value}\n")
    os.chmod(env_file, 0o600)
    return "ok"


def _repo_reachable(url: str, run: Callable[..., Any]) -> tuple[bool, str]:
    """True when the repo answers ``git ls-remote`` (public or token via env)."""
    try:
        cp = run(["git", "ls-remote", url, "HEAD"], timeout=60)
    except Exception as exc:  # noqa: BLE001 - surfaced as detail, not raised
        return False, f"git ls-remote failed: {exc}"
    if getattr(cp, "returncode", 1) != 0:
        err = (getattr(cp, "stderr", "") or "").strip().splitlines()
        hint = err[0][:160] if err else "no output"
        return (
            False,
            f"git ls-remote {url} failed ({hint}). "
            "Check the owner/repo spelling; private repos need a token "
            "in GIT_ASKPASS / credential helper.",
        )
    return True, "reachable"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_repo(
    options: AddRepoOptions,
    run: Callable[..., Any] | None = None,
) -> AddRepoResult:
    """Onboard OWNER/REPO onto this receiver's deploy registry.

    Fail-closed: invalid name, unreachable repo, duplicate registration, or
    an unwritable registry file raises :class:`OnboardError` before any
    mutation. ``dry_run`` mutates nothing (the reachability probe still
    really runs — it is read-only, per the installer precedent).
    """
    run = run or _default_run
    full_name = options.full_name.strip()
    try:
        deploy_config._validate_full_name(full_name)  # noqa: SLF001 - same package
    except ValueError as exc:
        raise OnboardError(str(exc)) from exc

    url = _mirror_url(full_name, options.repo_url)
    reachable, reach_detail = _repo_reachable(url, run)
    if not reachable:
        raise OnboardError(
            f"cannot onboard {full_name!r}: repository not reachable: {reach_detail}"
        )

    registry = deploy_config.load_repo_registry()
    if full_name in registry:
        raise OnboardError(
            f"{full_name!r} is already in the deploy registry "
            f"({len(registry)} repos configured) — nothing to do."
        )

    target = (
        Path(options.registry_file).expanduser()
        if options.registry_file
        else default_registry_file()
    )
    existing = _read_registry_json(target)
    if full_name in existing:
        raise OnboardError(
            f"{full_name!r} is already in registry file {target} — nothing to do."
        )

    # Seed a fresh file from the CURRENT effective registry so behavior is
    # preserved (the default single-repo registry becomes an explicit entry).
    if not existing:
        existing = {repo.full_name: {} for repo in registry}

    overrides: dict[str, Any] = {}
    if options.target_service:
        overrides["target_service"] = options.target_service
    if options.health_endpoints:
        overrides["health_endpoints"] = [
            [path, name] for path, name in options.health_endpoints
        ]
    # The release prefix is ALWAYS explicit: it names the release dirs AND
    # the live symlinks. Auto-deriving from owner/repo guarantees two repos
    # can never share the production gateway's links by accident.
    overrides["release_prefix"] = (
        options.release_prefix.strip() or _default_release_prefix(full_name)
    )
    if options.port:
        overrides["port"] = options.port
    if options.extras is not None:
        overrides["extras"] = options.extras
    if options.smoke_import:
        overrides["smoke_import"] = options.smoke_import
    existing[full_name] = overrides

    steps = [RepoStep("validate", "ok", f"{full_name} reachable at {url}")]

    if options.dry_run:
        steps.append(
            RepoStep(
                "registry",
                "would-do",
                f"write {full_name} into {target} ({len(existing)} repos total)",
            )
        )
    else:
        try:
            _write_json_atomic(target, existing)
        except OSError as exc:
            raise OnboardError(f"cannot write registry file {target}: {exc}") from exc
        steps.append(
            RepoStep(
                "registry",
                "ok",
                f"registered {full_name} in {target} ({len(existing)} repos total)",
            )
        )

    # The registry only reads the file when the env var points at it.
    export_hint = ""
    if not os.environ.get(deploy_config.REPOS_FILE_ENV_VAR, "").strip():
        export_hint = (
            f"export {deploy_config.REPOS_FILE_ENV_VAR}={target}\n"
            "(add it to the receiver unit's Environment or "
            "~/.prismatic/.env so the receiver picks it up)"
        )

    # Mirror (skippable; skip-if-present for idempotency).
    cfg = deploy_config._config_for_name(  # noqa: SLF001 - same package
        full_name, overrides or None
    )
    mirror = cfg.mirror_dir
    if options.no_mirror:
        steps.append(RepoStep("mirror", "skipped", "--no-mirror"))
    elif (mirror / "HEAD").is_file():
        steps.append(
            RepoStep("mirror", "skipped", f"mirror already present at {mirror}")
        )
    elif options.dry_run:
        steps.append(
            RepoStep("mirror", "would-do", f"git clone --mirror {url} {mirror}")
        )
    else:
        mirror.parent.mkdir(parents=True, exist_ok=True)
        cp = run(["git", "clone", "--mirror", url, str(mirror)], timeout=300)
        if getattr(cp, "returncode", 1) != 0:
            err = (getattr(cp, "stderr", "") or "").strip()[:300]
            raise OnboardError(f"git clone --mirror {url} failed: {err or cp}")
        steps.append(RepoStep("mirror", "ok", f"cloned mirror to {mirror}"))

    # Per-repo HMAC secret -> receiver env file (0600), value shown once.
    secret_var = f"DEPLOY_HMAC_SECRET_{deploy_config.sanitize_env_name(full_name)}"
    secret_value = secrets.token_hex(32)
    env_file = deploy_config.state_dir() / RECEIVER_ENV_RELATIVE
    secret_status = _append_secret_var(
        env_file, secret_var, secret_value, options.dry_run
    )
    if secret_status == "skipped":
        steps.append(
            RepoStep(
                "secret",
                "skipped",
                f"{secret_var} already present in {env_file}",
            )
        )
        secret_value = ""  # nothing new to show
    else:
        steps.append(
            RepoStep(
                "secret",
                secret_status,
                f"{secret_var} -> {env_file} (0600)"
                if secret_status == "ok"
                else f"generate {secret_var} into {env_file} (0600)",
            )
        )

    gh_cmd = (
        f'gh secret set DEPLOY_HMAC_SECRET --repo {full_name} --body "{secret_value}"'
        if secret_value
        else f"# {secret_var} already stored receiver-side; set the repo's "
        "DEPLOY_HMAC_SECRET Actions secret to the matching value"
    )
    checklist = _render_repo_checklist(
        full_name, cfg, target, export_hint, secret_var, gh_cmd
    )
    return AddRepoResult(
        ok=True,
        full_name=full_name,
        steps=steps,
        registry_file=str(target),
        registry_export_hint=export_hint,
        secret_env_var=secret_var,
        secret_value=secret_value,
        gh_secret_command=gh_cmd,
        checklist=checklist,
    )


def _render_repo_checklist(
    full_name: str,
    cfg: deploy_config.DeployRepoConfig,
    registry_file: Path,
    export_hint: str,
    secret_var: str,
    gh_cmd: str,
) -> str:
    """The human half of onboarding: exact steps for THIS repo."""
    lines = [
        "",
        f"Finish onboarding {full_name} — the parts only a human can do:",
        "",
        "  1. Workflow file: copy into the repo at",
        f"     {WORKFLOW_PATH}",
        f"     from: {WORKFLOW_SOURCE_URL}",
        "     (the POST payload must include `repository: ${{ github.repository }}`)",
        "",
        "  2. Runner: the repo needs a self-hosted runner it can reach",
        "     (labels: self-hosted, linux, x64). GitHub-hosted runners are a",
        "     silent no-op — nothing listens on their localhost.",
        "",
        "  3. Actions secrets on the repo (Settings -> Secrets -> Actions):",
        f"     {gh_cmd}",
        "     optionally: gh secret set DEPLOY_RECEIVER_HOST "
        f'--repo {full_name} --body "http://<receiver-host>:9460"',
        "     (skip when the runner and receiver share a box)",
        "",
        "  4. Registry: the receiver reloads it per trigger — no restart.",
        f"     Entry: {full_name} -> mirror={cfg.mirror_dir}",
        f"             service={cfg.target_service} node={cfg.target_node}",
        f"             port={cfg.port} extras={cfg.extras or '(none)'}",
        f"             secret-var={secret_var}",
    ]
    if export_hint:
        lines += [
            "",
            "  Registry file is new — the receiver needs:",
            f"  {export_hint}",
        ]
    lines += [
        "",
        "  Validate without deploying:",
        f"    prismatic deploy validate-repo {full_name}",
        "",
    ]
    return "\n".join(lines)


def list_repos() -> list[RepoStatus]:
    """Read-only registry status, one row per repo."""
    registry = deploy_config.load_repo_registry()
    env_files = [
        deploy_config.state_dir() / ".env",
        deploy_config.state_dir() / RECEIVER_ENV_RELATIVE,
    ]
    present: set[str] = set()
    for ef in env_files:
        present |= _env_file_vars(ef)
    shared = "DEPLOY_HMAC_SECRET" in os.environ or "DEPLOY_HMAC_SECRET" in present
    rows = []
    for repo in registry:
        mirror_ok = (repo.mirror_dir / "HEAD").is_file()
        if repo.hmac_secret_env in os.environ or repo.hmac_secret_env in present:
            secret = "per-repo"
        elif shared:
            secret = "shared-fallback"
        else:
            secret = "missing"
        rows.append(
            RepoStatus(
                full_name=repo.full_name,
                mirror_dir=str(repo.mirror_dir),
                mirror_present=mirror_ok,
                target_service=repo.target_service,
                target_node=repo.target_node,
                secret=secret,
            )
        )
    return rows


def validate_repo(
    full_name: str,
    run: Callable[..., Any] | None = None,
) -> ValidateResult:
    """Dry-run proof that OWNER/REPO would deploy. Zero side effects.

    Never touches target_service, never POSTs, never writes. Fails loud
    with the exact fix for each failing check.
    """
    run = run or _default_run
    full_name = full_name.strip()
    checks: list[RepoCheck] = []

    try:
        deploy_config._validate_full_name(full_name)  # noqa: SLF001 - same package
        checks.append(RepoCheck("name", True, "valid 'owner/repo'"))
    except ValueError as exc:
        return ValidateResult(False, full_name, [RepoCheck("name", False, str(exc))])

    try:
        registry = deploy_config.load_repo_registry()
    except Exception as exc:  # noqa: BLE001 - reported as a check
        return ValidateResult(
            False, full_name, checks + [RepoCheck("registry", False, str(exc))]
        )
    if full_name not in registry:
        hint = ""
        if not os.environ.get(deploy_config.REPOS_FILE_ENV_VAR, "").strip():
            hint = (
                f" — did you export {deploy_config.REPOS_FILE_ENV_VAR}? "
                f"(`prismatic deploy add-repo {full_name}` writes "
                f"{default_registry_file()})"
            )
        return ValidateResult(
            False,
            full_name,
            checks
            + [
                RepoCheck(
                    "registry",
                    False,
                    f"{full_name!r} not in the deploy registry"
                    f" ({len(registry)} configured){hint}",
                )
            ],
        )
    checks.append(RepoCheck("registry", True, "registered"))
    cfg = registry.get(full_name)

    mirror = cfg.mirror_dir
    if not (mirror / "HEAD").is_file():
        return ValidateResult(
            False,
            full_name,
            checks
            + [
                RepoCheck(
                    "mirror",
                    False,
                    f"mirror missing at {mirror} — run "
                    f"`prismatic deploy add-repo {full_name}` (or clone it by hand)",
                )
            ],
        )
    checks.append(RepoCheck("mirror", True, f"present at {mirror}"))

    cp = run(["git", "--git-dir", str(mirror), "rev-parse", "HEAD"], timeout=30)
    if getattr(cp, "returncode", 1) != 0:
        return ValidateResult(
            False,
            full_name,
            checks + [RepoCheck("mirror-content", False, "mirror has no commits")],
        )
    checks.append(RepoCheck("mirror-content", True, "mirror has commits"))

    ok, detail = _repo_reachable(_mirror_url(full_name, ""), run)
    checks.append(
        RepoCheck(
            "upstream",
            ok,
            detail if ok else f"upstream unreachable: {detail}",
        )
    )
    if not ok:
        return ValidateResult(False, full_name, checks)

    env_file = deploy_config.state_dir() / RECEIVER_ENV_RELATIVE
    have = _env_file_vars(env_file) | {
        k for k in os.environ if k.startswith("DEPLOY_HMAC_SECRET")
    }
    if cfg.hmac_secret_env in have:
        checks.append(RepoCheck("secret", True, f"{cfg.hmac_secret_env} resolvable"))
    elif "DEPLOY_HMAC_SECRET" in have:
        checks.append(RepoCheck("secret", True, "shared DEPLOY_HMAC_SECRET fallback"))
    else:
        return ValidateResult(
            False,
            full_name,
            checks
            + [
                RepoCheck(
                    "secret",
                    False,
                    f"no HMAC secret for {full_name!r}: set "
                    f"{cfg.hmac_secret_env} (or shared DEPLOY_HMAC_SECRET) "
                    "where the receiver can read it",
                )
            ],
        )

    return ValidateResult(True, full_name, checks)
