"""One-command installer for Prismatic's post-merge deploy plane (WS3).

Entry point: ``python -m pe.deploy.install``.

Builds a working deploy-plane install on a fresh Linux machine::

    1. prerequisites -- python >= 3.11, git, a WS2 process manager,
                       free ports 9460/9000, sane disk space
    2. layout        -- ~/.prismatic/{versions,releases,venv_receiver,
                       venv_gateway,repos,db,logs,env.d}
    3. mirror        -- git mirror clone per repo in the WS1 registry
    4. fetch_timer   -- per-repo mirror-fetch service+timer, installed and
                       enabled via the WS2 process-manager abstraction
    5. venvs         -- receiver + gateway virtualenvs with the package installed
    6. units         -- rendered receiver/gateway units, installed and enabled
                       via the process-manager abstraction
    7. secrets       -- generated DEPLOY_HMAC_SECRET in env.d/deploy-receiver.env
                       (0600); never a shipped default
    8. auth          -- auth provider selection persisted to instance.json
                       (0600) with the identity->role mapping; basic-auth
                       also writes auth/basic-auth-users.json (0600)
    9. checklist     -- printed per-repo trigger setup checklist (always runs)

Extends (does not fork) install.sh's flow: OS detection, config-dir
conventions, and "print next steps" at the end. Every numbered step is
independently skippable via ``--skip-steps`` and the whole run is
previewable via ``--dry-run`` (prerequisites still really run; mutating
steps report what they *would* do instead of doing it).

Fail-closed: the installer REFUSES to run when the layout already exists
(``InstallRefused``, exit 2); ``--upgrade`` is deferred per the plan. A
failed step stops the run loudly (exit 1). The installer is not
transactional -- clean a failed run with ``rm -rf ~/.prismatic`` and run
it again from scratch.

The installer operates on the process HOME (``--home`` sets HOME for this
process up front, so every config.py path default agrees). ``PRISMATIC_*``
env vars are honored wherever config.py defines them.

Enroll-node mode (WS7): ``python -m pe.deploy.install --enroll-node
--node-name athens --node-address athens --node-ssh-user ubuntu
[--node-confirm]`` verifies a tailnet node (mesh resolution, tailscale
ping, BatchMode ssh, platform probe) and registers it in the node registry
(``$PRISMATIC_NODES_FILE`` or ``~/.prismatic/nodes.yaml``). Registration is
a local file write only and REQUIRES ``--node-confirm`` -- nodes are never
auto-enrolled. ``--dry-run`` previews the checks without writing.

Stdlib only (plus pe.deploy.config / pe.deploy.process_manager*, which are
stdlib-only too), and no work happens at import time -- so this module runs
on a bare system python with no Prismatic dependencies installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pe.deploy import config as deploy_config
from pe.deploy import instance as deploy_instance
from pe.deploy.process_manager import ProcessManager, ProcessManagerError
from pe.deploy.process_manager_systemd import SystemdProcessManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Canonical receiver port (cf. pe.deploy.receiver.RECEIVER_PORT -- that
#: module is not importable on a bare python, so the value is repeated here).
DEFAULT_RECEIVER_PORT = 9460

#: Gateway port default comes from the centralized config (PRISMATIC_PORT).
DEFAULT_GATEWAY_PORT = deploy_config.DEFAULT_GATEWAY_PORT

#: Install steps in run order. Every step is skippable via --skip-steps.
STEP_PREREQUISITES = "prerequisites"
STEP_LAYOUT = "layout"
STEP_MIRROR = "mirror"
STEP_FETCH_TIMER = "fetch_timer"
STEP_VENVS = "venvs"
STEP_UNITS = "units"
STEP_SECRETS = "secrets"
STEP_AUTH = "auth"
STEP_NAMES = (
    STEP_PREREQUISITES,
    STEP_LAYOUT,
    STEP_MIRROR,
    STEP_FETCH_TIMER,
    STEP_VENVS,
    STEP_UNITS,
    STEP_SECRETS,
    STEP_AUTH,
)

#: Subdirs created under the state dir (the deploy-plane layout).
LAYOUT_SUBDIRS = (
    "versions",
    "releases",
    "venv_receiver",
    "venv_gateway",
    "repos",
    "db",
    "logs",
    "env.d",
)

#: systemd unit names installed by this installer.
RECEIVER_UNIT_NAME = "prismatic-deploy-receiver.service"
GATEWAY_UNIT_NAME = "prismatic-gateway.service"

#: Where the generated HMAC secret lives (0600), relative to the state dir.
RECEIVER_ENV_RELATIVE = Path("env.d") / "deploy-receiver.env"

#: Marker file the installer writes into the state dir root. Its presence
#: tells doctor (and a future --upgrade) "this layout was created by the WS3
#: installer". Grandfathered pre-WS3 layouts have no marker and stay neutral.
INSTALL_MARKER_NAME = ".deploy-install.json"

#: Installer layout version stamped into the marker.
INSTALL_VERSION = 1

#: Template placeholders look like {{UPPER_SNAKE}}.
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")

#: Exit codes: 0 ok, 1 step failed, 2 refused / bad usage.
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2

UNITS_DIR = Path(__file__).resolve().parent / "units"


# ---------------------------------------------------------------------------
# Errors / results
# ---------------------------------------------------------------------------


class InstallError(Exception):
    """A hard installer failure: stop loudly, exit 1."""


class InstallRefused(InstallError):
    """The installer refused to run (existing layout); exit 2."""


@dataclass
class StepResult:
    """Outcome of one install step."""

    name: str
    status: str  # "ok" | "skipped" | "would-do" | "failed"
    detail: str = ""


@dataclass
class InstallOptions:
    """Installer knobs (see --help)."""

    dry_run: bool = False
    skip_steps: tuple[str, ...] = ()
    package_source: str | None = None
    repo_url: str | None = None
    unit_scope: str = "user"  # "user" | "system"
    receiver_port: int = DEFAULT_RECEIVER_PORT
    gateway_port: int = DEFAULT_GATEWAY_PORT
    auth_provider: str = deploy_instance.DEFAULT_AUTH_PROVIDER
    admin_identities: tuple[str, ...] = ()
    basic_auth_users: tuple[str, ...] = ()


@dataclass
class InstallResult:
    """Full run outcome."""

    ok: bool
    steps: list[StepResult] = field(default_factory=list)
    state_dir: str = ""


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def render_template(name: str, mapping: dict[str, str]) -> str:
    """Render a unit template with ``{{PLACEHOLDER}}`` substitution.

    Lines that render empty (e.g. an empty ``{{USER_DIRECTIVE}}``) are
    dropped. Any placeholder left over after substitution -- or any
    placeholder with no mapping entry -- raises :class:`InstallError`.
    """
    path = UNITS_DIR / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallError(f"template {name} unreadable: {exc}") from exc

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in mapping:
            raise InstallError(
                f"template {name}: no value provided for placeholder '{{{{{key}}}}}'"
            )
        return str(mapping[key])

    rendered = _PLACEHOLDER_RE.sub(_sub, text)
    # Drop lines left empty by substitution, then fail on leftovers.
    rendered = "\n".join(line for line in rendered.splitlines() if line.strip()) + "\n"
    leftover = _PLACEHOLDER_RE.search(rendered)
    if leftover:
        raise InstallError(
            f"template {name}: unrendered placeholder {leftover.group(0)!r}"
        )
    return rendered


def fetch_unit_base(full_name: str, default_full_name: str) -> str:
    """Base unit name (no .service/.timer suffix) for a repo's mirror fetch.

    The default repo keeps today's unit names (``prismatic-repo-mirror-fetch``);
    additional repos get a slug suffix so N repos can each have a timer.
    """
    if full_name == default_full_name:
        return "prismatic-repo-mirror-fetch"
    slug = re.sub(r"[^a-z0-9]+", "-", full_name.lower()).strip("-")
    return f"prismatic-repo-mirror-fetch-{slug}"


# ---------------------------------------------------------------------------
# Manager / source selection
# ---------------------------------------------------------------------------


def _default_manager() -> ProcessManager:
    """Pick the platform process manager (WS2 abstraction).

    Linux + systemctl -> SystemdProcessManager. Anything else fails with a
    clear message (launchd/Windows are deferred per the plan).
    """
    if sys.platform == "darwin":
        raise InstallError(
            "macOS launchd process manager is deferred per the "
            "deploy-portability plan; Linux with systemd is the target"
        )
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        return SystemdProcessManager()
    raise InstallError(
        "no supported process manager on this platform: need Linux with "
        "systemctl on PATH (launchd/Windows implementations are deferred)"
    )


def _default_package_source() -> str:
    """Pip spec for the package installed into the venvs.

    The enclosing checkout when this runs from one, else the default repo's
    GitHub URL (pip installs from git).
    """
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file():
        return str(checkout)
    return f"git+https://github.com/{deploy_config.DEFAULT_REPO_FULL_NAME}.git"


def _default_run(argv: list[str], **kwargs: Any) -> Any:
    return subprocess.run(
        argv,
        capture_output=kwargs.get("capture_output", True),
        text=True,
        timeout=kwargs.get("timeout"),
        cwd=kwargs.get("cwd"),
    )


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def _port_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _check_prerequisites(
    options: InstallOptions, manager: ProcessManager, state_dir: Path
) -> StepResult:
    """Step 1: fail fast when the box cannot host the deploy plane."""
    problems: list[str] = []
    notes: list[str] = []

    py = sys.version_info
    if py < (3, 11):
        problems.append(
            f"python >= 3.11 required (found {py.major}.{py.minor}.{py.micro})"
        )
    else:
        notes.append(f"python {py.major}.{py.minor}.{py.micro}")

    if not shutil.which("git"):
        problems.append("git not found on PATH")
    else:
        notes.append("git found")

    notes.append(f"process manager: {type(manager).__name__}")
    if isinstance(manager, SystemdProcessManager) and not shutil.which(
        manager.systemctl_bin
    ):
        problems.append(f"systemctl not found at {manager.systemctl_bin!r}")
    if options.unit_scope == "system":
        # Fail fast instead of hanging on a sudo password prompt (WS2
        # privilege contract): system-scope unit installs need passwordless
        # sudo for systemctl.
        probe = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode != 0:
            problems.append(
                "system unit scope needs passwordless sudo for systemctl "
                "('sudo -n true' failed); use --unit-scope=user or configure "
                "NOPASSWD sudo"
            )

    if options.dry_run:
        notes.append("port checks skipped in dry-run")
    else:
        for port, label in (
            (options.receiver_port, "receiver"),
            (options.gateway_port, "gateway"),
        ):
            if _port_free(port):
                notes.append(f"port {port} ({label}) free")
            else:
                problems.append(f"port {port} ({label}) is already in use")

    try:
        free_gb = shutil.disk_usage(str(state_dir.parent)).free / (1024**3)
        min_gb = deploy_config.min_free_gb()
        if free_gb < min_gb:
            problems.append(
                f"disk free {free_gb:.1f} GB < required {min_gb:.1f} GB "
                f"(PRISMATIC_DEPLOY_MIN_FREE_GB)"
            )
        else:
            notes.append(f"disk free {free_gb:.1f} GB")
    except OSError as exc:
        problems.append(f"cannot stat disk for {state_dir.parent}: {exc}")

    if problems:
        raise InstallError("prerequisites failed: " + "; ".join(problems))
    return StepResult(STEP_PREREQUISITES, "ok", "; ".join(notes))


def _refuse_if_layout_exists(state_dir: Path) -> None:
    """The installer never clobbers: refuse when the layout already exists."""
    if state_dir.exists() and any(state_dir.iterdir()):
        raise InstallRefused(
            f"{state_dir} already exists and is not empty: refusing to "
            "install over an existing layout (--upgrade is deferred per the "
            "plan; remove the directory and re-run for a fresh install)"
        )


def _create_layout(state_dir: Path, dry_run: bool) -> StepResult:
    _refuse_if_layout_exists(state_dir)
    if dry_run:
        return StepResult(
            STEP_LAYOUT,
            "would-do",
            f"create {state_dir}/{{{','.join(LAYOUT_SUBDIRS)}}} (0700)",
        )
    # NOTE: config.versions_dir()/deploy_db_path() mkdir as a side effect;
    # the installer computes layout paths from config.state_dir() directly
    # so a dry run stays side-effect free.
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    for sub in LAYOUT_SUBDIRS:
        (state_dir / sub).mkdir(parents=True, exist_ok=True)
    marker = state_dir / INSTALL_MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "installer": "pe.deploy.install",
                "install_version": INSTALL_VERSION,
                "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return StepResult(
        STEP_LAYOUT, "ok", f"created {len(LAYOUT_SUBDIRS)} dirs under {state_dir}"
    )


def _mirror_url(full_name: str, default_full_name: str, options: InstallOptions) -> str:
    if full_name == default_full_name and options.repo_url:
        return options.repo_url
    return f"https://github.com/{full_name}.git"


def _clone_mirrors(
    registry: deploy_config.DeployRepoRegistry,
    options: InstallOptions,
    run: Callable[..., Any],
    dry_run: bool,
) -> StepResult:
    """Step 3: git mirror clone per repo in the registry."""
    default_name = registry.default().full_name
    planned = [
        (repo, _mirror_url(repo.full_name, default_name, options)) for repo in registry
    ]
    if dry_run:
        detail = "; ".join(f"{r.full_name} -> {m}" for r, m in planned)
        return StepResult(STEP_MIRROR, "would-do", f"git clone --mirror: {detail}")
    for repo, url in planned:
        mirror_dir = repo.mirror_dir
        if mirror_dir.exists() and any(mirror_dir.iterdir()):
            raise InstallError(
                f"mirror dir {mirror_dir} already exists and is not empty: "
                "refusing to clone over it"
            )
        mirror_dir.parent.mkdir(parents=True, exist_ok=True)
        cp = run(["git", "clone", "--mirror", url, str(mirror_dir)], timeout=300)
        if getattr(cp, "returncode", 1) != 0:
            err = (getattr(cp, "stderr", "") or "").strip()[:500]
            raise InstallError(f"git clone --mirror {url} failed: {err or cp}")
    return StepResult(
        STEP_MIRROR, "ok", f"cloned {len(planned)} mirror(s) (git --mirror)"
    )


def _install_fetch_timer(
    registry: deploy_config.DeployRepoRegistry,
    options: InstallOptions,
    manager: ProcessManager,
    dry_run: bool,
) -> StepResult:
    """Step 4: per-repo mirror-fetch service+timer via the process manager."""
    default_name = registry.default().full_name
    git_bin = shutil.which("git") or "/usr/bin/git"
    units: list[tuple[str, str]] = []
    for repo in registry:
        base = fetch_unit_base(repo.full_name, default_name)
        svc = render_template(
            "mirror-fetch.service.template",
            {
                "REPO_FULL_NAME": repo.full_name,
                "MIRROR_DIR": str(repo.mirror_dir),
                "GIT_BIN": git_bin,
            },
        )
        timer = render_template(
            "mirror-fetch.timer.template",
            {
                "REPO_FULL_NAME": repo.full_name,
                "FETCH_SERVICE_NAME": f"{base}.service",
            },
        )
        units.append((f"{base}.service", svc))
        units.append((f"{base}.timer", timer))
    if dry_run:
        names = ", ".join(name for name, _ in units)
        return StepResult(
            STEP_FETCH_TIMER,
            "would-do",
            f"install+enable via {type(manager).__name__} "
            f"(scope={options.unit_scope}): {names}",
        )
    try:
        for name, content in units:
            manager.install_unit(name, content, scope=options.unit_scope)
        for repo in registry:
            base = fetch_unit_base(repo.full_name, default_name)
            manager.enable(f"{base}.timer", scope=options.unit_scope)
    except ProcessManagerError as exc:
        raise InstallError(f"fetch timer install failed: {exc}") from exc
    return StepResult(
        STEP_FETCH_TIMER,
        "ok",
        f"installed+enabled {len(units) // 2} fetch timer(s) "
        f"(scope={options.unit_scope})",
    )


def _build_venvs(
    state_dir: Path,
    options: InstallOptions,
    run: Callable[..., Any],
    dry_run: bool,
) -> StepResult:
    """Step 5: receiver + gateway virtualenvs with the package installed."""
    src = options.package_source or _default_package_source()
    extras = deploy_config.gateway_extras()
    specs = {
        "venv_receiver": f"{src}[gateway]",
        "venv_gateway": f"{src}[{extras}]",
    }
    if dry_run:
        detail = "; ".join(
            f"{name}: pip install {spec}" for name, spec in specs.items()
        )
        return StepResult(STEP_VENVS, "would-do", detail)
    for name, spec in specs.items():
        venv_dir = state_dir / name
        cp = run([sys.executable, "-m", "venv", str(venv_dir)], timeout=300)
        if getattr(cp, "returncode", 1) != 0:
            raise InstallError(f"venv creation failed for {venv_dir}: {cp}")
        pip = venv_dir / "bin" / "pip"
        cp = run([str(pip), "install", spec], timeout=900)
        if getattr(cp, "returncode", 1) != 0:
            err = (getattr(cp, "stderr", "") or "").strip()[-500:]
            raise InstallError(f"pip install {spec} failed: {err or cp}")
    return StepResult(STEP_VENVS, "ok", f"built {len(specs)} venvs from {src}")


def _install_units(
    state_dir: Path,
    registry: deploy_config.DeployRepoRegistry,
    options: InstallOptions,
    manager: ProcessManager,
    dry_run: bool,
) -> StepResult:
    """Step 6: render + install + enable the receiver/gateway units."""
    home = str(Path.home())
    env_file = state_dir / RECEIVER_ENV_RELATIVE
    default_repo = registry.default()
    wanted_by = (
        "default.target" if options.unit_scope == "user" else "multi-user.target"
    )
    user_directive = (
        ""
        if options.unit_scope == "user"
        else f"User={os.environ.get('USER', 'ubuntu')}"
    )
    common = {
        "STATE_DIR": str(state_dir),
        "HOME_DIR": home,
        "ENV_FILE": str(env_file),
        "WANTED_BY": wanted_by,
        "USER_DIRECTIVE": user_directive,
    }
    receiver = render_template(
        "receiver.service.template",
        {
            **common,
            "RECEIVER_PORT": str(options.receiver_port),
            "RECEIVER_VENV": str(state_dir / "venv_receiver"),
            "DEPLOY_SOURCE_REPO": str(default_repo.mirror_dir),
        },
    )
    gateway = render_template(
        "gateway.service.template",
        {
            **common,
            "GATEWAY_PORT": str(options.gateway_port),
            "GATEWAY_VENV": str(state_dir / "venv_gateway"),
        },
    )
    units = [(RECEIVER_UNIT_NAME, receiver), (GATEWAY_UNIT_NAME, gateway)]
    if dry_run:
        names = ", ".join(name for name, _ in units)
        return StepResult(
            STEP_UNITS,
            "would-do",
            f"install+enable via {type(manager).__name__} "
            f"(scope={options.unit_scope}): {names}",
        )
    try:
        for name, content in units:
            manager.install_unit(name, content, scope=options.unit_scope)
            manager.enable(name, scope=options.unit_scope)
    except ProcessManagerError as exc:
        raise InstallError(f"unit install failed: {exc}") from exc
    return StepResult(
        STEP_UNITS,
        "ok",
        f"installed+enabled {RECEIVER_UNIT_NAME}, {GATEWAY_UNIT_NAME} "
        f"(scope={options.unit_scope}; start them explicitly)",
    )


def _generate_secret() -> str:
    """A real secret, never the dev default (fail-closed per #528)."""
    return secrets.token_hex(32)


def _write_secrets(state_dir: Path, dry_run: bool) -> StepResult:
    """Step 7: generated DEPLOY_HMAC_SECRET into the env file (0600)."""
    env_file = state_dir / RECEIVER_ENV_RELATIVE
    if dry_run:
        return StepResult(
            STEP_SECRETS,
            "would-do",
            f"generate DEPLOY_HMAC_SECRET into {env_file} (0600)",
        )
    if env_file.exists():
        raise InstallError(
            f"{env_file} already exists: refusing to overwrite a secret file"
        )
    secret = _generate_secret()
    header = (
        "# Generated by `python -m pe.deploy.install`. Do not commit.\n"
        "# Rotation: put a new value here, update the repo's DEPLOY_HMAC_SECRET\n"
        "# Actions secret(s), then restart prismatic-deploy-receiver.\n"
    )
    # O_EXCL + explicit chmod: no umask race, no clobber.
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(header + f"DEPLOY_HMAC_SECRET={secret}\n")
    except OSError as exc:
        raise InstallError(f"cannot write {env_file}: {exc}") from exc
    os.chmod(env_file, 0o600)
    return StepResult(STEP_SECRETS, "ok", f"wrote {env_file} (0600)")


def _write_auth_config(
    state_dir: Path, options: InstallOptions, dry_run: bool
) -> StepResult:
    """Step 8: persist the install-time auth provider selection.

    Writes ``instance.json`` (0600) with the chosen provider and the
    identity->role mapping (``--admin-identities`` become admins).  With
    ``basic-auth``, also writes ``auth/basic-auth-users.json`` (0600, password
    hashes only -- never plaintext).
    """
    provider = options.auth_provider
    if provider not in deploy_instance.PROVIDER_NAMES:
        raise InstallRefused(
            f"unknown --auth-provider {provider!r} "
            f"(valid: {', '.join(deploy_instance.PROVIDER_NAMES)})"
        )
    if provider == "basic-auth" and not options.basic_auth_users:
        raise InstallRefused(
            "basic-auth needs at least one --basic-auth-user "
            "(password via PRISMATIC_BASIC_AUTH_PASSWORD or prompt)"
        )
    instance_path = deploy_instance.instance_file_path(state_dir)
    admin_list = ", ".join(options.admin_identities) or "(none)"
    if dry_run:
        detail = (
            f"write {instance_path} (0600): provider={provider}, "
            f"admin identities={admin_list}"
        )
        if provider == "basic-auth":
            detail += (
                "; write auth/basic-auth-users.json (0600) for "
                + ", ".join(options.basic_auth_users)
            )
        return StepResult(STEP_AUTH, "would-do", detail)
    if instance_path.exists():
        raise InstallRefused(
            f"{instance_path} already exists: refusing to overwrite "
            "an instance identity"
        )
    config = deploy_instance.new_config(
        auth_provider=provider,
        identity_roles={subject: "admin" for subject in options.admin_identities},
    )
    deploy_instance.save(config, state_dir)
    detail = (
        f"wrote {instance_path} (0600): provider={provider}, "
        f"admin identities={admin_list}"
    )
    if provider == "basic-auth":
        everyone = {username: "" for username in options.basic_auth_users}
        users = {
            username: deploy_instance.resolve_password(username, everyone)
            for username in options.basic_auth_users
        }
        users_path = deploy_instance.write_basic_auth_users(users, state_dir)
        detail += f"; wrote {users_path} (0600, password hashes only)"
    return StepResult(STEP_AUTH, "ok", detail)


# ---------------------------------------------------------------------------
# Enroll-node flow (WS7)
# ---------------------------------------------------------------------------

#: Valid --node-ssh-method values (cf. pe.deploy.nodes.SSH_METHODS).
ENROLL_SSH_METHODS = ("tailscale-ssh", "key")

#: Valid --node-platform values (macOS/Windows deferred per the plan).
ENROLL_PLATFORMS = ("linux-systemd",)


@dataclass
class EnrollNodeOptions:
    """Enroll-node knobs (see --enroll-node --help)."""

    name: str = ""
    address: str = ""
    ssh_user: str = ""
    ssh_method: str = "tailscale-ssh"
    ssh_key: str | None = None
    platform: str = "linux-systemd"
    roles: tuple[str, ...] = ("gateway",)
    confirm: bool = False
    dry_run: bool = False
    nodes_file: str | None = None


@dataclass
class EnrollResult:
    """Outcome of one enroll-node run."""

    ok: bool
    steps: list[StepResult] = field(default_factory=list)
    node_name: str = ""
    registry_path: str = ""


def _mesh_client_or_raise() -> Any:
    """The shared tailscale mesh client; fail-closed when unavailable."""
    try:
        from prismatic.mesh.tailscale import get_tailscale_mesh_client
    except Exception as exc:
        raise InstallError(
            "cannot reach the tailnet mesh client "
            f"(prismatic.mesh.tailscale unavailable: {exc}); is Tailscale "
            "running on this control plane?"
        ) from exc
    return get_tailscale_mesh_client()


def enroll_node(
    options: EnrollNodeOptions,
    *,
    mesh_client: Any | None = None,
    probe: Callable[..., bool] | None = None,
    run_remote: Callable[..., Any] | None = None,
) -> EnrollResult:
    """Verify a tailnet node and register it in the node registry.

    Verification (all fail-closed, nothing written until every check passes):

    1. the address resolves via the mesh client (Tailscale IP or MagicDNS);
    2. the node answers a tailscale ping;
    3. BatchMode ssh answers (no prompts, pinned host key);
    4. the platform probe matches (``uname -s`` == Linux, systemctl present
       for ``linux-systemd``).

    Registration is a LOCAL registry-file write only -- this flow never
    writes to the node. Enrollment requires explicit confirmation
    (``confirm=True`` / ``--node-confirm``); without it the flow refuses
    (exit 2) -- nodes are never auto-enrolled.

    ``mesh_client``, ``probe`` (ssh true-probe), and ``run_remote``
    (arbitrary remote command) are test seams.
    """
    from pe.deploy import nodes as deploy_nodes
    from pe.deploy.node_executor import probe_ssh, run_remote_command

    steps: list[StepResult] = []
    result = EnrollResult(ok=False, node_name=options.name)

    def _step(name: str, status: str, detail: str = "") -> None:
        steps.append(StepResult(name, status, detail))
        result.steps = steps

    # 0. Validate the requested entry up front (cheap, local).
    if options.name == deploy_nodes.LOCAL_NODE_NAME:
        raise InstallError(
            f"invalid node entry: name {deploy_nodes.LOCAL_NODE_NAME!r} is "
            "reserved for the control plane"
        )
    try:
        node = deploy_nodes.DeployNode(
            name=options.name,
            address=options.address,
            ssh_user=options.ssh_user,
            ssh_method=options.ssh_method,
            platform=options.platform,
            roles=tuple(options.roles),
            ssh_key=options.ssh_key,
        )
    except ValueError as exc:
        raise InstallError(f"invalid node entry: {exc}") from exc
    _step("validate", "ok", f"{node.name} -> {node.address} ({node.ssh_method})")

    # 1-2. Mesh resolution + ping.
    client = mesh_client if mesh_client is not None else _mesh_client_or_raise()
    try:
        address = deploy_nodes.resolve_address(node, client)
    except deploy_nodes.UnresolvableNodeError as exc:
        raise InstallError(f"enroll refused: {exc}") from exc
    _step("mesh-resolve", "ok", f"{node.address} -> {address}")
    ping = deploy_nodes.ping_node(node, client)
    if not ping.get("success"):
        raise InstallError(
            f"enroll refused: node {node.name!r} unreachable over the tailnet "
            f"(tailscale ping to {address} failed: "
            f"{ping.get('error', 'no pong')})"
        )
    _step("mesh-ping", "ok", f"pong from {address}")

    # 3. SSH probe (BatchMode: fail-closed, never prompts).
    ssh_probe = probe or probe_ssh
    if not ssh_probe(node, address, 10):
        raise InstallError(
            f"enroll refused: ssh probe failed for {node.ssh_user}@{address} "
            f"(node {node.name!r}) -- check the ssh method/key, tailnet ACLs, "
            "and the pinned host key"
        )
    _step("ssh", "ok", f"{node.ssh_user}@{address} answered")

    # 4. Platform probe (read-only remote commands).
    remote = run_remote or run_remote_command
    try:
        uname = remote(node, address, ["uname", "-s"], timeout=30)
        kernel = (getattr(uname, "stdout", "") or "").strip()
    except Exception as exc:
        raise InstallError(
            f"enroll refused: cannot probe platform on {node.name!r}: {exc}"
        ) from exc
    if kernel != "Linux":
        raise InstallError(
            f"enroll refused: node {node.name!r} reports kernel {kernel!r}; "
            "only linux-systemd nodes are supported"
        )
    if node.platform == "linux-systemd":
        which = remote(node, address, ["command", "-v", "systemctl"], timeout=30)
        if (
            getattr(which, "returncode", 1) != 0
            or not (getattr(which, "stdout", "") or "").strip()
        ):
            raise InstallError(
                f"enroll refused: node {node.name!r} has no systemctl on PATH; "
                "platform linux-systemd requires systemd"
            )
    _step("platform", "ok", f"{kernel} + systemctl ({node.platform})")

    # 5. Register (local file write only). Confirmation is mandatory --
    #    never auto-enroll.
    registry_path = (
        Path(options.nodes_file).expanduser()
        if options.nodes_file
        else deploy_nodes.nodes_file_path()
    )
    if options.dry_run:
        _step(
            "register",
            "would-do",
            f"append {node.name} to {registry_path} (dry-run: not written)",
        )
        result.ok = True
        result.registry_path = str(registry_path)
        return result
    if not options.confirm:
        raise InstallRefused(
            f"refusing to enroll node {node.name!r} without explicit "
            "confirmation: re-run with --node-confirm"
        )
    try:
        written = deploy_nodes.append_node(registry_path, node)
    except deploy_nodes.NodeRegistryError as exc:
        raise InstallError(f"enroll failed: {exc}") from exc
    _step("register", "ok", f"registered {node.name} in {written} (0600)")
    result.ok = True
    result.registry_path = str(written)
    return result


# ---------------------------------------------------------------------------
# Checklist (pure rendering; printed by main)
# ---------------------------------------------------------------------------


def render_checklist(
    registry: deploy_config.DeployRepoRegistry, receiver_port: int
) -> str:
    """Per-repo trigger setup checklist printed at the end of the install."""
    lines = [
        "",
        "Receiver endpoint (HMAC-signed POST): "
        f"http://<this-host>:{receiver_port}/deploy",
        "",
        "Per-repo trigger setup -- repeat for EACH repo below:",
        "  1. Copy .github/workflows/post-merge-deploy.yml into the repo,",
        "     with `repository: ${{ github.repository }}` in the POST payload.",
        "  2. A self-hosted GitHub Actions runner the repo can access.",
        "  3. Actions secrets on the repo:",
        "       DEPLOY_HMAC_SECRET = <value from env.d/deploy-receiver.env>",
        "     (or a per-repo DEPLOY_HMAC_SECRET_<OWNER>_<REPO> for isolation;",
        "      the receiver falls back to the shared secret).",
        "  4. DEPLOY_RECEIVER_HOST secret when the receiver is remote",
        "     (default: http://localhost:9460).",
        "",
        "Repos registered on this receiver:",
    ]
    for repo in registry:
        lines.append(
            f"  - {repo.full_name}: mirror={repo.mirror_dir} "
            f"service={repo.target_service} secret-var={repo.hmac_secret_env}"
        )
    lines += [
        "",
        "Next:",
        f"  - Start the receiver:  systemctl --user start {RECEIVER_UNIT_NAME}",
        "  - Verify everything:   prismatic-engine doctor   (deploy section)",
        "  - The gateway's first release is built by the first real deploy;",
        "    the gateway unit stays down until then (by design).",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_install(
    options: InstallOptions,
    *,
    manager: ProcessManager | None = None,
    run: Callable[..., Any] | None = None,
) -> InstallResult:
    """Run the installer. No prints; raises InstallError / InstallRefused.

    ``manager`` defaults to the platform pick (:func:`_default_manager`);
    tests inject a fake. ``run`` is the subprocess callable (default:
    subprocess.run wrapper); tests inject a recorder or skip network steps.
    """
    if options.unit_scope not in ("user", "system"):
        raise InstallError(
            f"invalid unit scope {options.unit_scope!r}: 'user' or 'system'"
        )
    if options.auth_provider not in deploy_instance.PROVIDER_NAMES:
        raise InstallRefused(
            f"unknown auth provider {options.auth_provider!r} "
            f"(valid: {', '.join(deploy_instance.PROVIDER_NAMES)})"
        )
    if options.auth_provider == "basic-auth" and not options.basic_auth_users:
        raise InstallRefused(
            "basic-auth needs at least one basic_auth_user"
        )
    unknown = set(options.skip_steps) - set(STEP_NAMES)
    if unknown:
        raise InstallError(
            f"unknown --skip-steps value(s): {sorted(unknown)} "
            f"(valid: {', '.join(STEP_NAMES)})"
        )

    if manager is None:
        manager = _default_manager()
    run = run or _default_run

    try:
        registry = deploy_config.load_repo_registry()
    except Exception as exc:
        raise InstallError(f"cannot load deploy repo registry: {exc}") from exc

    # config.state_dir() honors PRISMATIC_STATE_DIR; otherwise ~/.prismatic.
    # (--home sets HOME for this process in main(), so this agrees.)
    state_dir = deploy_config.state_dir()
    result = InstallResult(ok=True, state_dir=str(state_dir))
    steps: list[StepResult] = []

    def _do(name: str, fn: Callable[[], StepResult]) -> None:
        if name in options.skip_steps:
            steps.append(StepResult(name, "skipped", "via --skip-steps"))
            return
        try:
            steps.append(fn())
        except InstallRefused:
            raise
        except InstallError as exc:
            steps.append(StepResult(name, "failed", str(exc)))
            result.ok = False
            result.steps = steps
            raise

    try:
        _do(
            STEP_PREREQUISITES,
            lambda: _check_prerequisites(options, manager, state_dir),
        )
        _do(STEP_LAYOUT, lambda: _create_layout(state_dir, options.dry_run))
        _do(
            STEP_MIRROR,
            lambda: _clone_mirrors(registry, options, run, options.dry_run),
        )
        _do(
            STEP_FETCH_TIMER,
            lambda: _install_fetch_timer(registry, options, manager, options.dry_run),
        )
        _do(
            STEP_VENVS,
            lambda: _build_venvs(state_dir, options, run, options.dry_run),
        )
        _do(
            STEP_UNITS,
            lambda: _install_units(
                state_dir, registry, options, manager, options.dry_run
            ),
        )
        _do(STEP_SECRETS, lambda: _write_secrets(state_dir, options.dry_run))
        _do(
            STEP_AUTH,
            lambda: _write_auth_config(state_dir, options, options.dry_run),
        )
    except InstallRefused:
        result.ok = False
        result.steps = steps
        raise
    except InstallError:
        # _do already recorded the failed step; stop at the first failure.
        raise

    result.steps = steps
    return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m pe.deploy.install",
        description="Install Prismatic's post-merge deploy plane on this machine.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen without changing anything "
        "(prerequisites still really run)",
    )
    parser.add_argument(
        "--home",
        default=None,
        help="install under this home dir (sets HOME for this process)",
    )
    parser.add_argument(
        "--skip-steps",
        default="",
        help="comma-separated steps to skip: " + ",".join(STEP_NAMES),
    )
    parser.add_argument(
        "--package-source",
        default=None,
        help="pip spec installed into the venvs (default: enclosing "
        "checkout, else the default repo's GitHub URL)",
    )
    parser.add_argument(
        "--repo-url",
        default=None,
        help="git URL cloned for the default repo's mirror "
        "(default: https://github.com/<owner>/<repo>.git)",
    )
    parser.add_argument(
        "--unit-scope",
        choices=("user", "system"),
        default="user",
        help="systemd unit scope (default: user, like today's receiver)",
    )
    parser.add_argument(
        "--receiver-port",
        type=int,
        default=DEFAULT_RECEIVER_PORT,
        help="receiver port (default: 9460)",
    )
    parser.add_argument(
        "--gateway-port",
        type=int,
        default=DEFAULT_GATEWAY_PORT,
        help="gateway port (default: 9000)",
    )
    parser.add_argument(
        "--auth-provider",
        choices=deploy_instance.PROVIDER_NAMES,
        default=deploy_instance.DEFAULT_AUTH_PROVIDER,
        help="portal auth provider persisted to instance.json "
        "(default: cloudflare-access, the historical posture)",
    )
    parser.add_argument(
        "--admin-identities",
        default="",
        help="comma-separated identity subjects mapped to the admin portal "
        "role (e.g. your Cloudflare Access email); written to instance.json",
    )
    parser.add_argument(
        "--basic-auth-user",
        action="append",
        default=[],
        dest="basic_auth_user",
        help="basic-auth username (repeatable; requires "
        "--auth-provider basic-auth; password via "
        "PRISMATIC_BASIC_AUTH_PASSWORD or an interactive prompt)",
    )

    # WS7: enroll a tailnet node into the deploy registry. When set, the
    # installer skips the install steps and runs the enroll-node flow
    # instead (verify via mesh/ssh, register locally -- never auto-enrolled
    # without --node-confirm).
    enroll = parser.add_argument_group("enroll a tailnet node (--enroll-node)")
    enroll.add_argument(
        "--enroll-node",
        action="store_true",
        help="enroll a tailnet node as a deploy target instead of installing",
    )
    enroll.add_argument("--node-name", default="", help="registry name for the node")
    enroll.add_argument(
        "--node-address",
        default="",
        help="tailnet address: Tailscale IP or MagicDNS name",
    )
    enroll.add_argument("--node-ssh-user", default="", help="ssh user on the node")
    enroll.add_argument(
        "--node-ssh-method",
        choices=ENROLL_SSH_METHODS,
        default="tailscale-ssh",
        help="tailscale-ssh (default) or key",
    )
    enroll.add_argument(
        "--node-ssh-key",
        default=None,
        help="private key file (required with --node-ssh-method=key)",
    )
    enroll.add_argument(
        "--node-platform",
        choices=ENROLL_PLATFORMS,
        default="linux-systemd",
        help="node platform (default: linux-systemd)",
    )
    enroll.add_argument(
        "--node-roles",
        default="gateway",
        help="comma-separated node roles (default: gateway)",
    )
    enroll.add_argument(
        "--node-confirm",
        action="store_true",
        help="explicit confirmation to register the node (required)",
    )
    enroll.add_argument(
        "--nodes-file",
        default=None,
        help="node registry path (default: $PRISMATIC_NODES_FILE or "
        "~/.prismatic/nodes.yaml)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _parse_args(argv)
    if args.home:
        # Set HOME for this process so every config.py path default
        # (state dir, registry mirror dirs, unit dirs) agrees.
        os.environ["HOME"] = str(Path(args.home).expanduser().resolve())

    # WS7: enroll-node flow (replaces the install steps for this run).
    if args.enroll_node:
        return _main_enroll_node(args)

    options = InstallOptions(
        dry_run=args.dry_run,
        skip_steps=tuple(s.strip() for s in args.skip_steps.split(",") if s.strip()),
        package_source=args.package_source,
        repo_url=args.repo_url,
        unit_scope=args.unit_scope,
        receiver_port=args.receiver_port,
        gateway_port=args.gateway_port,
        auth_provider=args.auth_provider,
        admin_identities=tuple(
            s.strip() for s in args.admin_identities.split(",") if s.strip()
        ),
        basic_auth_users=tuple(args.basic_auth_user or ()),
    )
    try:
        result = run_install(options)
    except InstallRefused as exc:
        print(f"install refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except InstallError as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return EXIT_FAILED

    for step in result.steps:
        mark = {"ok": "ok", "skipped": "skipped", "would-do": "would-do"}.get(
            step.status, step.status
        )
        print(f"[{mark}] {step.name}: {step.detail}")

    try:
        registry = deploy_config.load_repo_registry()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"warning: cannot render checklist: {exc}", file=sys.stderr)
        return EXIT_OK if result.ok else EXIT_FAILED
    print(render_checklist(registry, options.receiver_port))
    return EXIT_OK if result.ok else EXIT_FAILED


def _main_enroll_node(args: argparse.Namespace) -> int:
    """CLI driver for the WS7 enroll-node flow. Returns an exit code."""
    from pe.deploy import nodes as deploy_nodes

    missing = [
        flag
        for flag, value in (
            ("--node-name", args.node_name),
            ("--node-address", args.node_address),
            ("--node-ssh-user", args.node_ssh_user),
        )
        if not value
    ]
    if missing:
        print(
            f"enroll-node needs {', '.join(missing)} (see --help)",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    options = EnrollNodeOptions(
        name=args.node_name,
        address=args.node_address,
        ssh_user=args.node_ssh_user,
        ssh_method=args.node_ssh_method,
        ssh_key=args.node_ssh_key,
        platform=args.node_platform,
        roles=tuple(r.strip() for r in args.node_roles.split(",") if r.strip()),
        confirm=args.node_confirm,
        dry_run=args.dry_run,
        nodes_file=args.nodes_file,
    )
    try:
        result = enroll_node(options)
    except InstallRefused as exc:
        print(f"enroll-node refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except InstallError as exc:
        print(f"enroll-node failed: {exc}", file=sys.stderr)
        return EXIT_FAILED
    for step in result.steps:
        print(f"[{step.status}] {step.name}: {step.detail}")
    registry = (
        Path(options.nodes_file).expanduser()
        if options.nodes_file
        else deploy_nodes.nodes_file_path()
    )
    print(f"node registry: {registry}")
    return EXIT_OK if result.ok else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
